import csv, difflib, io, os, re, uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from .config import settings
from .database import SessionLocal, get_db
from .gemini_provider import GeminiProvider
from .models import AIUsageEvent, Alert, AuditLog, BusinessUser, Category, Customer, Expense, ImageDocument, InventoryMovement, LedgerEntry, Product, ProductAlias, ProductUnitMap, Purchase, PurchaseLine, Sale, SaleLine, Store, Subscription, Supplier, UdhaarEntry, User
from .security import current_user

router = APIRouter(prefix="/api")
def now(): return datetime.now(timezone.utc)
def member(db: Session, user: User, business_id: str, roles: set[str] | None=None):
    m=db.scalar(select(BusinessUser).where(BusinessUser.business_id==business_id,BusinessUser.user_id==user.id,BusinessUser.active.is_(True)))
    if not m: raise HTTPException(404,"Business not found")
    if roles and m.role.value not in roles: raise HTTPException(403,"Permission denied")
    return m
def require_starter(db:Session,business_id:str):
    subscription=db.scalar(select(Subscription).where(Subscription.business_id==business_id))
    if not subscription or subscription.status not in {"active","trial"} or not subscription.ends_at:
        raise HTTPException(403,"An active Starter membership is required")
    ends=subscription.ends_at
    if ends.tzinfo is None:ends=ends.replace(tzinfo=timezone.utc)
    if ends<=now():raise HTTPException(403,"Your Starter membership has expired")
    return subscription
def audit(db,bid,user,action,entity,rid): db.add(AuditLog(business_id=bid,user_id=user.id,action=action,entity=entity,record_id=rid))
def tracked_provider(business_id:str,user_id:str,feature:str)->GeminiProvider:
    def record(usage:dict):
        with SessionLocal() as usage_db:
            usage_db.add(AIUsageEvent(business_id=business_id,user_id=user_id,feature=feature,
                prompt_tokens=usage["prompt_tokens"],output_tokens=usage["output_tokens"],total_tokens=usage["total_tokens"]))
            usage_db.commit()
    return GeminiProvider(usage_recorder=record)

class MasterIn(BaseModel): name:str=Field(min_length=2,max_length=180); mobile:str|None=None
class ProductIn(BaseModel): store_id:str; code:str; name:str; category_id:str|None=None; supplier_id:str|None=None; local_name:str|None=None; barcode:str|None=None; base_unit:str="piece"; purchase_unit:str="piece"; selling_unit:str="piece"; conversion_factor:float=Field(1,gt=0); mrp:float=Field(0,ge=0); selling_price:float=Field(0,ge=0); purchase_cost:float=Field(0,ge=0); reorder_level:float=Field(0,ge=0); aliases:list[str]=[]
class ResolveProductIn(BaseModel): store_id:str; name:str=Field(min_length=2,max_length=180); unit:str|None=None; mrp:float=Field(0,ge=0); price:float=Field(0,ge=0)
class MatchProductIn(BaseModel): name:str=Field(min_length=2,max_length=180)
class ProductEditIn(BaseModel): name:str=Field(min_length=2,max_length=180); selling_unit:str; mrp:float=Field(ge=0); selling_price:float=Field(ge=0)
class StockIn(BaseModel): store_id:str; product_id:str; quantity:float=Field(gt=0); movement_type:str="opening_stock"; unit_cost:float=Field(0,ge=0); notes:str|None=None
class LineIn(BaseModel): product_id:str; quantity:float=Field(gt=0); unit_price:float=Field(ge=0)
class SaleIn(BaseModel): store_id:str; invoice_number:str; payment_mode:str; transaction_date:date|None=None; customer_id:str|None=None; discount:float=Field(0,ge=0); lines:list[LineIn]=Field(min_length=1)
class NamedSaleLineIn(BaseModel): name:str=Field(min_length=2,max_length=180); quantity:float=Field(gt=0); total_price:float=Field(gt=0); unit:str|None=None
class NamedSaleIn(BaseModel): store_id:str; invoice_number:str; payment_mode:str; transaction_date:date|None=None; customer_id:str|None=None; discount:float=Field(0,ge=0); lines:list[NamedSaleLineIn]=Field(min_length=1,max_length=250)
class PurchaseIn(BaseModel): store_id:str; supplier_id:str; invoice_number:str; transaction_date:date|None=None; payment_mode:str="cash"; paid:float=Field(0,ge=0); lines:list[LineIn]=Field(min_length=1)
class PaymentIn(BaseModel): party_type:str; party_id:str; amount:float=Field(gt=0); entry_type:str="payment"
class SupplierPaymentRecordIn(BaseModel): supplier_id:str; amount_paid:float=Field(gt=0); payment_date:date|None=None
class UdhaarEntryIn(BaseModel):
    customer_id:str
    entry_date:date|None=None
    products:str=Field(default="",max_length=2000)
    amount:float=Field(ge=0)
    total_present:bool=True
    given:float=Field(default=0,ge=0)
class ExpenseIn(BaseModel): store_id:str; category:str; amount:float=Field(gt=0); payment_method:str; payee:str|None=None; transaction_date:date|None=None
class NaturalEntryIn(BaseModel): entry_type:str; text:str=Field(min_length=2,max_length=5000)
class TransactionLineEditIn(BaseModel): quantity:float=Field(gt=0); total_price:float=Field(ge=0); payment_mode:str|None=None; customer_id:str|None=None
class BusinessChatIn(BaseModel):
    question:str=Field(min_length=2,max_length=1200)
    history:list[dict]=Field(default_factory=list,max_length=10)
    language:str=Field(default="en",pattern=r"^(en|hi|mr|gu|kn|ta)$")
class ReportInsightIn(BaseModel):
    period:str=Field(pattern=r"^(week|month|year)$")
    language:str=Field(default="en",pattern=r"^(en|hi|mr|gu|kn|ta)$")
class VendorEditIn(BaseModel): name:str=Field(min_length=2,max_length=180); mobile:str|None=None
class InventoryEditIn(BaseModel): closing_stock:float=Field(ge=0); notes:str|None=None

PAYMENT_MODES={"cash","upi","customer_credit"}
def known_profit_expression():
    return case((SaleLine.cost_known.is_(True),SaleLine.net-SaleLine.quantity*SaleLine.unit_cost),else_=0.0)
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
    try: return await tracked_provider(business_id,user.id,f"parse_{body.entry_type}").parse_text(body.text,body.entry_type)
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
async def add_product(business_id:str,body:ProductIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"});
    if not db.scalar(select(Store).where(Store.id==body.store_id,Store.business_id==business_id)): raise HTTPException(400,"Invalid store")
    category_id=body.category_id
    if category_id:
        if not db.scalar(select(Category).where(Category.id==category_id,Category.business_id==business_id,Category.active.is_(True))):raise HTTPException(400,"Invalid category")
    else:
        category_id=(await resolve_product_category(db,business_id,body.name,user.id)).id
    row=Product(business_id=business_id,store_id=body.store_id,code=body.code,name=body.name,category_id=category_id,preferred_supplier_id=body.supplier_id,local_name=body.local_name,barcode=body.barcode,base_unit=body.base_unit,purchase_unit=body.purchase_unit,selling_unit=body.selling_unit,conversion_factor=body.conversion_factor,mrp=body.mrp,selling_price=body.selling_price,purchase_cost=body.purchase_cost,reorder_level=body.reorder_level); db.add(row); db.flush()
    db.add_all([ProductAlias(business_id=business_id,product_id=row.id,alias=x) for x in body.aliases]); audit(db,business_id,user,"create","product",row.id); db.commit(); return row

def fallback_unit(label:str, supplied:str|None=None) -> str:
    lower=label.lower(); unit=(supplied or "").lower()
    if unit:return unit
    if re.search(r"\b\d+(\.\d+)?\s*(ml|l|litre|liter)\b",lower) or any(x in lower for x in ["cola","juice","milk","oil","drink"]): return "litre"
    if re.search(r"\b\d+(\.\d+)?\s*(kg|g|gram)\b",lower) or any(x in lower for x in ["rice","atta","dal","sugar","salt"]): return "kilogram"
    if any(x in lower for x in ["biscuit","parle","namkeen","noodle","soap","packet","pack"]): return "packet"
    return "piece"

KIRANA_CATEGORIES=[
    "Staples & Grains","Pulses & Lentils","Dairy","Beverages",
    "Biscuits & Snacks","Cooking Oil & Ghee","Spices & Condiments",
    "Instant & Packaged Food","Personal Care","Home Care",
    "Confectionery","Other",
]

def fallback_category(label:str)->str:
    value=label.casefold()
    rules=[
        ("Dairy",["milk","dahi","curd","paneer","cheese","butter","cream"]),
        ("Pulses & Lentils",["dal","daal","pulse","rajma","chana","lentil"]),
        ("Cooking Oil & Ghee",["oil","ghee","vanaspati"]),
        ("Biscuits & Snacks",["biscuit","cookie","namkeen","chips","parle","kurkure"]),
        ("Beverages",["cola","coke","pepsi","juice","drink","tea","coffee","water"]),
        ("Spices & Condiments",["salt","masala","spice","haldi","mirch","jeera","sauce","pickle"]),
        ("Instant & Packaged Food",["noodle","maggi","pasta","soup","oats","cornflake"]),
        ("Personal Care",["shampoo","toothpaste","toothbrush","soap","face wash","hair"]),
        ("Home Care",["detergent","surf","cleaner","phenyl","dishwash","floor"]),
        ("Confectionery",["chocolate","candy","toffee","gum"]),
        ("Staples & Grains",["atta","flour","rice","chawal","sugar","cheeni","wheat","suji","maida"]),
    ]
    return next((category for category,terms in rules if any(term in value for term in terms)),"Other")

async def resolve_product_category(db:Session,business_id:str,label:str,user_id:str|None=None)->Category:
    existing=db.scalars(select(Category).where(Category.business_id==business_id,Category.active.is_(True))).all()
    existing_by_name={row.name.casefold():row for row in existing}
    allowed=list(dict.fromkeys([*KIRANA_CATEGORIES,*[row.name for row in existing]]))
    try:
        decision=await (tracked_provider(business_id,user_id,"product_category") if user_id else GeminiProvider()).classify_kirana_category(label,allowed)
        chosen=decision["category"]
    except Exception:
        chosen=fallback_category(label)
    category=existing_by_name.get(chosen.casefold())
    if category:return category
    category=Category(business_id=business_id,name=chosen)
    db.add(category);db.flush()
    return category

@router.get("/businesses/{business_id}/product-unit-map")
def product_unit_map(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id)
    return db.scalars(select(ProductUnitMap).order_by(ProductUnitMap.display_name)).all()

def normalized_product_name(value:str)->str:
    return re.sub(r"[^a-z0-9\u0900-\u097f]+","",value.casefold())

def fast_resolve_sales_product(db:Session,business_id:str,store_id:str,label:str,unit:str|None,products:list[Product])->tuple[Product,bool]:
    """Resolve a sales product without an external AI call.

    This path is deliberately deterministic and transaction-local so a daily
    sale never waits on one AI request per row. AI extraction already provides
    the cleaned label; close spelling variants are matched locally and truly
    new products are created with an unknown purchase cost.
    """
    normalized=normalized_product_name(label)
    for product in products:
        if normalized in {normalized_product_name(product.name),normalized_product_name(product.local_name or "")}:
            return product,False
    aliases=db.execute(select(ProductAlias,Product).join(Product,Product.id==ProductAlias.product_id).where(ProductAlias.business_id==business_id,Product.active.is_(True))).all()
    for alias,product in aliases:
        if normalized_product_name(alias.alias)==normalized:return product,False
    entered_numbers=re.findall(r"\d+(?:\.\d+)?",label)
    scored=[]
    for product in products:
        candidate_numbers=re.findall(r"\d+(?:\.\d+)?",product.name)
        if entered_numbers and candidate_numbers and entered_numbers!=candidate_numbers:continue
        score=difflib.SequenceMatcher(None,normalized,normalized_product_name(product.name)).ratio()
        scored.append((score,product))
    if scored:
        score,product=max(scored,key=lambda item:item[0])
        if score>=0.88:
            db.add(ProductAlias(business_id=business_id,product_id=product.id,alias=label.strip()))
            return product,False
    category_name=fallback_category(label)
    category=db.scalar(select(Category).where(Category.business_id==business_id,func.lower(Category.name)==category_name.lower()))
    if not category:
        category=Category(business_id=business_id,name=category_name);db.add(category);db.flush()
    resolved_unit=fallback_unit(label,unit)
    product=Product(business_id=business_id,store_id=store_id,code=f"SALE-{uuid.uuid4().hex[:8].upper()}",name=label.strip(),category_id=category.id,base_unit=resolved_unit,purchase_unit=resolved_unit,selling_unit=resolved_unit,conversion_factor=1,mrp=0,selling_price=0,purchase_cost=0,reorder_level=0)
    db.add(product);db.flush();products.append(product)
    return product,True

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
        decision=await tracked_provider(business_id,user.id,"product_match").match_existing_product(entered,[{"id":p.id,"name":p.name,"unit":p.selling_unit,"pack":p.local_name or ""} for p in shortlisted])
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
            result=await tracked_provider(business_id,user.id,"product_unit_research").research_indian_kirana_unit(label); unit=result["selling_unit"]; acceptable=result.get("acceptable_units") or [unit]
            if "milk" in normalized: acceptable=list(dict.fromkeys([*acceptable,"litre","kilogram"]))
            knowledge=ProductUnitMap(normalized_name=normalized,display_name=label,selling_unit=unit,acceptable_units=acceptable,reasoning=result.get("reasoning"),sources=result.get("sources",[]),lookup_status="researched")
        except Exception as exc:
            unit=fallback_unit(label,body.unit); acceptable=[unit]
            if "milk" in normalized: acceptable=list(dict.fromkeys([*acceptable,"litre","kilogram"]))
            knowledge=ProductUnitMap(normalized_name=normalized,display_name=label,selling_unit=unit,acceptable_units=acceptable,reasoning="Local fallback used because web research was unavailable.",sources=[],lookup_status="fallback")
        db.add(knowledge); db.flush()
    unit=(body.unit or knowledge.selling_unit).lower()
    code=f"AUTO-{uuid.uuid4().hex[:8].upper()}"; price=body.price or body.mrp
    category=await resolve_product_category(db,business_id,label,user.id)
    product=Product(business_id=business_id,store_id=body.store_id,code=code,name=label,category_id=category.id,base_unit=unit,purchase_unit=unit,selling_unit=unit,conversion_factor=1,mrp=body.mrp or price,selling_price=price,purchase_cost=0,reorder_level=5); db.add(product); db.flush(); audit(db,business_id,user,"auto_create","product",product.id); db.commit(); return {"product":product,"created":True,"category":{"id":category.id,"name":category.name},"unit_knowledge":{"selling_unit":knowledge.selling_unit,"acceptable_units":knowledge.acceptable_units,"lookup_status":knowledge.lookup_status,"reasoning":knowledge.reasoning}}

@router.post("/businesses/{business_id}/products/backfill-categories")
async def backfill_product_categories(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"})
    products=db.scalars(select(Product).where(Product.business_id==business_id,Product.active.is_(True),Product.category_id.is_(None))).all()
    for product in products:
        category=await resolve_product_category(db,business_id,product.name,user.id)
        product.category_id=category.id
        audit(db,business_id,user,"ai_categorize","product",product.id)
    db.commit()
    return {"categorized":len(products)}

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
    member(db,user,business_id); today=now().date(); movement_day=func.date(InventoryMovement.transaction_date)
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
    if body.customer_id and not db.scalar(select(Customer.id).where(Customer.id==body.customer_id,Customer.business_id==business_id,Customer.active.is_(True))):raise HTTPException(400,"This customer was not found for your business.")
    gross=sum(x.quantity*x.unit_price for x in body.lines); sale=Sale(business_id=business_id,store_id=body.store_id,invoice_number=body.invoice_number,customer_id=body.customer_id,payment_mode=body.payment_mode,gross=gross,discount=body.discount,net=gross-body.discount,created_by=user.id,created_at=posted_at); db.add(sale); db.flush()
    for x in body.lines:
        p=products[x.product_id]; cost_known=p.purchase_cost>0; db.add(SaleLine(business_id=business_id,sale_id=sale.id,product_id=p.id,quantity=x.quantity,unit_price=x.unit_price,unit_cost=p.purchase_cost,cost_known=cost_known,net=x.quantity*x.unit_price)); db.add(InventoryMovement(business_id=business_id,store_id=body.store_id,product_id=p.id,movement_type="sale",quantity_base=-x.quantity,unit_cost=p.purchase_cost,reference_type="sale",reference_id=sale.id,created_by=user.id,transaction_date=posted_at))
    if body.payment_mode=="customer_credit" and body.customer_id:
        db.add(LedgerEntry(business_id=business_id,party_type="customer",party_id=body.customer_id,entry_type="credit_sale",amount=sale.net,reference_id=sale.id,created_by=user.id,created_at=posted_at))
    db.flush()
    for product_id in products:recalculate_product_sale_costs(db,business_id,product_id)
    audit(db,business_id,user,"post","sale",sale.id); db.commit(); return sale

@router.post("/businesses/{business_id}/sales/by-name",status_code=201)
def post_sale_by_name(business_id:str,body:NamedSaleIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id)
    # Lock the store row for the short resolution/write transaction. This
    # prevents concurrent uploads for one store from creating duplicate names.
    store=db.scalar(select(Store).where(Store.id==body.store_id,Store.business_id==business_id).with_for_update())
    if not store:raise HTTPException(400,"This store was not found for your business.")
    if body.payment_mode not in PAYMENT_MODES:raise HTTPException(422,"Choose Cash, UPI or Udhaar as the payment method.")
    if db.scalar(select(Sale.id).where(Sale.business_id==business_id,Sale.invoice_number==body.invoice_number)):raise HTTPException(409,"This sale was already saved. No duplicate entry was created.")
    if body.customer_id and not db.scalar(select(Customer.id).where(Customer.id==body.customer_id,Customer.business_id==business_id,Customer.active.is_(True))):raise HTTPException(400,"This customer was not found for your business.")
    products=list(db.scalars(select(Product).where(Product.business_id==business_id,Product.active.is_(True))))
    resolved=[];created_count=0
    for line in body.lines:
        product,created=fast_resolve_sales_product(db,business_id,body.store_id,line.name,line.unit,products)
        resolved.append((product,line));created_count+=int(created)
    posted_at=entry_datetime(body.transaction_date);gross=sum(line.total_price for _,line in resolved)
    sale=Sale(business_id=business_id,store_id=body.store_id,invoice_number=body.invoice_number,customer_id=body.customer_id,payment_mode=body.payment_mode,gross=gross,discount=body.discount,net=max(0,gross-body.discount),created_by=user.id,created_at=posted_at)
    db.add(sale);db.flush()
    for product,line in resolved:
        unit_price=line.total_price/line.quantity;cost_known=product.purchase_cost>0
        db.add(SaleLine(business_id=business_id,sale_id=sale.id,product_id=product.id,quantity=line.quantity,unit_price=unit_price,unit_cost=product.purchase_cost,cost_known=cost_known,net=line.total_price))
        db.add(InventoryMovement(business_id=business_id,store_id=body.store_id,product_id=product.id,movement_type="sale",quantity_base=-line.quantity,unit_cost=product.purchase_cost,reference_type="sale",reference_id=sale.id,created_by=user.id,transaction_date=posted_at,notes=None if cost_known else "Sale recorded before purchase cost was available"))
    if body.payment_mode=="customer_credit" and body.customer_id:
        db.add(LedgerEntry(business_id=business_id,party_type="customer",party_id=body.customer_id,entry_type="credit_sale",amount=sale.net,reference_id=sale.id,created_by=user.id,created_at=posted_at))
    db.flush()
    for product_id in {product.id for product,_ in resolved}:recalculate_product_sale_costs(db,business_id,product_id)
    profit_excluded_lines=int(db.scalar(select(func.count(SaleLine.id)).where(SaleLine.sale_id==sale.id,SaleLine.cost_known.is_(False))) or 0)
    audit(db,business_id,user,"post_by_name","sale",sale.id);db.commit()
    return {"id":sale.id,"net":sale.net,"created_products":created_count,"profit_excluded_lines":profit_excluded_lines}

def recalculate_product_sale_costs(db:Session,business_id:str,product_id:str)->None:
    """Attach the latest purchase cost available on each sale's date.

    Purchases and sales can be uploaded in any order. Replaying the small
    per-product cost timeline makes profit independent of upload order while
    ensuring a future purchase price is never applied to an earlier sale.
    """
    purchases=db.execute(
        select(Purchase.created_at,PurchaseLine.unit_cost)
        .join(PurchaseLine,PurchaseLine.purchase_id==Purchase.id)
        .where(Purchase.business_id==business_id,PurchaseLine.product_id==product_id)
        .order_by(Purchase.created_at,PurchaseLine.id)
    ).all()
    product=db.scalar(select(Product).where(Product.id==product_id,Product.business_id==business_id))
    if product:product.purchase_cost=float(purchases[-1].unit_cost) if purchases else 0
    sales=db.execute(
        select(SaleLine,Sale.created_at).join(Sale,Sale.id==SaleLine.sale_id)
        .where(SaleLine.business_id==business_id,SaleLine.product_id==product_id)
        .order_by(Sale.created_at,SaleLine.id)
    ).all()
    purchase_index=-1
    for line,sold_at in sales:
        while purchase_index+1<len(purchases) and purchases[purchase_index+1].created_at<=sold_at:purchase_index+=1
        if purchase_index>=0:
            line.unit_cost=float(purchases[purchase_index].unit_cost);line.cost_known=True
        else:
            line.unit_cost=0;line.cost_known=False

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
    db.flush()
    for product_id in {line.product_id for line in body.lines}:recalculate_product_sale_costs(db,business_id,product_id)
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

@router.get("/businesses/{business_id}/supplier-udhaar")
def supplier_udhaar(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id)
    credit_rows=db.execute(
        select(Purchase.supplier_id,func.max(Purchase.created_at).label("last_purchase"),
               func.sum(Purchase.total).label("udhaar_total"))
        .where(Purchase.business_id==business_id,Purchase.payment_mode=="supplier_credit")
        .group_by(Purchase.supplier_id)
    ).all()
    paid_rows=dict(db.execute(
        select(LedgerEntry.party_id,func.coalesce(-func.sum(LedgerEntry.amount),0))
        .where(LedgerEntry.business_id==business_id,LedgerEntry.party_type=="supplier",
               LedgerEntry.entry_type=="payment",LedgerEntry.amount<0)
        .group_by(LedgerEntry.party_id)
    ).all())
    suppliers={row.id:row.name for row in db.scalars(select(Supplier).where(Supplier.business_id==business_id))}
    return [{"supplier_id":row.supplier_id,"date":row.last_purchase,"dealer_name":suppliers.get(row.supplier_id,"Unknown dealer"),
             "udhaar_total":float(row.udhaar_total or 0),"amount_paid":min(float(row.udhaar_total or 0),float(paid_rows.get(row.supplier_id,0) or 0)),
             "amount_pending":max(0,float(row.udhaar_total or 0)-float(paid_rows.get(row.supplier_id,0) or 0))}
            for row in credit_rows]

@router.post("/businesses/{business_id}/supplier-payments",status_code=201)
def supplier_payment(business_id:str,body:SupplierPaymentRecordIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"})
    supplier=db.scalar(select(Supplier).where(Supplier.id==body.supplier_id,Supplier.business_id==business_id,Supplier.active.is_(True)))
    if not supplier:raise HTTPException(400,"Dealer was not found for this business.")
    total=float(db.scalar(select(func.coalesce(func.sum(Purchase.total),0)).where(
        Purchase.business_id==business_id,Purchase.supplier_id==supplier.id,Purchase.payment_mode=="supplier_credit")) or 0)
    paid=float(db.scalar(select(func.coalesce(-func.sum(LedgerEntry.amount),0)).where(
        LedgerEntry.business_id==business_id,LedgerEntry.party_type=="supplier",LedgerEntry.party_id==supplier.id,
        LedgerEntry.entry_type=="payment",LedgerEntry.amount<0)) or 0)
    pending=max(0,total-paid)
    if body.amount_paid>pending+0.001:raise HTTPException(422,f"Payment cannot exceed the pending amount of ₹{pending:g}.")
    posted_at=entry_datetime(body.payment_date)
    row=LedgerEntry(business_id=business_id,party_type="supplier",party_id=supplier.id,entry_type="payment",
                    amount=-body.amount_paid,created_by=user.id,created_at=posted_at)
    db.add(row);db.flush();audit(db,business_id,user,"post","supplier_payment",row.id);db.commit()
    return {"id":row.id,"date":posted_at,"dealer_name":supplier.name,"amount_paid":body.amount_paid,
            "udhaar_total":total,"amount_pending":pending-body.amount_paid}

@router.post("/businesses/{business_id}/udhaar-entries",status_code=201)
def add_udhaar_entry(business_id:str,body:UdhaarEntryIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id);require_starter(db,business_id)
    customer=db.scalar(select(Customer).where(Customer.id==body.customer_id,Customer.business_id==business_id,Customer.active.is_(True)))
    if not customer:raise HTTPException(400,"Customer was not found for this business.")
    posted_at=entry_datetime(body.entry_date)
    ledger_amounts=db.scalars(select(LedgerEntry.amount).where(LedgerEntry.business_id==business_id,LedgerEntry.party_type=="customer",LedgerEntry.party_id==customer.id)).all()
    previous_total=sum(float(value) for value in ledger_amounts if value>0)
    previous_paid=-sum(float(value) for value in ledger_amounts if value<0)
    total=body.amount if body.total_present else previous_total
    paid=body.given
    if total+0.001<previous_total:raise HTTPException(422,f"Total Udhaar cannot decrease. Existing total is ₹{previous_total:g}.")
    if paid+0.001<previous_paid:raise HTTPException(422,f"Total Paid cannot decrease. Existing paid is ₹{previous_paid:g}.")
    if paid>total+0.001:raise HTTPException(422,"Total Paid cannot be more than Total Udhaar.")
    row=UdhaarEntry(business_id=business_id,customer_id=customer.id,entry_date=posted_at,products=", ".join(x.strip() for x in body.products.split(",") if x.strip()),total_present=body.total_present,amount=total,given=paid,pending=total-paid,created_by=user.id)
    db.add(row);db.flush()
    credit_added=total-previous_total;payment_added=paid-previous_paid
    if credit_added:db.add(LedgerEntry(business_id=business_id,party_type="customer",party_id=customer.id,entry_type="credit_sale",amount=credit_added,reference_id=row.id,created_by=user.id,created_at=posted_at))
    if payment_added:db.add(LedgerEntry(business_id=business_id,party_type="customer",party_id=customer.id,entry_type="payment_received",amount=-payment_added,reference_id=row.id,created_by=user.id,created_at=posted_at))
    audit(db,business_id,user,"post","udhaar_entry",row.id);db.commit()
    return {"id":row.id,"date":row.entry_date,"customer_name":customer.name,"products":row.products,"amount":row.amount,"given":row.given,"pending":row.pending}

@router.get("/businesses/{business_id}/udhaar-entries")
def udhaar_entries(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id);require_starter(db,business_id)
    rows=db.execute(select(UdhaarEntry,Customer).join(Customer,Customer.id==UdhaarEntry.customer_id).where(UdhaarEntry.business_id==business_id).order_by(UdhaarEntry.entry_date.desc(),UdhaarEntry.created_at.desc()).limit(500)).all()
    return [{"id":row.id,"date":row.entry_date,"customer_id":row.customer_id,"customer_name":customer.name,"products":row.products,"total_present":row.total_present,"amount":row.amount,"given":row.given,"pending":row.pending} for row,customer in rows]
@router.get("/businesses/{business_id}/ledger")
def ledger(business_id:str,party_type:str="customer",user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id); rows=db.execute(select(LedgerEntry.party_id,func.sum(LedgerEntry.amount).label("balance"),func.max(LedgerEntry.created_at).label("last_activity")).where(LedgerEntry.business_id==business_id,LedgerEntry.party_type==party_type).group_by(LedgerEntry.party_id)).all(); names={x.id:x.name for x in db.scalars(select(Customer if party_type=="customer" else Supplier).where((Customer if party_type=="customer" else Supplier).business_id==business_id))}; return [{"party_id":r.party_id,"name":names.get(r.party_id,"Unknown"),"balance":r.balance,"last_activity":r.last_activity} for r in rows]
@router.get("/businesses/{business_id}/sales")
def sales_list(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)): member(db,user,business_id); return db.scalars(select(Sale).where(Sale.business_id==business_id).order_by(Sale.created_at.desc()).limit(100)).all()
@router.get("/businesses/{business_id}/sales-details")
def sales_details(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id); rows=db.execute(select(Sale,SaleLine,Product).join(SaleLine,SaleLine.sale_id==Sale.id).join(Product,Product.id==SaleLine.product_id).where(Sale.business_id==business_id).order_by(Sale.created_at.desc()).limit(300)).all()
    return [{"sale_id":s.id,"line_id":line.id,"date":s.created_at,"invoice_number":s.invoice_number,"payment_mode":s.payment_mode,"customer_id":s.customer_id,"product_name":p.name,"quantity":line.quantity,"total_price":line.net,"unit_price":line.unit_price,"cost_known":line.cost_known,"bought_price_per_unit":line.unit_cost if line.cost_known else None,"profit_per_unit":line.unit_price-line.unit_cost if line.cost_known else None,"profit_loss":line.net-line.quantity*line.unit_cost if line.cost_known else None} for s,line,p in rows]
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
    rows=db.execute(select(sale_day.label("date"),func.count(func.distinct(Sale.id)).label("transactions"),func.coalesce(func.sum(SaleLine.quantity),0).label("quantity_sold"),func.coalesce(func.sum(SaleLine.net),0).label("total_sales"),func.coalesce(func.sum(known_profit_expression()),0).label("profit"),func.coalesce(func.sum(case((func.lower(Sale.payment_mode)=="cash",SaleLine.net),else_=0)),0).label("cash_sales"),func.coalesce(func.sum(case((func.lower(Sale.payment_mode)=="upi",SaleLine.net),else_=0)),0).label("upi_sales"),func.coalesce(func.sum(case((func.lower(Sale.payment_mode)=="customer_credit",SaleLine.net),else_=0)),0).label("credit_sales")).outerjoin(SaleLine,SaleLine.sale_id==Sale.id).where(Sale.business_id==business_id).group_by(sale_day).order_by(sale_day.desc()).limit(90)).all()
    return [{"date":r.date,"transactions":r.transactions,"quantity_sold":r.quantity_sold,"total_sales":r.total_sales,"profit":r.profit,"cash_sales":r.cash_sales,"upi_sales":r.upi_sales,"credit_sales":r.credit_sales} for r in rows]

@router.get("/businesses/{business_id}/sales-products-daily")
def sales_products_daily(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id); sale_day=func.date(Sale.created_at)
    rows=db.execute(select(sale_day.label("date"),Product.id.label("product_id"),Product.name.label("product_name"),Product.base_unit.label("unit"),func.coalesce(func.sum(SaleLine.quantity),0).label("quantity_sold"),func.coalesce(func.sum(SaleLine.net),0).label("total_sales"),func.coalesce(func.sum(known_profit_expression()),0).label("profit")).join(SaleLine,SaleLine.sale_id==Sale.id).join(Product,Product.id==SaleLine.product_id).where(Sale.business_id==business_id).group_by(sale_day,Product.id,Product.name,Product.base_unit).order_by(sale_day.desc(),func.sum(SaleLine.net).desc()).limit(1000)).all()
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
        rows=db.scalars(select(UdhaarEntry).where(UdhaarEntry.business_id==business_id).order_by(UdhaarEntry.entry_date.desc(),UdhaarEntry.created_at.desc())).all()
        names={x.id:x.name for x in db.scalars(select(Customer).where(Customer.business_id==business_id))}
        latest={}
        for row in rows:
            latest.setdefault(row.customer_id,row)
        return [{"id":row.id,"date":row.entry_date.date(),"party_id":party_id,"party_name":names.get(party_id,"Unknown customer"),"amount":row.pending} for party_id,row in list(latest.items())[:300]]
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
    db.flush();recalculate_product_sale_costs(db,business_id,line.product_id)
    audit(db,business_id,user,"update","purchase",purchase.id);db.commit();return {"updated":True,"purchase_total":purchase.total,"purchase_id":purchase.id,"line_id":line.id,"payment_mode":purchase.payment_mode}
@router.delete("/businesses/{business_id}/purchases/{purchase_id}")
def delete_purchase(business_id:str,purchase_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"});purchase=db.scalar(select(Purchase).where(Purchase.id==purchase_id,Purchase.business_id==business_id))
    if not purchase:raise HTTPException(404,"Purchase not found")
    product_ids=set(db.scalars(select(PurchaseLine.product_id).where(PurchaseLine.business_id==business_id,PurchaseLine.purchase_id==purchase_id)))
    db.execute(delete(InventoryMovement).where(InventoryMovement.business_id==business_id,InventoryMovement.reference_id==purchase_id));db.execute(delete(LedgerEntry).where(LedgerEntry.business_id==business_id,LedgerEntry.reference_id==purchase_id));db.execute(delete(PurchaseLine).where(PurchaseLine.business_id==business_id,PurchaseLine.purchase_id==purchase_id));audit(db,business_id,user,"delete","purchase",purchase.id);db.delete(purchase);db.flush()
    for product_id in product_ids:recalculate_product_sale_costs(db,business_id,product_id)
    db.commit();return {"deleted":True}
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
    db.flush();recalculate_product_sale_costs(db,business_id,line.product_id)
    audit(db,business_id,user,"delete_line","purchase",purchase_id);db.commit();return {"deleted":True,"transaction_deleted":not bool(remaining)}
@router.get("/businesses/{business_id}/expenses")
def expense_list(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)): member(db,user,business_id); return db.scalars(select(Expense).where(Expense.business_id==business_id).order_by(Expense.created_at.desc()).limit(100)).all()
@router.post("/businesses/{business_id}/expenses",status_code=201)
def expense(business_id:str,body:ExpenseIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id); tenant_store(db,business_id,body.store_id); row=Expense(business_id=business_id,store_id=body.store_id,category=body.category,amount=body.amount,payment_method=body.payment_method,payee=body.payee,created_by=user.id,created_at=entry_datetime(body.transaction_date)); db.add(row); db.flush(); audit(db,business_id,user,"create","expense",row.id); db.commit(); return row

@router.get("/businesses/{business_id}/dashboard")
def dashboard(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    membership=member(db,user,business_id); today=now().date(); previous_day=today-timedelta(days=1); sales=db.scalar(select(func.coalesce(func.sum(Sale.net),0)).where(Sale.business_id==business_id)); today_sales=db.scalar(select(func.coalesce(func.sum(Sale.net),0)).where(Sale.business_id==business_id,func.date(Sale.created_at)==today)); today_cash=db.scalar(select(func.coalesce(func.sum(Sale.net),0)).where(Sale.business_id==business_id,func.date(Sale.created_at)==today,Sale.payment_mode=="cash")); today_transactions=db.scalar(select(func.count(Sale.id)).where(Sale.business_id==business_id,func.date(Sale.created_at)==today)); today_quantity=db.scalar(select(func.coalesce(func.sum(SaleLine.quantity),0)).join(Sale,Sale.id==SaleLine.sale_id).where(Sale.business_id==business_id,func.date(Sale.created_at)==today)); purchases=db.scalar(select(func.coalesce(func.sum(Purchase.total),0)).where(Purchase.business_id==business_id)); expenses=db.scalar(select(func.coalesce(func.sum(Expense.amount),0)).where(Expense.business_id==business_id)); profit_expression=known_profit_expression(); gp=db.scalar(select(func.coalesce(func.sum(profit_expression),0)).where(SaleLine.business_id==business_id)); today_profit=db.scalar(select(func.coalesce(func.sum(profit_expression),0)).join(Sale,Sale.id==SaleLine.sale_id).where(SaleLine.business_id==business_id,func.date(Sale.created_at)==today)); previous_day_profit=db.scalar(select(func.coalesce(func.sum(profit_expression),0)).join(Sale,Sale.id==SaleLine.sale_id).where(SaleLine.business_id==business_id,func.date(Sale.created_at)==previous_day)); inv=inventory(business_id,user,db); customer_balances=ledger(business_id,"customer",user,db);customer_due=sum(max(0,float(x["balance"])) for x in customer_balances);supplier_credit=db.scalar(select(func.coalesce(func.sum(Purchase.total),0)).where(Purchase.business_id==business_id,Purchase.payment_mode=="supplier_credit"));supplier_paid=db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount),0)).where(LedgerEntry.business_id==business_id,LedgerEntry.party_type=="supplier",LedgerEntry.entry_type=="payment"));supplier_due=max(0,supplier_credit+supplier_paid);result={"today_sales":today_sales,"today_cash":today_cash,"today_transactions":today_transactions,"today_quantity_sold":today_quantity,"net_sales":sales,"customer_outstanding":customer_due,"supplier_outstanding":supplier_due,"customer_due_count":sum(float(x["balance"])>0 for x in customer_balances),"low_stock_count":sum(x["status"]=="low" for x in inv),"out_of_stock_count":sum(x["status"]=="out_of_stock" for x in inv)}
    if membership.role.value in {"owner","manager"}:result.update({"total_purchases":purchases,"estimated_gross_profit":gp,"expenses":expenses,"estimated_operating_profit":gp-expenses,"today_profit":today_profit,"previous_day_profit":previous_day_profit,"inventory_value":sum(x["value"] for x in inv)})
    return result
@router.get("/businesses/{business_id}/reorder-list")
def reorder(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)): return [{**x,"suggested_quantity":max(0,x["reorder_level"]*2-x["stock"])} for x in inventory(business_id,user,db) if x["status"]!="healthy"]

def _analysis_period(db:Session,business_id:str,label:str,start:date,end:date)->dict:
    start_day,end_day=start,end
    sale_filter=(Sale.business_id==business_id,func.date(Sale.created_at)>=start_day,func.date(Sale.created_at)<=end_day)
    purchase_filter=(Purchase.business_id==business_id,func.date(Purchase.created_at)>=start_day,func.date(Purchase.created_at)<=end_day)
    expense_filter=(Expense.business_id==business_id,func.date(Expense.created_at)>=start_day,func.date(Expense.created_at)<=end_day)
    sales=float(db.scalar(select(func.coalesce(func.sum(Sale.net),0)).where(*sale_filter)) or 0)
    purchases=float(db.scalar(select(func.coalesce(func.sum(Purchase.total),0)).where(*purchase_filter)) or 0)
    expenses=float(db.scalar(select(func.coalesce(func.sum(Expense.amount),0)).where(*expense_filter)) or 0)
    quantity=float(db.scalar(select(func.coalesce(func.sum(SaleLine.quantity),0)).join(Sale,Sale.id==SaleLine.sale_id).where(*sale_filter)) or 0)
    profit=float(db.scalar(select(func.coalesce(func.sum(known_profit_expression()),0)).join(Sale,Sale.id==SaleLine.sale_id).where(*sale_filter)) or 0)
    profit_eligible_sales=float(db.scalar(select(func.coalesce(func.sum(case((SaleLine.cost_known.is_(True),SaleLine.net),else_=0.0)),0)).join(Sale,Sale.id==SaleLine.sale_id).where(*sale_filter)) or 0)
    transactions=int(db.scalar(select(func.count(Sale.id)).where(*sale_filter)) or 0)
    payment_rows=db.execute(select(Sale.payment_mode,func.coalesce(func.sum(Sale.net),0)).where(*sale_filter).group_by(Sale.payment_mode)).all()
    days=max(1,(end-start).days+1)
    return {"label":label,"from":start.isoformat(),"to":end.isoformat(),"recorded_days":days,"sales":round(sales,2),"quantity_sold":round(quantity,2),"sales_transactions":transactions,"purchases":round(purchases,2),"expenses":round(expenses,2),"gross_profit":round(profit,2),"profit_eligible_sales":round(profit_eligible_sales,2),"profit_excluded_sales":round(sales-profit_eligible_sales,2),"gross_margin_percent":round(profit/profit_eligible_sales*100,1) if profit_eligible_sales else 0,"average_daily_sales":round(sales/days,2),"payment_mix":{str(mode):round(float(amount),2) for mode,amount in payment_rows}}

def _business_analysis_context(db:Session,business_id:str,user:User)->dict:
    today=now().date(); week_start=today-timedelta(days=6); month_start=today.replace(day=1)
    periods=[
        _analysis_period(db,business_id,"daily",today,today),
        _analysis_period(db,business_id,"weekly",week_start,today),
        _analysis_period(db,business_id,"monthly",month_start,today),
    ]
    previous=[
        _analysis_period(db,business_id,"previous_daily",today-timedelta(days=1),today-timedelta(days=1)),
        _analysis_period(db,business_id,"previous_weekly",week_start-timedelta(days=7),week_start-timedelta(days=1)),
    ]
    top_rows=db.execute(
        select(Product.name,func.sum(SaleLine.quantity),func.sum(SaleLine.net),func.sum(known_profit_expression()))
        .join(SaleLine,SaleLine.product_id==Product.id).join(Sale,Sale.id==SaleLine.sale_id)
        .where(Product.business_id==business_id,Sale.business_id==business_id,func.date(Sale.created_at)>=month_start,func.date(Sale.created_at)<=today)
        .group_by(Product.id,Product.name).order_by(func.sum(SaleLine.net).desc()).limit(10)
    ).all()
    stock=inventory(business_id,user,db); low=[{"product":x["name"],"stock":round(float(x["stock"]),2),"unit":x["unit"],"status":x["status"]} for x in stock if x["status"]!="healthy"][:20]
    customer_balances=ledger(business_id,"customer",user,db)
    supplier_credit=float(db.scalar(select(func.coalesce(func.sum(Purchase.total),0)).where(Purchase.business_id==business_id,Purchase.payment_mode=="supplier_credit")) or 0)
    supplier_paid=abs(float(db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount),0)).where(LedgerEntry.business_id==business_id,LedgerEntry.party_type=="supplier",LedgerEntry.entry_type=="payment")) or 0))
    return {
        "as_of":today.isoformat(),
        "periods":periods,
        "comparison_periods":previous,
        "top_products_this_month":[{"product":name,"quantity":round(float(qty or 0),2),"sales":round(float(sales or 0),2),"gross_profit":round(float(profit or 0),2)} for name,qty,sales,profit in top_rows],
        "inventory":{"total_products":len(stock),"low_or_out_of_stock":low},
        "credit":{"customer_receivable":round(sum(max(0,float(x["balance"])) for x in customer_balances),2),"supplier_payable":round(max(0,supplier_credit-supplier_paid),2)},
        "data_quality_note":"Figures include only records entered and confirmed in this app.",
    }

@router.get("/businesses/{business_id}/analysis-summary")
async def analysis_summary(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"})
    result=_business_analysis_context(db,business_id,user)
    tracked_provider(business_id,user.id,"market_context").warm_market_context()
    return result

@router.post("/businesses/{business_id}/business-chat")
async def business_chat(business_id:str,body:BusinessChatIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"})
    context=_business_analysis_context(db,business_id,user)
    try: result=await tracked_provider(business_id,user.id,"business_chat").business_advice(body.question,context,body.history,body.language)
    except RuntimeError as exc: raise HTTPException(502,f"Business assistant could not answer: {exc}") from exc
    audit(db,business_id,user,"ask","business_assistant",str(uuid.uuid4()));db.commit()
    return {"summary":context,"response":result}

def _report_window(period:str)->tuple[date,date]:
    end=now().date()
    if period=="week": return end-timedelta(days=end.weekday()),end
    if period=="month": return end.replace(day=1),end
    if period=="year": return end.replace(month=1,day=1),end
    raise HTTPException(422,"Choose week, month or year.")

def _report_analytics(db:Session,business_id:str,user:User,period:str)->dict:
    start,end=_report_window(period); start_day,end_day=start.isoformat(),end.isoformat()
    rows=db.execute(
        select(Sale.created_at,Product.id,Product.name,Product.base_unit,Category.name,Sale.payment_mode,
               SaleLine.quantity,SaleLine.net,SaleLine.cost_known,known_profit_expression().label("profit"))
        .join(SaleLine,SaleLine.sale_id==Sale.id).join(Product,Product.id==SaleLine.product_id)
        .outerjoin(Category,Category.id==Product.category_id)
        .where(Sale.business_id==business_id,func.date(Sale.created_at)>=start,func.date(Sale.created_at)<=end)
    ).all()
    daily:dict[str,dict]={}; products:dict[str,dict]={}; categories:dict[str,dict]={}
    for created,pid,pname,unit,category,payment_mode,qty,net,cost_known,profit in rows:
        day=created.date().isoformat(); d=daily.setdefault(day,{"date":day,"sales":0.0,"profit":0.0,"profit_eligible_sales":0.0,"udhaar":0.0,"quantity":0.0})
        d["sales"]+=float(net);d["profit"]+=float(profit);d["profit_eligible_sales"]+=float(net) if cost_known else 0.0;d["quantity"]+=float(qty)
        if str(payment_mode).lower()=="customer_credit":d["udhaar"]+=float(net)
        p=products.setdefault(pid,{"product_id":pid,"name":pname,"unit":unit,"quantity":0.0,"sales":0.0,"profit":0.0,"profit_eligible_sales":0.0})
        p["quantity"]+=float(qty);p["sales"]+=float(net);p["profit"]+=float(profit);p["profit_eligible_sales"]+=float(net) if cost_known else 0.0
        cname=category or "Uncategorised";c=categories.setdefault(cname,{"name":cname,"quantity":0.0,"sales":0.0,"profit":0.0,"profit_eligible_sales":0.0})
        c["quantity"]+=float(qty);c["sales"]+=float(net);c["profit"]+=float(profit);c["profit_eligible_sales"]+=float(net) if cost_known else 0.0
    cursor=start
    while cursor<=end:
        key=cursor.isoformat();daily.setdefault(key,{"date":key,"sales":0.0,"profit":0.0,"profit_eligible_sales":0.0,"udhaar":0.0,"quantity":0.0});cursor+=timedelta(days=1)
    daily_rows=sorted(daily.values(),key=lambda x:x["date"])
    product_rows=sorted(products.values(),key=lambda x:(-x["quantity"],-x["sales"]))
    category_rows=sorted(categories.values(),key=lambda x:(-x["quantity"],-x["sales"]))
    total_sales=sum(x["sales"] for x in daily_rows);total_profit=sum(x["profit"] for x in daily_rows);profit_eligible_sales=sum(x["profit_eligible_sales"] for x in daily_rows);total_qty=sum(x["quantity"] for x in daily_rows)
    cumulative=0.0;pareto=[]
    for product in sorted(product_rows,key=lambda x:-x["sales"]):
        cumulative+=product["sales"];share=(product["sales"]/total_sales*100) if total_sales else 0
        pareto.append({**product,"sales_share_percent":round(share,1),"cumulative_percent":round(cumulative/total_sales*100,1) if total_sales else 0,"pareto_core":bool(total_sales and cumulative-product["sales"]<total_sales*.8)})
    stock=inventory(business_id,user,db);product_velocity={x["product_id"]:x["quantity"]/max(1,(end-start).days+1) for x in product_rows}
    restock=[]
    for item in stock:
        if item["status"]=="healthy":continue
        velocity=product_velocity.get(item["product_id"],0);suggested=max(0,float(item["reorder_level"])*2-float(item["stock"]),velocity*7-float(item["stock"]))
        restock.append({"product_id":item["product_id"],"name":item["name"],"status":item["status"],"stock":round(float(item["stock"]),2),"unit":item["unit"],"average_daily_sales":round(velocity,2),"suggested_quantity":round(suggested,2)})
    restock.sort(key=lambda x:(0 if x["status"]=="out_of_stock" else 1,-x["average_daily_sales"]))
    weekly:dict[str,dict]={};monthly:dict[str,dict]={}
    for row in daily_rows:
        d=date.fromisoformat(row["date"]);monday=d-timedelta(days=d.weekday());wk=weekly.setdefault(monday.isoformat(),{"week_start":monday.isoformat(),"sales":0.0,"profit":0.0,"udhaar":0.0,"quantity":0.0});month=monthly.setdefault(d.strftime("%Y-%m"),{"month":d.strftime("%Y-%m"),"sales":0.0,"profit":0.0,"udhaar":0.0,"quantity":0.0})
        for target in (wk,month):
            target["sales"]+=row["sales"];target["profit"]+=row["profit"];target["udhaar"]+=row["udhaar"];target["quantity"]+=row["quantity"]
    kpis={"total_sales":round(total_sales,2),"profit":round(total_profit,2),"profit_eligible_sales":round(profit_eligible_sales,2),"profit_excluded_sales":round(total_sales-profit_eligible_sales,2),"quantity_sold":round(total_qty,2),"gross_margin_percent":round(total_profit/profit_eligible_sales*100,1) if profit_eligible_sales else 0,
          "most_sold_product":product_rows[0] if product_rows else None,"least_sold_product":product_rows[-1] if product_rows else None,
          "most_sold_category":category_rows[0] if category_rows else None,"least_sold_category":category_rows[-1] if category_rows else None}
    immediate=[]
    if restock:immediate.append({"kind":"restock","data":restock[0],"priority":"high","title":f"Restock {restock[0]['name']}","reason":f"{restock[0]['stock']:g} {restock[0]['unit']} left; recent velocity {restock[0]['average_daily_sales']:g}/day.","action":f"Order about {restock[0]['suggested_quantity']:g} {restock[0]['unit']}."})
    if pareto:
        core=[x for x in pareto if x["pareto_core"]];immediate.append({"kind":"pareto","data":{"count":len(core)},"priority":"medium","title":"Protect your Pareto products","reason":f"{len(core)} product(s) generate roughly the first 80% of recorded sales.","action":"Keep these products visible and avoid stock-outs."})
    if product_rows:immediate.append({"kind":"slow_mover","data":product_rows[-1],"priority":"low","title":f"Review slow mover: {product_rows[-1]['name']}","reason":f"Only {product_rows[-1]['quantity']:g} {product_rows[-1]['unit']} sold in this period.","action":"Reduce reorder quantity or test a small promotion."})
    ai_summary={"period":period,"from":start_day,"to":end_day,"kpis":kpis,"daily_last_14":daily_rows[-14:],"weekly_summaries":list(weekly.values()),"monthly_summaries":list(monthly.values()),"top_products":product_rows[:8],"bottom_products":list(reversed(product_rows[-8:])),"category_rankings":category_rows,"pareto_products":[x for x in pareto if x["pareto_core"]],"restock":restock[:12]}
    monthly_series=[{"date":f"{row['month']}-01","sales":round(row["sales"],2),"profit":round(row["profit"],2),"udhaar":round(row["udhaar"],2),"quantity":round(row["quantity"],2)} for row in monthly.values()]
    trend_series=monthly_series if period=="year" else daily_rows
    return {"period":period,"from":start_day,"to":end_day,"series_granularity":"month" if period=="year" else "day","kpis":kpis,"sales_profit_series":trend_series,"top_products":product_rows[:8],"bottom_products":list(reversed(product_rows[-8:])),"categories":category_rows,"pareto":pareto,"restock":restock,"immediate_insights":immediate,"ai_summary":ai_summary}

@router.get("/businesses/{business_id}/reports/analytics")
def report_analytics(business_id:str,period:str="week",user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"});require_starter(db,business_id)
    return _report_analytics(db,business_id,user,period)

def _localized_report_fallback(report:dict,language:str)->dict:
    copy={
        "en":("Actions from your recorded business data","Live AI formatting was temporarily unavailable, so these actions were calculated directly from your summarized sales, stock and Pareto analysis."),
        "hi":("आपके दर्ज व्यवसाय डेटा से उपयोगी सुझाव","लाइव AI उत्तर अस्थायी रूप से उपलब्ध नहीं था, इसलिए ये सुझाव आपकी बिक्री, स्टॉक और पैरेटो विश्लेषण के सारांश से सीधे निकाले गए हैं।"),
        "mr":("तुमच्या नोंदवलेल्या व्यवसाय डेटावर आधारित कृती","लाइव्ह AI उत्तर तात्पुरते उपलब्ध नव्हते, म्हणून या कृती विक्री, साठा आणि पॅरेटो विश्लेषणाच्या सारांशातून थेट मोजल्या आहेत."),
        "gu":("તમારા નોંધાયેલા વ્યવસાય ડેટા પરથી ઉપયોગી પગલાં","લાઇવ AI જવાબ અસ્થાયી રીતે ઉપલબ્ધ ન હતો, તેથી આ પગલાં વેચાણ, સ્ટોક અને પેરેટો વિશ્લેષણના સારાંશ પરથી સીધા ગણવામાં આવ્યા છે."),
        "kn":("ನಿಮ್ಮ ದಾಖಲಾದ ವ್ಯಾಪಾರ ಮಾಹಿತಿಯಿಂದ ಉಪಯುಕ್ತ ಕ್ರಮಗಳು","ಲೈವ್ AI ಉತ್ತರ ತಾತ್ಕಾಲಿಕವಾಗಿ ಲಭ್ಯವಿರಲಿಲ್ಲ, ಆದ್ದರಿಂದ ಈ ಕ್ರಮಗಳನ್ನು ಮಾರಾಟ, ದಾಸ್ತಾನು ಮತ್ತು ಪಾರೆಟೊ ವಿಶ್ಲೇಷಣೆಯ ಸಾರಾಂಶದಿಂದ ನೇರವಾಗಿ ಲೆಕ್ಕಿಸಲಾಗಿದೆ."),
        "ta":("உங்கள் பதிவு செய்யப்பட்ட வணிகத் தரவிலிருந்து பயனுள்ள நடவடிக்கைகள்","நேரடி AI பதில் தற்காலிகமாக கிடைக்கவில்லை; எனவே இந்த நடவடிக்கைகள் விற்பனை, இருப்பு மற்றும் பாரெட்டோ பகுப்பாய்வு சுருக்கத்திலிருந்து நேரடியாக கணக்கிடப்பட்டன."),
    }
    actions=[]
    for item in report["immediate_insights"]:
        data=item.get("data",{});kind=item.get("kind")
        if language=="hi":
            values={"restock":(f"{data.get('name','')} फिर से मंगाएँ",f"{data.get('stock',0):g} {data.get('unit','')} बचा है; हाल की बिक्री गति {data.get('average_daily_sales',0):g} प्रति दिन है।",f"लगभग {data.get('suggested_quantity',0):g} {data.get('unit','')} मंगाएँ।"),"pareto":("अपने पैरेटो उत्पादों का स्टॉक बनाए रखें",f"{data.get('count',0)} उत्पाद दर्ज बिक्री का शुरुआती लगभग 80% बनाते हैं।","इन उत्पादों को आसानी से दिखने वाली जगह रखें और स्टॉक खत्म न होने दें।"),"slow_mover":(f"धीमी बिक्री वाले उत्पाद की समीक्षा करें: {data.get('name','')}",f"इस अवधि में केवल {data.get('quantity',0):g} {data.get('unit','')} बिके।","दोबारा मंगाने की मात्रा घटाएँ या छोटा प्रचार आज़माएँ।")}
        elif language=="mr":
            values={"restock":(f"{data.get('name','')} पुन्हा मागवा",f"{data.get('stock',0):g} {data.get('unit','')} शिल्लक; अलीकडील विक्री वेग {data.get('average_daily_sales',0):g} प्रतिदिन आहे.",f"सुमारे {data.get('suggested_quantity',0):g} {data.get('unit','')} मागवा."),"pareto":("तुमच्या पॅरेटो उत्पादनांचा साठा जपा",f"{data.get('count',0)} उत्पादने नोंदवलेल्या विक्रीच्या पहिल्या सुमारे 80% वाटा देतात.","ही उत्पादने सहज दिसतील अशी ठेवा आणि साठा संपू देऊ नका."),"slow_mover":(f"कमी विक्रीच्या उत्पादनाचा आढावा घ्या: {data.get('name','')}",f"या कालावधीत फक्त {data.get('quantity',0):g} {data.get('unit','')} विकले गेले.","पुनर्मागणीचे प्रमाण कमी करा किंवा छोटी जाहिरात करून पाहा.")}
        elif language=="gu":
            values={"restock":(f"{data.get('name','')} ફરી મંગાવો",f"{data.get('stock',0):g} {data.get('unit','')} બાકી છે; તાજેતરની વેચાણ ગતિ {data.get('average_daily_sales',0):g} પ્રતિ દિવસ છે.",f"લગભગ {data.get('suggested_quantity',0):g} {data.get('unit','')} મંગાવો."),"pareto":("તમારા પેરેટો ઉત્પાદનોનો સ્ટોક જાળવો",f"{data.get('count',0)} ઉત્પાદનો નોંધાયેલા વેચાણના શરૂઆતના આશરે 80% આપે છે.","આ ઉત્પાદનો સરળતાથી દેખાય તેમ રાખો અને સ્ટોક ખૂટવા ન દો."),"slow_mover":(f"ધીમા વેચાણવાળા ઉત્પાદનની સમીક્ષા કરો: {data.get('name','')}",f"આ સમયગાળામાં માત્ર {data.get('quantity',0):g} {data.get('unit','')} વેચાયા.","ફરી મંગાવવાની માત્રા ઘટાડો અથવા નાનું પ્રમોશન અજમાવો.")}
        elif language=="kn":
            values={"restock":(f"{data.get('name','')} ಮತ್ತೆ ತರಿಸಿ",f"{data.get('stock',0):g} {data.get('unit','')} ಉಳಿದಿದೆ; ಇತ್ತೀಚಿನ ಮಾರಾಟ ವೇಗ ದಿನಕ್ಕೆ {data.get('average_daily_sales',0):g}.",f"ಸುಮಾರು {data.get('suggested_quantity',0):g} {data.get('unit','')} ತರಿಸಿ."),"pareto":("ನಿಮ್ಮ ಪಾರೆಟೊ ಉತ್ಪನ್ನಗಳ ದಾಸ್ತಾನು ಕಾಪಾಡಿ",f"{data.get('count',0)} ಉತ್ಪನ್ನಗಳು ದಾಖಲಾದ ಮಾರಾಟದ ಮೊದಲ ಸುಮಾರು 80% ನೀಡುತ್ತವೆ.","ಈ ಉತ್ಪನ್ನಗಳನ್ನು ಸುಲಭವಾಗಿ ಕಾಣುವಂತೆ ಇಡಿ ಮತ್ತು ದಾಸ್ತಾನು ಖಾಲಿಯಾಗದಂತೆ ನೋಡಿಕೊಳ್ಳಿ."),"slow_mover":(f"ನಿಧಾನವಾಗಿ ಮಾರಾಟವಾಗುವ ಉತ್ಪನ್ನ ಪರಿಶೀಲಿಸಿ: {data.get('name','')}",f"ಈ ಅವಧಿಯಲ್ಲಿ ಕೇವಲ {data.get('quantity',0):g} {data.get('unit','')} ಮಾರಾಟವಾಗಿದೆ.","ಮರುಆರ್ಡರ್ ಪ್ರಮಾಣ ಕಡಿಮೆ ಮಾಡಿ ಅಥವಾ ಸಣ್ಣ ಪ್ರಚಾರ ಪ್ರಯತ್ನಿಸಿ.")}
        elif language=="ta":
            values={"restock":(f"{data.get('name','')} மீண்டும் வாங்குங்கள்",f"{data.get('stock',0):g} {data.get('unit','')} மீதம்; சமீபத்திய விற்பனை வேகம் நாளுக்கு {data.get('average_daily_sales',0):g}.",f"சுமார் {data.get('suggested_quantity',0):g} {data.get('unit','')} வாங்குங்கள்."),"pareto":("உங்கள் பாரெட்டோ பொருட்களின் இருப்பை பாதுகாக்கவும்",f"{data.get('count',0)} பொருட்கள் பதிவு செய்யப்பட்ட விற்பனையின் முதல் சுமார் 80% வழங்குகின்றன.","இந்த பொருட்களை எளிதாகத் தெரியும்படி வைத்து, இருப்பு தீராமல் கவனிக்கவும்."),"slow_mover":(f"மெதுவாக விற்கும் பொருளை மதிப்பாய்வு செய்யவும்: {data.get('name','')}",f"இந்த காலத்தில் {data.get('quantity',0):g} {data.get('unit','')} மட்டுமே விற்றது.","மறு கொள்முதல் அளவைக் குறைக்கவும் அல்லது சிறிய சலுகையை முயற்சிக்கவும்.")}
        else: values={}
        title,reason,next_step=values.get(kind,(item["title"],item["reason"],item["action"]))
        actions.append({"priority":item["priority"],"title":title,"reason":reason,"next_step":next_step})
    headline,summary=copy.get(language,copy["en"])
    return {"headline":headline,"summary":summary,"actions":actions,"risks":[],"opportunities":[],"sources":[]}

@router.post("/businesses/{business_id}/reports/insights")
async def report_ai_insights(business_id:str,body:ReportInsightIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"});require_starter(db,business_id);report=_report_analytics(db,business_id,user,body.period)
    try:
        insights=await tracked_provider(business_id,user.id,"report_insights").report_insights(report["ai_summary"],body.language);source="gemini"
    except RuntimeError:
        insights=_localized_report_fallback(report,body.language);source="calculated_fallback"
    return {"period":body.period,"insights":insights,"source":source}

@router.post("/businesses/{business_id}/images",status_code=202)
async def upload_image(business_id:str,store_id:str,document_type:str,file:UploadFile=File(...),user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id); tenant_store(db,business_id,store_id)
    if document_type not in {"sales","purchase","stock","udhaar","supplier_payment"}:raise HTTPException(422,"Choose Sales, Purchase, Stock, Udhaar or Supplier Payment for this image.")
    allowed={"image/jpeg","image/png","image/webp"};
    if file.content_type not in allowed: raise HTTPException(415,"Please upload a JPG, PNG or WebP image.")
    content=await file.read();
    if len(content)>10*1024*1024: raise HTTPException(413,"File exceeds 10 MB")
    signatures={"image/jpeg":content.startswith(b"\xff\xd8\xff"),"image/png":content.startswith(b"\x89PNG\r\n\x1a\n"),"image/webp":content.startswith(b"RIFF") and content[8:12]==b"WEBP"}
    if not signatures[file.content_type]:raise HTTPException(415,"This file does not appear to be a valid image. Take a new photo and try again.")
    folder=Path(settings.upload_dir)/business_id; await run_in_threadpool(folder.mkdir,parents=True,exist_ok=True); path=folder/f"{uuid.uuid4()}{Path(file.filename or 'upload').suffix.lower()}"; await run_in_threadpool(path.write_bytes,content)
    row=ImageDocument(business_id=business_id,store_id=store_id,uploaded_by=user.id,document_type=document_type,filename=file.filename or "upload",storage_path=str(path),mime_type=file.content_type,status="uploaded"); db.add(row); db.commit(); db.refresh(row); return {"id":row.id,"status":row.status}
@router.post("/businesses/{business_id}/images/{document_id}/extract")
async def extract_image(business_id:str,document_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id); row=db.scalar(select(ImageDocument).where(ImageDocument.id==document_id,ImageDocument.business_id==business_id));
    if not row: raise HTTPException(404,"Document not found")
    if row.status=="review_required":return {"id":row.id,"status":row.status,"data":row.structured_data}
    if row.status=="processing":raise HTTPException(409,"This image is already being processed.")
    storage_path,mime_type,document_type=row.storage_path,row.mime_type,row.document_type
    row.status="processing";db.commit()  # release the DB connection during the external AI request
    try: data=await tracked_provider(business_id,user.id,f"image_{document_type}").extract(storage_path,mime_type,document_type)
    except RuntimeError as exc:
        row=db.scalar(select(ImageDocument).where(ImageDocument.id==document_id,ImageDocument.business_id==business_id));row.status="failed"; db.commit(); raise HTTPException(502,f"AI extraction failed: {exc}") from exc
    except Exception as exc:
        row=db.scalar(select(ImageDocument).where(ImageDocument.id==document_id,ImageDocument.business_id==business_id));row.status="failed"; db.commit(); raise HTTPException(502,"AI extraction failed because Gemini could not be reached. Check your internet connection and try again.") from exc
    row=db.scalar(select(ImageDocument).where(ImageDocument.id==document_id,ImageDocument.business_id==business_id))
    row.structured_data=data; row.confidence=float(data.get("confidence",0)); row.status="review_required"; db.commit(); return {"id":row.id,"status":row.status,"data":data}
@router.post("/businesses/{business_id}/images/{document_id}/confirm")
def confirm_image(business_id:str,document_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member(db,user,business_id,{"owner","manager"}); row=db.scalar(select(ImageDocument).where(ImageDocument.id==document_id,ImageDocument.business_id==business_id));
    if not row or row.status!="review_required": raise HTTPException(409,"Document is not ready for confirmation")
    created_id=None
    if row.document_type=="sales":
        extracted=(row.structured_data or {}).get("rows",[]); lines=[]
        products=list(db.scalars(select(Product).where(Product.business_id==business_id,Product.active.is_(True))))
        for item in extracted:
            label=str(item.get("product_name","")).strip()
            if not label:raise HTTPException(409,"Review required: every row needs a product name")
            product,_=fast_resolve_sales_product(db,business_id,row.store_id,label,str(item.get("unit") or "") or None,products)
            qty=float(item.get("quantity",0)); price=float(item.get("total_price",0))/qty if qty and item.get("total_price") is not None else float(item.get("unit_price",0) or product.selling_price)
            if qty<=0: raise HTTPException(409,"Review required: all quantities must be positive")
            lines.append((product,qty,price))
        if not lines: raise HTTPException(409,"No confirmed sale rows")
        gross=sum(q*p for _,q,p in lines); sale=Sale(business_id=business_id,store_id=row.store_id,invoice_number=f"IMG-{row.id[:8]}",payment_mode="cash",gross=gross,discount=0,net=gross,created_by=user.id); db.add(sale); db.flush(); created_id=sale.id
        for product,qty,price in lines: db.add(SaleLine(business_id=business_id,sale_id=sale.id,product_id=product.id,quantity=qty,unit_price=price,unit_cost=product.purchase_cost,cost_known=product.purchase_cost>0,net=qty*price)); db.add(InventoryMovement(business_id=business_id,store_id=row.store_id,product_id=product.id,movement_type="sale",quantity_base=-qty,unit_cost=product.purchase_cost,reference_type="image_sale",reference_id=sale.id,created_by=user.id))
        db.flush()
        for product_id in {product.id for product,_,_ in lines}:recalculate_product_sale_costs(db,business_id,product_id)
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
    for r in rows:
        category=await resolve_product_category(db,business_id,r["name"],user.id)
        db.add(Product(business_id=business_id,store_id=store_id,code=r["code"],name=r["name"],category_id=category.id,base_unit=r["unit"],purchase_unit=r["unit"],selling_unit=r["unit"],selling_price=float(r["selling_price"]),purchase_cost=float(r["purchase_cost"]),reorder_level=float(r["reorder_level"])))
    audit(db,business_id,user,"import","products",str(uuid.uuid4())); db.commit(); return {"imported":len(rows),"errors":[]}

@router.get("/businesses/{business_id}/reports/daily-closing.pdf")
def daily_closing_pdf(business_id:str,report_date:date|None=None,user:User=Depends(current_user),db:Session=Depends(get_db)):
    from fastapi.responses import Response
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    member(db,user,business_id,{"owner","manager"}); report_day=report_date or now().date(); day=report_day.isoformat()
    sale_total=float(db.scalar(select(func.coalesce(func.sum(SaleLine.net),0)).join(Sale,Sale.id==SaleLine.sale_id).where(Sale.business_id==business_id,func.date(Sale.created_at)==report_day)) or 0)
    profit=float(db.scalar(select(func.coalesce(func.sum(known_profit_expression()),0)).join(Sale,Sale.id==SaleLine.sale_id).where(Sale.business_id==business_id,func.date(Sale.created_at)==report_day)) or 0)
    def sale_mode(mode:str):return float(db.scalar(select(func.coalesce(func.sum(SaleLine.net),0)).join(Sale,Sale.id==SaleLine.sale_id).where(Sale.business_id==business_id,func.date(Sale.created_at)==report_day,Sale.payment_mode==mode)) or 0)
    cash_sales,upi_sales,credit_sales=sale_mode("cash"),sale_mode("upi"),sale_mode("customer_credit")
    purchases=float(db.scalar(select(func.coalesce(func.sum(Purchase.total),0)).where(Purchase.business_id==business_id,func.date(Purchase.created_at)==report_day)) or 0)
    expenses=float(db.scalar(select(func.coalesce(func.sum(Expense.amount),0)).where(Expense.business_id==business_id,func.date(Expense.created_at)==report_day)) or 0)
    customer_received=abs(float(db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount),0)).where(LedgerEntry.business_id==business_id,LedgerEntry.party_type=="customer",LedgerEntry.entry_type=="payment_received",func.date(LedgerEntry.created_at)==report_day)) or 0))
    supplier_paid=abs(float(db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount),0)).where(LedgerEntry.business_id==business_id,LedgerEntry.party_type=="supplier",LedgerEntry.entry_type=="payment",func.date(LedgerEntry.created_at)==report_day)) or 0))
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
