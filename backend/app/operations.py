import csv, difflib, io, os, re, uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session
from .config import settings
from .database import get_db
from .gemini_provider import GeminiProvider
from .models import Alert, AuditLog, BusinessUser, Category, Customer, Expense, ImageDocument, InventoryMovement, LedgerEntry, Product, ProductAlias, ProductUnitMap, Purchase, PurchaseLine, Sale, SaleLine, Store, Subscription, Supplier, User
from .security import current_user

router = APIRouter(prefix="/api")
def now(): return datetime.now(timezone.utc)
def member(db: Session, user: User, business_id: str, roles: set[str] | None=None):
    m=db.scalar(select(BusinessUser).where(BusinessUser.business_id==business_id,BusinessUser.user_id==user.id,BusinessUser.active.is_(True)))
    if not m: raise HTTPException(404,"Business not found")
    if roles and m.role.value not in roles: raise HTTPException(403,"Permission denied")
    return m
def audit(db,bid,user,action,entity,rid): db.add(AuditLog(business_id=bid,user_id=user.id,action=action,entity=entity,record_id=rid))

class MasterIn(BaseModel): name:str=Field(min_length=2,max_length=180); mobile:str|None=None
class ProductIn(BaseModel): store_id:str; code:str; name:str; category_id:str|None=None; supplier_id:str|None=None; local_name:str|None=None; barcode:str|None=None; base_unit:str="piece"; purchase_unit:str="piece"; selling_unit:str="piece"; conversion_factor:float=Field(1,gt=0); mrp:float=Field(0,ge=0); selling_price:float=Field(0,ge=0); purchase_cost:float=Field(0,ge=0); reorder_level:float=Field(0,ge=0); aliases:list[str]=[]
class ResolveProductIn(BaseModel): store_id:str; name:str=Field(min_length=2,max_length=180); unit:str|None=None; mrp:float=Field(0,ge=0); price:float=Field(0,ge=0)
class MatchProductIn(BaseModel): name:str=Field(min_length=2,max_length=180)
class ProductEditIn(BaseModel): name:str=Field(min_length=2,max_length=180); selling_unit:str; mrp:float=Field(ge=0); selling_price:float=Field(ge=0)
class StockIn(BaseModel): store_id:str; product_id:str; quantity:float=Field(gt=0); movement_type:str="opening_stock"; unit_cost:float=Field(0,ge=0); notes:str|None=None
class LineIn(BaseModel): product_id:str; quantity:float=Field(gt=0); unit_price:float=Field(ge=0)
class SaleIn(BaseModel): store_id:str; invoice_number:str; payment_mode:str; transaction_date:date|None=None; customer_id:str|None=None; discount:float=Field(0,ge=0); lines:list[LineIn]=Field(min_length=1)
class PurchaseIn(BaseModel): store_id:str; supplier_id:str; invoice_number:str; transaction_date:date|None=None; payment_mode:str="cash"; paid:float=Field(0,ge=0); lines:list[LineIn]=Field(min_length=1)
class PaymentIn(BaseModel): party_type:str; party_id:str; amount:float=Field(gt=0); entry_type:str="payment"
class ExpenseIn(BaseModel): store_id:str; category:str; amount:float=Field(gt=0); payment_method:str; payee:str|None=None
class NaturalEntryIn(BaseModel): entry_type:str; text:str=Field(min_length=2,max_length=5000)
class TransactionLineEditIn(BaseModel): quantity:float=Field(gt=0); total_price:float=Field(ge=0); payment_mode:str|None=None; customer_id:str|None=None
class VendorEditIn(BaseModel): name:str=Field(min_length=2,max_length=180); mobile:str|None=None
class InventoryEditIn(BaseModel): closing_stock:float=Field(ge=0); notes:str|None=None

PAYMENT_MODES={"cash","upi","customer_credit"}
def tenant_store(db:Session,business_id:str,store_id:str)->Store:
    row=db.scalar(select(Store).where(Store.id==store_id,Store.business_id==business_id))
    if not row:raise HTTPException(400,"This store was not found for your business.")
    return row
def available_stock(db:Session,business_id:str,product_id:str)->float:
    return float(db.scalar(select(func.coalesce(func.sum(InventoryMovement.quantity_base),0)).where(InventoryMovement.business_id==business_id,InventoryMovement.product_id==product_id)) or 0)
def entry_datetime(value:date|None)->datetime:
    chosen=value or now().date()
    if chosen>now().date():raise HTTPException(422,"Transaction date cannot be in the future.")
    return datetime.combine(chosen,time(hour=12),tzinfo=timezone.utc)

@router.post("/businesses/{business_id}/ai/parse-text")
async def parse_natural_entry(business_id:str,body:NaturalEntryIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id)
    if body.entry_type not in {"sales","stock","products","udhaar"}: raise HTTPException(422,"Unsupported entry type")
    try: return await GeminiProvider().parse_text(body.text,body.entry_type)
    except Exception as exc: raise HTTPException(502,f"AI mapping failed: {type(exc).__name__}") from exc

@router.get("/businesses/{business_id}/stores")
def stores(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)): member(db,user,business_id); return db.scalars(select(Store).where(Store.business_id==business_id)).all()

@router.get("/businesses/{business_id}/categories")
def categories(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)): member(db,user,business_id); return db.scalars(select(Category).where(Category.business_id==business_id,Category.active.is_(True))).all()
@router.post("/businesses/{business_id}/categories",status_code=201)
def add_category(business_id:str,body:MasterIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"}); row=Category(business_id=business_id,name=body.name); db.add(row); db.flush(); audit(db,business_id,user,"create","category",row.id); db.commit(); return row
@router.get("/businesses/{business_id}/suppliers")
def suppliers(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)): member(db,user,business_id); return db.scalars(select(Supplier).where(Supplier.business_id==business_id,Supplier.active.is_(True))).all()
@router.post("/businesses/{business_id}/suppliers",status_code=201)
def add_supplier(business_id:str,body:MasterIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"}); row=Supplier(business_id=business_id,name=body.name,mobile=body.mobile); db.add(row); db.flush(); audit(db,business_id,user,"create","supplier",row.id); db.commit(); return row
@router.post("/businesses/{business_id}/suppliers/resolve")
def resolve_supplier(business_id:str,body:MasterIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"}); name=body.name.strip(); row=db.scalar(select(Supplier).where(Supplier.business_id==business_id,func.lower(Supplier.name)==name.lower()))
    if row:return {"supplier":row,"created":False}
    row=Supplier(business_id=business_id,name=name,mobile=body.mobile);db.add(row);db.flush();audit(db,business_id,user,"auto_create","supplier",row.id);db.commit();return {"supplier":row,"created":True}
@router.patch("/businesses/{business_id}/suppliers/{supplier_id}")
def edit_supplier(business_id:str,supplier_id:str,body:VendorEditIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"}); row=db.scalar(select(Supplier).where(Supplier.id==supplier_id,Supplier.business_id==business_id))
    if not row:raise HTTPException(404,"Vendor not found")
    row.name=body.name.strip();row.mobile=body.mobile;audit(db,business_id,user,"update","supplier",row.id);db.commit();return row
@router.get("/businesses/{business_id}/customers")
def customers(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)): member(db,user,business_id); return db.scalars(select(Customer).where(Customer.business_id==business_id,Customer.active.is_(True))).all()
@router.post("/businesses/{business_id}/customers",status_code=201)
def add_customer(business_id:str,body:MasterIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id); row=Customer(business_id=business_id,name=body.name,mobile=body.mobile); db.add(row); db.flush(); audit(db,business_id,user,"create","customer",row.id); db.commit(); return row
@router.post("/businesses/{business_id}/customers/resolve")
def resolve_customer(business_id:str,body:MasterIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id); row=db.scalar(select(Customer).where(Customer.business_id==business_id,func.lower(Customer.name)==body.name.strip().lower()))
    if row:return {"customer":row,"created":False}
    row=Customer(business_id=business_id,name=body.name.strip(),mobile=body.mobile);db.add(row);db.flush();audit(db,business_id,user,"auto_create","customer",row.id);db.commit();return {"customer":row,"created":True}
@router.get("/businesses/{business_id}/products")
def products(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)): member(db,user,business_id); return db.scalars(select(Product).where(Product.business_id==business_id,Product.active.is_(True))).all()
@router.post("/businesses/{business_id}/products",status_code=201)
def add_product(business_id:str,body:ProductIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"});
    if not db.scalar(select(Store).where(Store.id==body.store_id,Store.business_id==business_id)): raise HTTPException(400,"Invalid store")
    row=Product(business_id=business_id,store_id=body.store_id,code=body.code,name=body.name,category_id=body.category_id,preferred_supplier_id=body.supplier_id,local_name=body.local_name,barcode=body.barcode,base_unit=body.base_unit,purchase_unit=body.purchase_unit,selling_unit=body.selling_unit,conversion_factor=body.conversion_factor,mrp=body.mrp,selling_price=body.selling_price,purchase_cost=body.purchase_cost,reorder_level=body.reorder_level); db.add(row); db.flush()
    db.add_all([ProductAlias(business_id=business_id,product_id=row.id,alias=x) for x in body.aliases]); audit(db,business_id,user,"create","product",row.id); db.commit(); return row

def fallback_unit(label:str, supplied:str|None=None) -> str:
    lower=label.lower(); unit=(supplied or "").lower()
    if unit:return unit
    if re.search(r"\b\d+(\.\d+)?\s*(ml|l|litre|liter)\b",lower) or any(x in lower for x in ["cola","juice","milk","oil","drink"]): return "litre"
    if re.search(r"\b\d+(\.\d+)?\s*(kg|g|gram)\b",lower) or any(x in lower for x in ["rice","atta","dal","sugar","salt"]): return "kilogram"
    if any(x in lower for x in ["biscuit","parle","namkeen","noodle","soap","packet","pack"]): return "packet"
    return "piece"

@router.get("/businesses/{business_id}/product-unit-map")
def product_unit_map(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id)
    return db.scalars(select(ProductUnitMap).order_by(ProductUnitMap.display_name)).all()

def normalized_product_name(value:str)->str:
    return re.sub(r"[^a-z0-9\u0900-\u097f]+","",value.casefold())

@router.post("/businesses/{business_id}/products/match")
async def match_product(business_id:str,body:MatchProductIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id); entered=body.name.strip(); normalized=normalized_product_name(entered)
    products=db.scalars(select(Product).where(Product.business_id==business_id,Product.active.is_(True))).all()
    aliases=db.execute(select(ProductAlias,Product).join(Product,Product.id==ProductAlias.product_id).where(ProductAlias.business_id==business_id,Product.active.is_(True))).all()
    for product in products:
        if normalized_product_name(product.name)==normalized or (product.local_name and normalized_product_name(product.local_name)==normalized):
            return {"product":product,"matched_by":"exact","confidence":1}
    for alias,product in aliases:
        if normalized_product_name(alias.alias)==normalized:return {"product":product,"matched_by":"alias","confidence":1}
    scored=[]
    for product in products:
        score=difflib.SequenceMatcher(None,normalized,normalized_product_name(product.name)).ratio()
        scored.append((score,product))
    shortlisted=[p for _,p in sorted(scored,key=lambda x:x[0],reverse=True)[:12]]
    if not shortlisted:raise HTTPException(404,"No products are available. Add the product through Purchases first.")
    try:
        decision=await GeminiProvider().match_existing_product(entered,[{"id":p.id,"name":p.name,"unit":p.selling_unit,"pack":p.local_name or ""} for p in shortlisted])
    except Exception:
        best_score,best=max(scored,key=lambda x:x[0])
        if best_score>=0.82:return {"product":best,"matched_by":"spelling","confidence":best_score}
        raise HTTPException(409,f"'{entered}' could not be matched safely. Choose an existing product name.")
    product=next((p for p in shortlisted if p.id==decision["match_id"]),None)
    if not product:raise HTTPException(409,f"'{entered}' could not be matched safely. Choose an existing product name.")
    return {"product":product,"matched_by":"ai_spelling","confidence":decision["confidence"],"reason":decision.get("reason","")}

@router.post("/businesses/{business_id}/products/resolve")
async def resolve_product(business_id:str,body:ResolveProductIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id); label=body.name.strip(); product=db.scalar(select(Product).where(Product.business_id==business_id,func.lower(Product.name)==label.lower()))
    if not product:
        alias=db.scalar(select(ProductAlias).where(ProductAlias.business_id==business_id,func.lower(ProductAlias.alias)==label.lower())); product=db.get(Product,alias.product_id) if alias else None
    if product:
        if not product.active:product.active=True;audit(db,business_id,user,"reactivate_from_purchase","product",product.id);db.commit();db.refresh(product)
        return {"product":product,"created":False,"reactivated":True if product.active else False}
    normalized=re.sub(r"\s+"," ",label.lower()).strip(); knowledge=db.scalar(select(ProductUnitMap).where(ProductUnitMap.normalized_name==normalized))
    if not knowledge:
        try:
            result=await GeminiProvider().research_indian_kirana_unit(label); unit=result["selling_unit"]; acceptable=result.get("acceptable_units") or [unit]
            if "milk" in normalized: acceptable=list(dict.fromkeys([*acceptable,"litre","kilogram"]))
            knowledge=ProductUnitMap(normalized_name=normalized,display_name=label,selling_unit=unit,acceptable_units=acceptable,reasoning=result.get("reasoning"),sources=result.get("sources",[]),lookup_status="researched")
        except Exception as exc:
            unit=fallback_unit(label,body.unit); acceptable=[unit]
            if "milk" in normalized: acceptable=list(dict.fromkeys([*acceptable,"litre","kilogram"]))
            knowledge=ProductUnitMap(normalized_name=normalized,display_name=label,selling_unit=unit,acceptable_units=acceptable,reasoning="Local fallback used because web research was unavailable.",sources=[],lookup_status="fallback")
        db.add(knowledge); db.flush()
    unit=(body.unit or knowledge.selling_unit).lower()
    code=f"AUTO-{uuid.uuid4().hex[:8].upper()}"; price=body.price or body.mrp
    product=Product(business_id=business_id,store_id=body.store_id,code=code,name=label,base_unit=unit,purchase_unit=unit,selling_unit=unit,conversion_factor=1,mrp=body.mrp or price,selling_price=price,purchase_cost=0,reorder_level=5); db.add(product); db.flush(); audit(db,business_id,user,"auto_create","product",product.id); db.commit(); return {"product":product,"created":True,"unit_knowledge":{"selling_unit":knowledge.selling_unit,"acceptable_units":knowledge.acceptable_units,"lookup_status":knowledge.lookup_status,"reasoning":knowledge.reasoning}}

@router.patch("/businesses/{business_id}/products/{product_id}")
def edit_product(business_id:str,product_id:str,body:ProductEditIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"}); product=db.scalar(select(Product).where(Product.id==product_id,Product.business_id==business_id));
    if not product: raise HTTPException(404,"Product not found")
    product.name=body.name; product.selling_unit=body.selling_unit; product.base_unit=body.selling_unit; product.mrp=body.mrp; product.selling_price=body.selling_price; audit(db,business_id,user,"update","product",product.id); db.commit(); return product

@router.post("/businesses/{business_id}/inventory/movements",status_code=201)
def stock(business_id:str,body:StockIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id); tenant_store(db,business_id,body.store_id); product=db.scalar(select(Product).where(Product.id==body.product_id,Product.business_id==business_id,Product.active.is_(True)));
    if not product: raise HTTPException(400,"Invalid product")
    sign=-1 if body.movement_type in {"sale","purchase_return","damage","expiry","adjustment_decrease","personal_use"} else 1
    row=InventoryMovement(business_id=business_id,store_id=body.store_id,product_id=body.product_id,movement_type=body.movement_type,quantity_base=body.quantity*sign,unit_cost=body.unit_cost,created_by=user.id,notes=body.notes); db.add(row); db.flush(); audit(db,business_id,user,"post","inventory_movement",row.id); db.commit(); return row
@router.get("/businesses/{business_id}/inventory")
def inventory(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id); today=now().date().isoformat(); movement_day=func.date(InventoryMovement.transaction_date)
    rows=db.execute(select(Product.id,Product.name,Product.base_unit,Product.reorder_level,Product.purchase_cost,
        func.coalesce(func.sum(case((((movement_day<today)|(InventoryMovement.movement_type=="opening_stock")),InventoryMovement.quantity_base),else_=0)),0).label("opening"),
        func.coalesce(func.sum(case((((movement_day==today)&(InventoryMovement.quantity_base>0)&(InventoryMovement.movement_type!="opening_stock")),InventoryMovement.quantity_base),else_=0)),0).label("added"),
        func.coalesce(func.sum(case(((movement_day==today)&(InventoryMovement.quantity_base<0),-InventoryMovement.quantity_base),else_=0)),0).label("sold"),
        func.coalesce(func.sum(InventoryMovement.quantity_base),0).label("stock")
    ).outerjoin(InventoryMovement,(InventoryMovement.product_id==Product.id)&(InventoryMovement.business_id==business_id)).where(Product.business_id==business_id,Product.active.is_(True)).group_by(Product.id)).all()
    return [{"product_id":r.id,"name":r.name,"unit":r.base_unit,"opening":r.opening,"added_today":r.added,"sold_today":r.sold,"stock":r.stock,"closing":r.opening+r.added-r.sold,"reorder_level":r.reorder_level,"value":r.stock*r.purchase_cost,"status":"out_of_stock" if r.stock<=0 else "low" if r.stock<=r.reorder_level else "healthy"} for r in rows]
@router.patch("/businesses/{business_id}/inventory/{product_id}")
def edit_inventory(business_id:str,product_id:str,body:InventoryEditIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"});product=db.scalar(select(Product).where(Product.id==product_id,Product.business_id==business_id,Product.active.is_(True)))
    if not product:raise HTTPException(404,"Inventory product not found")
    current=db.scalar(select(func.coalesce(func.sum(InventoryMovement.quantity_base),0)).where(InventoryMovement.business_id==business_id,InventoryMovement.product_id==product_id));delta=body.closing_stock-current
    if delta:db.add(InventoryMovement(business_id=business_id,store_id=product.store_id,product_id=product.id,movement_type="adjustment_increase" if delta>0 else "adjustment_decrease",quantity_base=delta,unit_cost=product.purchase_cost,reference_type="inventory_edit",created_by=user.id,notes=body.notes or f"Closing stock corrected from {current} to {body.closing_stock}"))
    audit(db,business_id,user,"update","inventory",product.id);db.commit();return {"product_id":product.id,"previous_stock":current,"closing_stock":body.closing_stock}
@router.delete("/businesses/{business_id}/inventory/{product_id}")
def delete_inventory(business_id:str,product_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"});product=db.scalar(select(Product).where(Product.id==product_id,Product.business_id==business_id,Product.active.is_(True)))
    if not product:raise HTTPException(404,"Inventory product not found")
    product.active=False;audit(db,business_id,user,"archive","inventory",product.id);db.commit();return {"deleted":True,"product_id":product.id}

@router.post("/businesses/{business_id}/sales",status_code=201)
def post_sale(business_id:str,body:SaleIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id); tenant_store(db,business_id,body.store_id)
    posted_at=entry_datetime(body.transaction_date)
    if body.payment_mode not in PAYMENT_MODES:raise HTTPException(422,"Choose Cash, UPI or Udhaar as the payment method.")
    if db.scalar(select(Sale.id).where(Sale.business_id==business_id,Sale.invoice_number==body.invoice_number)):raise HTTPException(409,"This sale was already saved. No duplicate entry was created.")
    products={p.id:p for p in db.scalars(select(Product).where(Product.business_id==business_id,Product.active.is_(True),Product.id.in_([x.product_id for x in body.lines])))}
    if len(products)!=len({x.product_id for x in body.lines}): raise HTTPException(400,"Invalid product")
    requested={pid:sum(x.quantity for x in body.lines if x.product_id==pid) for pid in products}
    for pid,quantity in requested.items():
        stock_left=available_stock(db,business_id,pid)
        if quantity>stock_left:raise HTTPException(409,f"Only {stock_left:g} {products[pid].base_unit} of {products[pid].name} is available. Reduce the quantity or add a purchase first.")
    if body.customer_id and not db.scalar(select(Customer.id).where(Customer.id==body.customer_id,Customer.business_id==business_id,Customer.active.is_(True))):raise HTTPException(400,"This customer was not found for your business.")
    gross=sum(x.quantity*x.unit_price for x in body.lines); sale=Sale(business_id=business_id,store_id=body.store_id,invoice_number=body.invoice_number,customer_id=body.customer_id,payment_mode=body.payment_mode,gross=gross,discount=body.discount,net=gross-body.discount,created_by=user.id,created_at=posted_at); db.add(sale); db.flush()
    for x in body.lines:
        p=products[x.product_id]; db.add(SaleLine(business_id=business_id,sale_id=sale.id,product_id=p.id,quantity=x.quantity,unit_price=x.unit_price,unit_cost=p.purchase_cost,net=x.quantity*x.unit_price)); db.add(InventoryMovement(business_id=business_id,store_id=body.store_id,product_id=p.id,movement_type="sale",quantity_base=-x.quantity,unit_cost=p.purchase_cost,reference_type="sale",reference_id=sale.id,created_by=user.id,transaction_date=posted_at))
    if body.payment_mode=="customer_credit" and body.customer_id:
        db.add(LedgerEntry(business_id=business_id,party_type="customer",party_id=body.customer_id,entry_type="credit_sale",amount=sale.net,reference_id=sale.id,created_by=user.id,created_at=posted_at))
    audit(db,business_id,user,"post","sale",sale.id); db.commit(); return sale

@router.post("/businesses/{business_id}/purchases",status_code=201)
def post_purchase(business_id:str,body:PurchaseIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"}); tenant_store(db,business_id,body.store_id)
    posted_at=entry_datetime(body.transaction_date)
    if not db.scalar(select(Supplier.id).where(Supplier.id==body.supplier_id,Supplier.business_id==business_id,Supplier.active.is_(True))):raise HTTPException(400,"This vendor was not found for your business.")
    if db.scalar(select(Purchase.id).where(Purchase.business_id==business_id,Purchase.supplier_id==body.supplier_id,Purchase.invoice_number==body.invoice_number)):raise HTTPException(409,"This vendor bill was already saved. No duplicate purchase was created.")
    products={p.id:p for p in db.scalars(select(Product).where(Product.business_id==business_id,Product.active.is_(True),Product.id.in_([x.product_id for x in body.lines])))}
    if len(products)!=len({x.product_id for x in body.lines}): raise HTTPException(400,"Invalid product")
    if body.payment_mode not in {"cash","upi","supplier_credit"}:raise HTTPException(422,"Choose Cash, UPI or Udhaar as the payment method.")
    total=sum(x.quantity*x.unit_price for x in body.lines); paid=0 if body.payment_mode=="supplier_credit" else total
    purchase=Purchase(business_id=business_id,store_id=body.store_id,supplier_id=body.supplier_id,invoice_number=body.invoice_number,total=total,paid=paid,payment_mode=body.payment_mode,created_by=user.id,created_at=posted_at); db.add(purchase); db.flush()
    for x in body.lines:
        product=products[x.product_id]; product.purchase_cost=x.unit_price
        db.add(PurchaseLine(business_id=business_id,purchase_id=purchase.id,product_id=x.product_id,quantity=x.quantity,unit_cost=x.unit_price,total=x.quantity*x.unit_price)); db.add(InventoryMovement(business_id=business_id,store_id=body.store_id,product_id=x.product_id,movement_type="purchase_receipt",quantity_base=x.quantity,unit_cost=x.unit_price,reference_type="purchase",reference_id=purchase.id,created_by=user.id,transaction_date=posted_at))
    if total>paid: db.add(LedgerEntry(business_id=business_id,party_type="supplier",party_id=body.supplier_id,entry_type="purchase_credit",amount=total-paid,reference_id=purchase.id,created_by=user.id,created_at=posted_at))
    audit(db,business_id,user,"post","purchase",purchase.id); db.commit(); return purchase

@router.post("/businesses/{business_id}/payments",status_code=201)
def payment(business_id:str,body:PaymentIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id)
    if body.party_type not in {"customer","supplier"}:raise HTTPException(422,"Choose customer or supplier.")
    model=Customer if body.party_type=="customer" else Supplier
    if not db.scalar(select(model.id).where(model.id==body.party_id,model.business_id==business_id,model.active.is_(True))):raise HTTPException(400,"This account was not found for your business.")
    allowed={"credit_sale","purchase_credit","opening_balance","payment_received","payment"}
    if body.entry_type not in allowed:raise HTTPException(422,"Choose a valid udhaar entry type.")
    signed=body.amount if body.entry_type in {"credit_sale","purchase_credit","opening_balance"} else -body.amount; row=LedgerEntry(business_id=business_id,party_type=body.party_type,party_id=body.party_id,entry_type=body.entry_type,amount=signed,created_by=user.id); db.add(row); db.flush(); audit(db,business_id,user,"post","ledger_entry",row.id); db.commit(); return row
@router.get("/businesses/{business_id}/ledger")
def ledger(business_id:str,party_type:str="customer",user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id); rows=db.execute(select(LedgerEntry.party_id,func.sum(LedgerEntry.amount).label("balance"),func.max(LedgerEntry.created_at).label("last_activity")).where(LedgerEntry.business_id==business_id,LedgerEntry.party_type==party_type).group_by(LedgerEntry.party_id)).all(); names={x.id:x.name for x in db.scalars(select(Customer if party_type=="customer" else Supplier).where((Customer if party_type=="customer" else Supplier).business_id==business_id))}; return [{"party_id":r.party_id,"name":names.get(r.party_id,"Unknown"),"balance":r.balance,"last_activity":r.last_activity} for r in rows]
@router.get("/businesses/{business_id}/sales")
def sales_list(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)): member(db,user,business_id); return db.scalars(select(Sale).where(Sale.business_id==business_id).order_by(Sale.created_at.desc()).limit(100)).all()
@router.get("/businesses/{business_id}/sales-details")
def sales_details(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id); rows=db.execute(select(Sale,SaleLine,Product).join(SaleLine,SaleLine.sale_id==Sale.id).join(Product,Product.id==SaleLine.product_id).where(Sale.business_id==business_id).order_by(Sale.created_at.desc()).limit(300)).all()
    return [{"sale_id":s.id,"line_id":line.id,"date":s.created_at,"invoice_number":s.invoice_number,"payment_mode":s.payment_mode,"customer_id":s.customer_id,"product_name":p.name,"quantity":line.quantity,"total_price":line.net,"unit_price":line.unit_price,"bought_price_per_unit":line.unit_cost,"profit_per_unit":line.unit_price-line.unit_cost,"profit_loss":line.net-line.quantity*line.unit_cost} for s,line,p in rows]
@router.patch("/businesses/{business_id}/sales/{sale_id}/lines/{line_id}")
def edit_sale_line(business_id:str,sale_id:str,line_id:str,body:TransactionLineEditIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"}); sale=db.scalar(select(Sale).where(Sale.id==sale_id,Sale.business_id==business_id)); line=db.scalar(select(SaleLine).where(SaleLine.id==line_id,SaleLine.sale_id==sale_id,SaleLine.business_id==business_id))
    if not sale or not line:raise HTTPException(404,"Sale row not found")
    if body.payment_mode:
        if body.payment_mode not in PAYMENT_MODES:raise HTTPException(422,"Choose Cash, UPI or Udhaar.")
        line_count=db.scalar(select(func.count(SaleLine.id)).where(SaleLine.sale_id==sale.id))
        if line_count>1 and body.payment_mode!=sale.payment_mode:
            split_sale=Sale(business_id=business_id,store_id=sale.store_id,invoice_number=f"{sale.invoice_number[:25]}-{line.id[:6]}",customer_id=None,payment_mode=body.payment_mode,gross=line.net,discount=0,net=line.net,created_by=user.id,created_at=sale.created_at)
            db.add(split_sale);db.flush();line.sale_id=split_sale.id
            movement=db.scalar(select(InventoryMovement).where(InventoryMovement.business_id==business_id,InventoryMovement.reference_id==sale.id,InventoryMovement.product_id==line.product_id,InventoryMovement.movement_type=="sale"))
            if movement:movement.reference_id=split_sale.id
            original_total=db.scalar(select(func.coalesce(func.sum(SaleLine.net),0)).where(SaleLine.sale_id==sale.id));sale.gross=original_total;sale.net=max(0,original_total-sale.discount)
            original_credit=db.scalar(select(LedgerEntry).where(LedgerEntry.business_id==business_id,LedgerEntry.reference_id==sale.id,LedgerEntry.entry_type=="credit_sale"))
            if original_credit:original_credit.amount=sale.net
            sale=split_sale;sale_id=split_sale.id
        else:
            sale.payment_mode=body.payment_mode
            sale.customer_id=None
    line.quantity=body.quantity;line.net=body.total_price;line.unit_price=body.total_price/body.quantity
    movement=db.scalar(select(InventoryMovement).where(InventoryMovement.business_id==business_id,InventoryMovement.reference_id==sale_id,InventoryMovement.product_id==line.product_id,InventoryMovement.movement_type=="sale"));
    if movement:movement.quantity_base=-body.quantity
    db.flush();gross=db.scalar(select(func.coalesce(func.sum(SaleLine.net),0)).where(SaleLine.sale_id==sale_id));sale.gross=gross;sale.net=max(0,gross-sale.discount);credit=db.scalar(select(LedgerEntry).where(LedgerEntry.business_id==business_id,LedgerEntry.reference_id==sale_id,LedgerEntry.entry_type=="credit_sale"));
    if credit:
        if sale.payment_mode=="customer_credit" and sale.customer_id:credit.party_id=sale.customer_id;credit.amount=sale.net
        else:db.delete(credit)
    audit(db,business_id,user,"update","sale",sale.id);db.commit();return {"updated":True,"sale_total":sale.net,"sale_id":sale.id,"line_id":line.id,"payment_mode":sale.payment_mode}
@router.delete("/businesses/{business_id}/sales/{sale_id}")
def delete_sale(business_id:str,sale_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"}); sale=db.scalar(select(Sale).where(Sale.id==sale_id,Sale.business_id==business_id))
    if not sale:raise HTTPException(404,"Sale not found")
    db.execute(delete(InventoryMovement).where(InventoryMovement.business_id==business_id,InventoryMovement.reference_id==sale_id));db.execute(delete(LedgerEntry).where(LedgerEntry.business_id==business_id,LedgerEntry.reference_id==sale_id));db.execute(delete(SaleLine).where(SaleLine.business_id==business_id,SaleLine.sale_id==sale_id));audit(db,business_id,user,"delete","sale",sale.id);db.delete(sale);db.commit();return {"deleted":True}
@router.delete("/businesses/{business_id}/sales/{sale_id}/lines/{line_id}")
def delete_sale_line(business_id:str,sale_id:str,line_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"});sale=db.scalar(select(Sale).where(Sale.id==sale_id,Sale.business_id==business_id));line=db.scalar(select(SaleLine).where(SaleLine.id==line_id,SaleLine.sale_id==sale_id,SaleLine.business_id==business_id))
    if not sale or not line:raise HTTPException(404,"Sale row not found")
    movement=db.scalar(select(InventoryMovement).where(InventoryMovement.business_id==business_id,InventoryMovement.reference_id==sale_id,InventoryMovement.product_id==line.product_id,InventoryMovement.movement_type=="sale"));
    if movement:db.delete(movement)
    db.delete(line);db.flush();remaining=db.scalar(select(func.count(SaleLine.id)).where(SaleLine.sale_id==sale_id))
    if remaining:
        gross=db.scalar(select(func.coalesce(func.sum(SaleLine.net),0)).where(SaleLine.sale_id==sale_id));sale.gross=gross;sale.net=max(0,gross-sale.discount);credit=db.scalar(select(LedgerEntry).where(LedgerEntry.business_id==business_id,LedgerEntry.reference_id==sale_id,LedgerEntry.entry_type=="credit_sale"));
        if credit:credit.amount=sale.net
    else:
        db.execute(delete(LedgerEntry).where(LedgerEntry.business_id==business_id,LedgerEntry.reference_id==sale_id));db.delete(sale)
    audit(db,business_id,user,"delete_line","sale",sale_id);db.commit();return {"deleted":True,"transaction_deleted":not bool(remaining)}
@router.get("/businesses/{business_id}/sales-daily")
def sales_daily(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id); sale_day=func.date(Sale.created_at)
    rows=db.execute(select(sale_day.label("date"),func.count(func.distinct(Sale.id)).label("transactions"),func.coalesce(func.sum(SaleLine.quantity),0).label("quantity_sold"),func.coalesce(func.sum(SaleLine.net),0).label("total_sales"),func.coalesce(func.sum(SaleLine.net-SaleLine.quantity*SaleLine.unit_cost),0).label("profit"),func.coalesce(func.sum(case((func.lower(Sale.payment_mode)=="cash",SaleLine.net),else_=0)),0).label("cash_sales"),func.coalesce(func.sum(case((func.lower(Sale.payment_mode)=="upi",SaleLine.net),else_=0)),0).label("upi_sales"),func.coalesce(func.sum(case((func.lower(Sale.payment_mode)=="customer_credit",SaleLine.net),else_=0)),0).label("credit_sales")).outerjoin(SaleLine,SaleLine.sale_id==Sale.id).where(Sale.business_id==business_id).group_by(sale_day).order_by(sale_day.desc()).limit(90)).all()
    return [{"date":r.date,"transactions":r.transactions,"quantity_sold":r.quantity_sold,"total_sales":r.total_sales,"profit":r.profit,"cash_sales":r.cash_sales,"upi_sales":r.upi_sales,"credit_sales":r.credit_sales} for r in rows]

@router.get("/businesses/{business_id}/sales-products-daily")
def sales_products_daily(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id); sale_day=func.date(Sale.created_at)
    rows=db.execute(select(sale_day.label("date"),Product.id.label("product_id"),Product.name.label("product_name"),Product.base_unit.label("unit"),func.coalesce(func.sum(SaleLine.quantity),0).label("quantity_sold"),func.coalesce(func.sum(SaleLine.net),0).label("total_sales"),func.coalesce(func.sum(SaleLine.net-SaleLine.quantity*SaleLine.unit_cost),0).label("profit")).join(SaleLine,SaleLine.sale_id==Sale.id).join(Product,Product.id==SaleLine.product_id).where(Sale.business_id==business_id).group_by(sale_day,Product.id,Product.name,Product.base_unit).order_by(sale_day.desc(),func.sum(SaleLine.net).desc()).limit(1000)).all()
    return [{"date":r.date,"product_id":r.product_id,"product_name":r.product_name,"unit":r.unit,"quantity_sold":r.quantity_sold,"total_sales":r.total_sales,"profit":r.profit} for r in rows]
@router.get("/businesses/{business_id}/purchases")
def purchase_list(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)): member(db,user,business_id); return db.scalars(select(Purchase).where(Purchase.business_id==business_id).order_by(Purchase.created_at.desc()).limit(100)).all()
@router.get("/businesses/{business_id}/purchases-details")
def purchases_details(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id);rows=db.execute(select(Purchase,PurchaseLine,Product,Supplier).join(PurchaseLine,PurchaseLine.purchase_id==Purchase.id).join(Product,Product.id==PurchaseLine.product_id).join(Supplier,Supplier.id==Purchase.supplier_id).where(Purchase.business_id==business_id).order_by(Purchase.created_at.desc()).limit(300)).all()
    return [{"purchase_id":p.id,"line_id":line.id,"date":p.created_at,"invoice_number":p.invoice_number,"payment_mode":p.payment_mode,"vendor_id":vendor.id,"vendor_name":vendor.name,"product_name":product.name,"quantity":line.quantity,"total_price":line.total,"unit_price":line.unit_cost} for p,line,product,vendor in rows]
@router.get("/businesses/{business_id}/credit-details")
def credit_details(business_id:str,party_type:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id)
    if party_type=="customer":
        credit_day=func.date(LedgerEntry.created_at)
        rows=db.execute(select(credit_day.label("date"),LedgerEntry.party_id,func.sum(LedgerEntry.amount).label("amount")).where(LedgerEntry.business_id==business_id,LedgerEntry.party_type=="customer",LedgerEntry.entry_type=="credit_sale").group_by(credit_day,LedgerEntry.party_id).order_by(credit_day.desc()).limit(300)).all()
        names={x.id:x.name for x in db.scalars(select(Customer).where(Customer.business_id==business_id))}
        return [{"id":f"{row.date}-{row.party_id}","date":row.date,"party_id":row.party_id,"party_name":names.get(row.party_id,"Unknown customer"),"amount":row.amount} for row in rows]
    if party_type=="supplier":
        credit_day=func.date(Purchase.created_at)
        rows=db.execute(select(credit_day.label("date"),Supplier.id.label("party_id"),Supplier.name.label("party_name"),func.sum(Purchase.total).label("amount")).join(Supplier,Supplier.id==Purchase.supplier_id).where(Purchase.business_id==business_id,Purchase.payment_mode=="supplier_credit").group_by(credit_day,Supplier.id,Supplier.name).order_by(credit_day.desc()).limit(300)).all()
        return [{"id":f"{row.date}-{row.party_id}","date":row.date,"party_id":row.party_id,"party_name":row.party_name,"amount":row.amount} for row in rows]
    raise HTTPException(422,"Choose customer or supplier.")
@router.patch("/businesses/{business_id}/purchases/{purchase_id}/lines/{line_id}")
def edit_purchase_line(business_id:str,purchase_id:str,line_id:str,body:TransactionLineEditIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"}); purchase=db.scalar(select(Purchase).where(Purchase.id==purchase_id,Purchase.business_id==business_id));line=db.scalar(select(PurchaseLine).where(PurchaseLine.id==line_id,PurchaseLine.purchase_id==purchase_id,PurchaseLine.business_id==business_id))
    if not purchase or not line:raise HTTPException(404,"Purchase row not found")
    if body.payment_mode:
        if body.payment_mode not in {"cash","upi","supplier_credit"}:raise HTTPException(422,"Choose Cash, UPI or Udhaar.")
        line_count=db.scalar(select(func.count(PurchaseLine.id)).where(PurchaseLine.purchase_id==purchase.id))
        if line_count>1 and body.payment_mode!=purchase.payment_mode:
            split_purchase=Purchase(business_id=business_id,store_id=purchase.store_id,supplier_id=purchase.supplier_id,invoice_number=f"{purchase.invoice_number[:44]}-{line.id[:6]}",total=line.total,paid=0 if body.payment_mode=="supplier_credit" else line.total,payment_mode=body.payment_mode,status=purchase.status,created_by=user.id,created_at=purchase.created_at)
            db.add(split_purchase);db.flush();line.purchase_id=split_purchase.id
            movement=db.scalar(select(InventoryMovement).where(InventoryMovement.business_id==business_id,InventoryMovement.reference_id==purchase.id,InventoryMovement.product_id==line.product_id,InventoryMovement.movement_type=="purchase_receipt"))
            if movement:movement.reference_id=split_purchase.id
            original_total=db.scalar(select(func.coalesce(func.sum(PurchaseLine.total),0)).where(PurchaseLine.purchase_id==purchase.id));purchase.total=original_total;purchase.paid=0 if purchase.payment_mode=="supplier_credit" else original_total
            original_credit=db.scalar(select(LedgerEntry).where(LedgerEntry.business_id==business_id,LedgerEntry.reference_id==purchase.id,LedgerEntry.entry_type=="purchase_credit"))
            if original_credit:original_credit.amount=original_total
            purchase=split_purchase;purchase_id=split_purchase.id
        else:purchase.payment_mode=body.payment_mode
    line.quantity=body.quantity;line.total=body.total_price;line.unit_cost=body.total_price/body.quantity
    product=db.scalar(select(Product).where(Product.id==line.product_id,Product.business_id==business_id))
    if product:product.purchase_cost=line.unit_cost
    movement=db.scalar(select(InventoryMovement).where(InventoryMovement.business_id==business_id,InventoryMovement.reference_id==purchase_id,InventoryMovement.product_id==line.product_id,InventoryMovement.movement_type=="purchase_receipt"));
    if movement:movement.quantity_base=body.quantity;movement.unit_cost=line.unit_cost
    db.flush();purchase.total=db.scalar(select(func.coalesce(func.sum(PurchaseLine.total),0)).where(PurchaseLine.purchase_id==purchase_id));purchase.paid=0 if purchase.payment_mode=="supplier_credit" else purchase.total;credit=db.scalar(select(LedgerEntry).where(LedgerEntry.business_id==business_id,LedgerEntry.reference_id==purchase_id,LedgerEntry.entry_type=="purchase_credit"));
    if purchase.payment_mode=="supplier_credit":
        if credit:credit.amount=purchase.total
        else:db.add(LedgerEntry(business_id=business_id,party_type="supplier",party_id=purchase.supplier_id,entry_type="purchase_credit",amount=purchase.total,reference_id=purchase.id,created_by=user.id,created_at=purchase.created_at))
    elif credit:db.delete(credit)
    audit(db,business_id,user,"update","purchase",purchase.id);db.commit();return {"updated":True,"purchase_total":purchase.total,"purchase_id":purchase.id,"line_id":line.id,"payment_mode":purchase.payment_mode}
@router.delete("/businesses/{business_id}/purchases/{purchase_id}")
def delete_purchase(business_id:str,purchase_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"});purchase=db.scalar(select(Purchase).where(Purchase.id==purchase_id,Purchase.business_id==business_id))
    if not purchase:raise HTTPException(404,"Purchase not found")
    db.execute(delete(InventoryMovement).where(InventoryMovement.business_id==business_id,InventoryMovement.reference_id==purchase_id));db.execute(delete(LedgerEntry).where(LedgerEntry.business_id==business_id,LedgerEntry.reference_id==purchase_id));db.execute(delete(PurchaseLine).where(PurchaseLine.business_id==business_id,PurchaseLine.purchase_id==purchase_id));audit(db,business_id,user,"delete","purchase",purchase.id);db.delete(purchase);db.commit();return {"deleted":True}
@router.delete("/businesses/{business_id}/purchases/{purchase_id}/lines/{line_id}")
def delete_purchase_line(business_id:str,purchase_id:str,line_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"});purchase=db.scalar(select(Purchase).where(Purchase.id==purchase_id,Purchase.business_id==business_id));line=db.scalar(select(PurchaseLine).where(PurchaseLine.id==line_id,PurchaseLine.purchase_id==purchase_id,PurchaseLine.business_id==business_id))
    if not purchase or not line:raise HTTPException(404,"Purchase row not found")
    movement=db.scalar(select(InventoryMovement).where(InventoryMovement.business_id==business_id,InventoryMovement.reference_id==purchase_id,InventoryMovement.product_id==line.product_id,InventoryMovement.movement_type=="purchase_receipt"));
    if movement:db.delete(movement)
    db.delete(line);db.flush();remaining=db.scalar(select(func.count(PurchaseLine.id)).where(PurchaseLine.purchase_id==purchase_id))
    if remaining:
        purchase.total=db.scalar(select(func.coalesce(func.sum(PurchaseLine.total),0)).where(PurchaseLine.purchase_id==purchase_id));credit=db.scalar(select(LedgerEntry).where(LedgerEntry.business_id==business_id,LedgerEntry.reference_id==purchase_id,LedgerEntry.entry_type=="purchase_credit"));
        if credit:credit.amount=max(0,purchase.total-purchase.paid)
    else:
        db.execute(delete(LedgerEntry).where(LedgerEntry.business_id==business_id,LedgerEntry.reference_id==purchase_id));db.delete(purchase)
    audit(db,business_id,user,"delete_line","purchase",purchase_id);db.commit();return {"deleted":True,"transaction_deleted":not bool(remaining)}
@router.get("/businesses/{business_id}/expenses")
def expense_list(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)): member(db,user,business_id); return db.scalars(select(Expense).where(Expense.business_id==business_id).order_by(Expense.created_at.desc()).limit(100)).all()
@router.post("/businesses/{business_id}/expenses",status_code=201)
def expense(business_id:str,body:ExpenseIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id); row=Expense(business_id=business_id,store_id=body.store_id,category=body.category,amount=body.amount,payment_method=body.payment_method,payee=body.payee,created_by=user.id); db.add(row); db.flush(); audit(db,business_id,user,"create","expense",row.id); db.commit(); return row

@router.get("/businesses/{business_id}/dashboard")
def dashboard(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    membership=member(db,user,business_id); today=now().date().isoformat(); previous_day=(now().date()-timedelta(days=1)).isoformat(); sales=db.scalar(select(func.coalesce(func.sum(Sale.net),0)).where(Sale.business_id==business_id)); today_sales=db.scalar(select(func.coalesce(func.sum(Sale.net),0)).where(Sale.business_id==business_id,func.date(Sale.created_at)==today)); today_cash=db.scalar(select(func.coalesce(func.sum(Sale.net),0)).where(Sale.business_id==business_id,func.date(Sale.created_at)==today,Sale.payment_mode=="cash")); today_transactions=db.scalar(select(func.count(Sale.id)).where(Sale.business_id==business_id,func.date(Sale.created_at)==today)); today_quantity=db.scalar(select(func.coalesce(func.sum(SaleLine.quantity),0)).join(Sale,Sale.id==SaleLine.sale_id).where(Sale.business_id==business_id,func.date(Sale.created_at)==today)); purchases=db.scalar(select(func.coalesce(func.sum(Purchase.total),0)).where(Purchase.business_id==business_id)); expenses=db.scalar(select(func.coalesce(func.sum(Expense.amount),0)).where(Expense.business_id==business_id)); profit_expression=SaleLine.net-SaleLine.quantity*SaleLine.unit_cost; gp=db.scalar(select(func.coalesce(func.sum(profit_expression),0)).where(SaleLine.business_id==business_id)); today_profit=db.scalar(select(func.coalesce(func.sum(profit_expression),0)).join(Sale,Sale.id==SaleLine.sale_id).where(SaleLine.business_id==business_id,func.date(Sale.created_at)==today)); previous_day_profit=db.scalar(select(func.coalesce(func.sum(profit_expression),0)).join(Sale,Sale.id==SaleLine.sale_id).where(SaleLine.business_id==business_id,func.date(Sale.created_at)==previous_day)); inv=inventory(business_id,user,db); customer_balances=ledger(business_id,"customer",user,db);customer_due=sum(max(0,float(x["balance"])) for x in customer_balances);supplier_credit=db.scalar(select(func.coalesce(func.sum(Purchase.total),0)).where(Purchase.business_id==business_id,Purchase.payment_mode=="supplier_credit"));supplier_paid=db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount),0)).where(LedgerEntry.business_id==business_id,LedgerEntry.party_type=="supplier",LedgerEntry.entry_type=="payment"));supplier_due=max(0,supplier_credit+supplier_paid);result={"today_sales":today_sales,"today_cash":today_cash,"today_transactions":today_transactions,"today_quantity_sold":today_quantity,"net_sales":sales,"customer_outstanding":customer_due,"supplier_outstanding":supplier_due,"customer_due_count":sum(float(x["balance"])>0 for x in customer_balances),"low_stock_count":sum(x["status"]=="low" for x in inv),"out_of_stock_count":sum(x["status"]=="out_of_stock" for x in inv)}
    if membership.role.value in {"owner","manager"}:result.update({"total_purchases":purchases,"estimated_gross_profit":gp,"expenses":expenses,"estimated_operating_profit":gp-expenses,"today_profit":today_profit,"previous_day_profit":previous_day_profit,"inventory_value":sum(x["value"] for x in inv)})
    return result
@router.get("/businesses/{business_id}/reorder-list")
def reorder(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)): return [{**x,"suggested_quantity":max(0,x["reorder_level"]*2-x["stock"])} for x in inventory(business_id,user,db) if x["status"]!="healthy"]

@router.post("/businesses/{business_id}/images",status_code=202)
async def upload_image(business_id:str,store_id:str,document_type:str,file:UploadFile=File(...),user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id); tenant_store(db,business_id,store_id)
    if document_type not in {"sales","purchase","stock","udhaar"}:raise HTTPException(422,"Choose Sales, Purchase, Stock or Udhaar for this image.")
    allowed={"image/jpeg","image/png","image/webp"};
    if file.content_type not in allowed: raise HTTPException(415,"Please upload a JPG, PNG or WebP image.")
    content=await file.read();
    if len(content)>10*1024*1024: raise HTTPException(413,"File exceeds 10 MB")
    signatures={"image/jpeg":content.startswith(b"\xff\xd8\xff"),"image/png":content.startswith(b"\x89PNG\r\n\x1a\n"),"image/webp":content.startswith(b"RIFF") and content[8:12]==b"WEBP"}
    if not signatures[file.content_type]:raise HTTPException(415,"This file does not appear to be a valid image. Take a new photo and try again.")
    folder=Path(settings.upload_dir)/business_id; folder.mkdir(parents=True,exist_ok=True); path=folder/f"{uuid.uuid4()}{Path(file.filename or 'upload').suffix.lower()}"; path.write_bytes(content)
    row=ImageDocument(business_id=business_id,store_id=store_id,uploaded_by=user.id,document_type=document_type,filename=file.filename or "upload",storage_path=str(path),mime_type=file.content_type,status="uploaded"); db.add(row); db.commit(); db.refresh(row); return {"id":row.id,"status":row.status}
@router.post("/businesses/{business_id}/images/{document_id}/extract")
async def extract_image(business_id:str,document_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id); row=db.scalar(select(ImageDocument).where(ImageDocument.id==document_id,ImageDocument.business_id==business_id));
    if not row: raise HTTPException(404,"Document not found")
    try: data=await GeminiProvider().extract(row.storage_path,row.mime_type,row.document_type)
    except RuntimeError as exc: row.status="failed"; db.commit(); raise HTTPException(502,f"AI extraction failed: {exc}") from exc
    except Exception as exc: row.status="failed"; db.commit(); raise HTTPException(502,"AI extraction failed because Gemini could not be reached. Check your internet connection and try again.") from exc
    row.structured_data=data; row.confidence=float(data.get("confidence",0)); row.status="review_required"; db.commit(); return {"id":row.id,"status":row.status,"data":data}
@router.post("/businesses/{business_id}/images/{document_id}/confirm")
def confirm_image(business_id:str,document_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"}); row=db.scalar(select(ImageDocument).where(ImageDocument.id==document_id,ImageDocument.business_id==business_id));
    if not row or row.status!="review_required": raise HTTPException(409,"Document is not ready for confirmation")
    created_id=None
    if row.document_type=="sales":
        extracted=(row.structured_data or {}).get("rows",[]); lines=[]
        for item in extracted:
            product=db.scalar(select(Product).where(Product.business_id==business_id,func.lower(Product.name)==str(item.get("product_name","")).lower()))
            if not product:
                alias=db.scalar(select(ProductAlias).where(ProductAlias.business_id==business_id,func.lower(ProductAlias.alias)==str(item.get("product_name","")).lower())); product=db.get(Product,alias.product_id) if alias else None
            if not product: raise HTTPException(409,f"Review required: product not matched: {item.get('product_name','unknown')}")
            qty=float(item.get("quantity",0)); price=float(item.get("total_price",0))/qty if qty and item.get("total_price") is not None else float(item.get("unit_price",0) or product.selling_price)
            if qty<=0: raise HTTPException(409,"Review required: all quantities must be positive")
            lines.append((product,qty,price))
        if not lines: raise HTTPException(409,"No confirmed sale rows")
        gross=sum(q*p for _,q,p in lines); sale=Sale(business_id=business_id,store_id=row.store_id,invoice_number=f"IMG-{row.id[:8]}",payment_mode="cash",gross=gross,discount=0,net=gross,created_by=user.id); db.add(sale); db.flush(); created_id=sale.id
        for product,qty,price in lines: db.add(SaleLine(business_id=business_id,sale_id=sale.id,product_id=product.id,quantity=qty,unit_price=price,unit_cost=product.purchase_cost,net=qty*price)); db.add(InventoryMovement(business_id=business_id,store_id=row.store_id,product_id=product.id,movement_type="sale",quantity_base=-qty,unit_cost=product.purchase_cost,reference_type="image_sale",reference_id=sale.id,created_by=user.id))
    row.status="confirmed"; row.confirmed_by=user.id; audit(db,business_id,user,"confirm_and_post","image_document",row.id); db.commit(); return {"id":row.id,"status":"confirmed","created_transaction_id":created_id}

@router.post("/businesses/{business_id}/imports/products")
async def import_products(business_id:str,store_id:str,file:UploadFile=File(...),user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"}); raw=(await file.read()).decode("utf-8-sig"); reader=csv.DictReader(io.StringIO(raw)); required={"code","name","unit","selling_price","purchase_cost","reorder_level"};
    if not required.issubset(set(reader.fieldnames or [])): raise HTTPException(422,{"missing_columns":sorted(required-set(reader.fieldnames or []))})
    rows=list(reader); errors=[]
    for i,r in enumerate(rows,2):
        try: float(r["selling_price"]); float(r["purchase_cost"]); float(r["reorder_level"])
        except ValueError: errors.append({"row":i,"error":"Invalid numeric value"})
    if errors: raise HTTPException(422,{"errors":errors})
    for r in rows: db.add(Product(business_id=business_id,store_id=store_id,code=r["code"],name=r["name"],base_unit=r["unit"],purchase_unit=r["unit"],selling_unit=r["unit"],selling_price=float(r["selling_price"]),purchase_cost=float(r["purchase_cost"]),reorder_level=float(r["reorder_level"])))
    audit(db,business_id,user,"import","products",str(uuid.uuid4())); db.commit(); return {"imported":len(rows),"errors":[]}

@router.get("/businesses/{business_id}/reports/daily-closing.pdf")
def daily_closing_pdf(business_id:str,report_date:date|None=None,user:User=Depends(current_user),db:Session=Depends(get_db)):
    from fastapi.responses import Response
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    member(db,user,business_id,{"owner","manager"}); day=(report_date or now().date()).isoformat()
    sale_total=float(db.scalar(select(func.coalesce(func.sum(SaleLine.net),0)).join(Sale,Sale.id==SaleLine.sale_id).where(Sale.business_id==business_id,func.date(Sale.created_at)==day)) or 0)
    profit=float(db.scalar(select(func.coalesce(func.sum(SaleLine.net-SaleLine.quantity*SaleLine.unit_cost),0)).join(Sale,Sale.id==SaleLine.sale_id).where(Sale.business_id==business_id,func.date(Sale.created_at)==day)) or 0)
    def sale_mode(mode:str):return float(db.scalar(select(func.coalesce(func.sum(SaleLine.net),0)).join(Sale,Sale.id==SaleLine.sale_id).where(Sale.business_id==business_id,func.date(Sale.created_at)==day,Sale.payment_mode==mode)) or 0)
    cash_sales,upi_sales,credit_sales=sale_mode("cash"),sale_mode("upi"),sale_mode("customer_credit")
    purchases=float(db.scalar(select(func.coalesce(func.sum(Purchase.total),0)).where(Purchase.business_id==business_id,func.date(Purchase.created_at)==day)) or 0)
    expenses=float(db.scalar(select(func.coalesce(func.sum(Expense.amount),0)).where(Expense.business_id==business_id,func.date(Expense.created_at)==day)) or 0)
    customer_received=abs(float(db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount),0)).where(LedgerEntry.business_id==business_id,LedgerEntry.party_type=="customer",LedgerEntry.entry_type=="payment_received",func.date(LedgerEntry.created_at)==day)) or 0))
    supplier_paid=abs(float(db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount),0)).where(LedgerEntry.business_id==business_id,LedgerEntry.party_type=="supplier",LedgerEntry.entry_type=="payment",func.date(LedgerEntry.created_at)==day)) or 0))
    stock=inventory(business_id,user,db); low=[x for x in stock if x["status"]!="healthy"]
    customer_due=float(db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount),0)).where(LedgerEntry.business_id==business_id,LedgerEntry.party_type=="customer")) or 0); supplier_due=float(db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount),0)).where(LedgerEntry.business_id==business_id,LedgerEntry.party_type=="supplier")) or 0)
    tasks=[*(f"Restock {x['name']} ({x['stock']:g} {x['unit']} left)" for x in low[:8]),*(["Collect pending customer udhaar"] if customer_due>0 else []),*(["Review supplier payment due"] if supplier_due>0 else [])]
    metrics=[("Total sales",sale_total),("Profit",profit),("Cash sales",cash_sales),("UPI sales",upi_sales),("Udhaar sales",credit_sales),("Purchases",purchases),("Expenses",expenses),("Customer payment received",customer_received),("Supplier payment made",supplier_paid)]
    buffer=io.BytesIO(); doc=SimpleDocTemplate(buffer,pagesize=A4,rightMargin=18*mm,leftMargin=18*mm,topMargin=16*mm,bottomMargin=16*mm); styles=getSampleStyleSheet(); story=[Paragraph("Aaj ka poora hisaab",styles["Title"]),Paragraph(f"Daily closing report - {day}",styles["Normal"]),Spacer(1,8*mm)]
    table=Table([["Metric","Amount (INR)"],*[[name,"Not recorded" if value is None else f"{value:,.2f}"] for name,value in metrics]],colWidths=[105*mm,55*mm]);table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#167b59")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("ALIGN",(1,0),(1,-1),"RIGHT"),("GRID",(0,0),(-1,-1),.4,colors.HexColor("#dce5df")),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f4f8f5")]),("BOTTOMPADDING",(0,0),(-1,-1),7),("TOPPADDING",(0,0),(-1,-1),7)]));story.extend([table,Spacer(1,7*mm),Paragraph(f"Low-stock products ({len(low)})",styles["Heading2"])])
    story.append(Paragraph("<br/>".join([f"- {x['name']}: {x['stock']:g} {x['unit']}" for x in low]) or "No low-stock products.",styles["BodyText"]));story.extend([Spacer(1,5*mm),Paragraph(f"Pending tasks ({len(tasks)})",styles["Heading2"]),Paragraph("<br/>".join(f"- {task}" for task in tasks) or "No pending tasks.",styles["BodyText"])]);doc.build(story)
    return Response(buffer.getvalue(),media_type="application/pdf",headers={"Content-Disposition":f'attachment; filename="aaj-ka-poora-hisaab-{day}.pdf"'})

@router.get("/businesses/{business_id}/reports/daily.csv")
def daily_csv(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    from fastapi.responses import Response
    data=dashboard(business_id,user,db); out=io.StringIO(); w=csv.writer(out); w.writerow(["metric","value"]); [w.writerow([k,v]) for k,v in data.items()]; return Response(out.getvalue(),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=daily-summary.csv"})
