import os, uuid
from unittest.mock import AsyncMock, patch
os.environ["KIRANA_DATABASE_URL"]="sqlite:///./test_e2e.db"
from fastapi.testclient import TestClient
from app.main import app
client=TestClient(app)
def token():
    m="9"+str(uuid.uuid4().int)[:9]; r=client.post("/api/auth/otp/verify",json={"mobile":m,"code":"123456","name":"E2E Owner"}); return r.json()["access_token"]
def test_complete_manual_business_flow():
    h={"Authorization":f"Bearer {token()}"}; me=client.get("/api/me",headers=h); assert me.status_code==200 and me.json()["name"]=="E2E Owner"
    onboard=client.post("/api/onboarding",headers=h,json={"business_name":"E2E Kirana","store_name":"Main","city":"Delhi","state":"Delhi","pin_code":"110001","preferred_language":"hinglish"}); assert onboard.status_code==201
    bid=onboard.json()["id"]
    assert client.get(f"/api/businesses/{bid}/products",headers=h).json()==[]
    assert client.get(f"/api/businesses/{bid}/inventory",headers=h).json()==[]
    # Discover store through DB-independent current foundation omission is handled via API model internals for test.
    from app.database import SessionLocal
    from app.models import Store
    from sqlalchemy import select
    with SessionLocal() as db: sid=db.scalar(select(Store.id).where(Store.business_id==bid))
    researched={"selling_unit":"litre","acceptable_units":["litre"],"reasoning":"Milk is commonly retailed by volume.","sources":["https://example.test/milk"]}
    milk_name=f"Fresh Milk {uuid.uuid4().hex[:8]}"
    with patch("app.operations.GeminiProvider.research_indian_kirana_unit",new=AsyncMock(return_value=researched)) as lookup:
        milk=client.post(f"/api/businesses/{bid}/products/resolve",headers=h,json={"store_id":sid,"name":milk_name,"price":60}); assert milk.status_code==200
        assert milk.json()["unit_knowledge"]["acceptable_units"]==["litre","kilogram"]
        again=client.post(f"/api/businesses/{bid}/products/resolve",headers=h,json={"store_id":sid,"name":milk_name,"price":60}); assert again.status_code==200 and not again.json()["created"]
        assert lookup.await_count==1
    cat=client.post(f"/api/businesses/{bid}/categories",headers=h,json={"name":"Rice"}); assert cat.status_code==201
    sup=client.post(f"/api/businesses/{bid}/suppliers",headers=h,json={"name":"Delhi Distributor","mobile":"9876543000"}).json()
    cust=client.post(f"/api/businesses/{bid}/customers",headers=h,json={"name":"Ramesh","mobile":"9876543001"}).json()
    prod=client.post(f"/api/businesses/{bid}/products",headers=h,json={"store_id":sid,"code":"RICE01","name":"Basmati Rice 1 kg","category_id":cat.json()["id"],"supplier_id":sup["id"],"selling_price":120,"purchase_cost":90,"reorder_level":5,"aliases":["Rice 1kg"]}); assert prod.status_code==201; pid=prod.json()["id"]
    assert client.post(f"/api/businesses/{bid}/inventory/movements",headers=h,json={"store_id":sid,"product_id":pid,"quantity":20,"unit_cost":90}).status_code==201
    sale=client.post(f"/api/businesses/{bid}/sales",headers=h,json={"store_id":sid,"invoice_number":"S-1","payment_mode":"customer_credit","customer_id":cust["id"],"lines":[{"product_id":pid,"quantity":3,"unit_price":120}]}); assert sale.status_code==201
    daily=client.get(f"/api/businesses/{bid}/sales-daily",headers=h); assert daily.status_code==200 and daily.json()[0]["total_sales"]==360 and daily.json()[0]["transactions"]==1
    purchase=client.post(f"/api/businesses/{bid}/purchases",headers=h,json={"store_id":sid,"supplier_id":sup["id"],"invoice_number":"P-1","payment_mode":"supplier_credit","paid":0,"lines":[{"product_id":pid,"quantity":10,"unit_price":92}]}); assert purchase.status_code==201
    supplier_due=client.get(f"/api/businesses/{bid}/supplier-udhaar",headers=h).json()
    assert len(supplier_due)==1 and supplier_due[0]["udhaar_total"]==920 and supplier_due[0]["amount_pending"]==920
    paid=client.post(f"/api/businesses/{bid}/supplier-payments",headers=h,json={"supplier_id":sup["id"],"amount_paid":300})
    assert paid.status_code==201 and paid.json()["amount_pending"]==620
    supplier_due=client.get(f"/api/businesses/{bid}/supplier-udhaar",headers=h).json()
    assert supplier_due[0]["amount_paid"]==300 and supplier_due[0]["amount_pending"]==620
    assert client.post(f"/api/businesses/{bid}/supplier-payments",headers=h,json={"supplier_id":sup["id"],"amount_paid":621}).status_code==422
    assert client.post(f"/api/businesses/{bid}/expenses",headers=h,json={"store_id":sid,"category":"Electricity","amount":250,"payment_method":"cash"}).status_code==201
    inv=client.get(f"/api/businesses/{bid}/inventory",headers=h).json(); assert next(x for x in inv if x["product_id"]==pid)["stock"]==27
    dash=client.get(f"/api/businesses/{bid}/dashboard",headers=h); assert dash.status_code==200; assert dash.json()["net_sales"]==360 and dash.json()["today_sales"]==360 and dash.json()["today_transactions"]==1
    report=client.get(f"/api/businesses/{bid}/reports/daily.csv",headers=h); assert report.status_code==200 and "net_sales" in report.text
    sale_detail=client.get(f"/api/businesses/{bid}/sales-details",headers=h).json()[0]
    assert client.patch(f"/api/businesses/{bid}/sales/{sale_detail['sale_id']}/lines/{sale_detail['line_id']}",headers=h,json={"quantity":4,"total_price":480}).status_code==200
    purchase_detail=client.get(f"/api/businesses/{bid}/purchases-details",headers=h).json()[0]
    assert purchase_detail["vendor_id"]==sup["id"]
    assert client.patch(f"/api/businesses/{bid}/purchases/{purchase_detail['purchase_id']}/lines/{purchase_detail['line_id']}",headers=h,json={"quantity":12,"total_price":1104}).status_code==200
    assert next(x for x in client.get(f"/api/businesses/{bid}/inventory",headers=h).json() if x["product_id"]==pid)["stock"]==28
    assert client.delete(f"/api/businesses/{bid}/purchases/{purchase_detail['purchase_id']}",headers=h).status_code==200
    assert client.delete(f"/api/businesses/{bid}/sales/{sale_detail['sale_id']}",headers=h).status_code==200
    assert next(x for x in client.get(f"/api/businesses/{bid}/inventory",headers=h).json() if x["product_id"]==pid)["stock"]==20
    milk_id=milk.json()["product"]["id"]
    assert client.post(f"/api/businesses/{bid}/inventory/movements",headers=h,json={"store_id":sid,"product_id":milk_id,"quantity":5,"unit_cost":40}).status_code==201
    multi=client.post(f"/api/businesses/{bid}/sales",headers=h,json={"store_id":sid,"invoice_number":"S-MULTI","payment_mode":"cash","lines":[{"product_id":pid,"quantity":2,"unit_price":120},{"product_id":milk_id,"quantity":1,"unit_price":60}]}); assert multi.status_code==201
    multi_rows=[x for x in client.get(f"/api/businesses/{bid}/sales-details",headers=h).json() if x["sale_id"]==multi.json()["id"]]; assert len(multi_rows)==2
    rice_row=next(x for x in multi_rows if x["product_name"]=="Basmati Rice 1 kg")
    deleted=client.delete(f"/api/businesses/{bid}/sales/{rice_row['sale_id']}/lines/{rice_row['line_id']}",headers=h); assert deleted.status_code==200 and not deleted.json()["transaction_deleted"]
    remaining=[x for x in client.get(f"/api/businesses/{bid}/sales-details",headers=h).json() if x["sale_id"]==multi.json()["id"]]; assert len(remaining)==1 and remaining[0]["product_name"]==milk_name
    assert client.delete(f"/api/businesses/{bid}/sales/{remaining[0]['sale_id']}/lines/{remaining[0]['line_id']}",headers=h).json()["transaction_deleted"]
    assert client.patch(f"/api/businesses/{bid}/inventory/{pid}",headers=h,json={"closing_stock":25,"notes":"count correction"}).status_code==200
    assert next(x for x in client.get(f"/api/businesses/{bid}/inventory",headers=h).json() if x["product_id"]==pid)["closing"]==25
    assert client.delete(f"/api/businesses/{bid}/inventory/{milk_id}",headers=h).status_code==200
    assert milk_id not in {x["product_id"] for x in client.get(f"/api/businesses/{bid}/inventory",headers=h).json()}
    bad=client.post(f"/api/businesses/{bid}/imports/products?store_id={sid}",headers=h,files={"file":("bad.csv",b"wrong,data\n1,2","text/csv")}); assert bad.status_code==422


def test_daily_sale_creates_product_and_backfills_profit_when_purchase_arrives():
    h={"Authorization":f"Bearer {token()}"}
    business=client.post("/api/onboarding",headers=h,json={"business_name":"Fast Sales Kirana","store_name":"Main","city":"Delhi","state":"Delhi","pin_code":"110001","preferred_language":"en"}).json()
    bid=business["id"]
    from app.database import SessionLocal
    from app.models import Store
    from sqlalchemy import select
    with SessionLocal() as db:sid=db.scalar(select(Store.id).where(Store.business_id==bid))
    product_name=f"Direct Sale Item {uuid.uuid4().hex[:8]}"
    first=client.post(f"/api/businesses/{bid}/sales/by-name",headers=h,json={"store_id":sid,"invoice_number":"DIRECT-1","payment_mode":"cash","lines":[{"name":product_name,"quantity":2,"total_price":100,"unit":"packet"}]})
    assert first.status_code==201 and first.json()["created_products"]==1 and first.json()["profit_excluded_lines"]==1
    products=client.get(f"/api/businesses/{bid}/products",headers=h).json();product=next(row for row in products if row["name"]==product_name)
    detail=next(row for row in client.get(f"/api/businesses/{bid}/sales-details",headers=h).json() if row["product_name"]==product_name)
    assert detail["cost_known"] is False and detail["profit_loss"] is None
    supplier=client.post(f"/api/businesses/{bid}/suppliers",headers=h,json={"name":"Fast Supplier"}).json()
    purchase=client.post(f"/api/businesses/{bid}/purchases",headers=h,json={"store_id":sid,"supplier_id":supplier["id"],"invoice_number":"COST-1","payment_mode":"cash","lines":[{"product_id":product["id"],"quantity":5,"unit_price":30}]})
    assert purchase.status_code==201
    detail=next(row for row in client.get(f"/api/businesses/{bid}/sales-details",headers=h).json() if row["product_name"]==product_name)
    assert detail["cost_known"] is True and detail["profit_loss"]==40
    second=client.post(f"/api/businesses/{bid}/sales/by-name",headers=h,json={"store_id":sid,"invoice_number":"DIRECT-2","payment_mode":"upi","lines":[{"name":product_name,"quantity":2,"total_price":80,"unit":"packet"}]})
    assert second.status_code==201 and second.json()["created_products"]==0 and second.json()["profit_excluded_lines"]==0
    daily=client.get(f"/api/businesses/{bid}/sales-daily",headers=h).json()[0]
    assert daily["total_sales"]==180 and daily["profit"]==60
