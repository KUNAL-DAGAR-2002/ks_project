import base64, time, uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.database import SessionLocal
from app.main import app
from app.models import BusinessUser, InventoryMovement, Product, Purchase, Role, Sale, Store, User

client=TestClient(app)

def account(name="QA Owner"):
    mobile="8"+str(uuid.uuid4().int)[:9]
    token=client.post("/api/auth/otp/verify",json={"mobile":mobile,"code":"123456","name":name}).json()["access_token"]
    headers={"Authorization":f"Bearer {token}"}
    business=client.post("/api/onboarding",headers=headers,json={"business_name":"QA Kirana","store_name":"Main","city":"Indore","state":"Madhya Pradesh","pin_code":"452001","preferred_language":"hinglish"}).json()
    store=client.get(f"/api/businesses/{business['id']}/stores",headers=headers).json()[0]
    return headers,business["id"],store["id"],mobile

def product(headers,bid,sid,name="QA Rice"):
    return client.post(f"/api/businesses/{bid}/products",headers=headers,json={"store_id":sid,"code":uuid.uuid4().hex[:8],"name":name,"base_unit":"kilogram","selling_unit":"kilogram","purchase_unit":"kilogram","selling_price":100,"purchase_cost":70,"reorder_level":2}).json()

def test_duplicate_sale_and_negative_stock_are_blocked_without_extra_movement():
    h,bid,sid,_=account(); p=product(h,bid,sid)
    client.post(f"/api/businesses/{bid}/inventory/movements",headers=h,json={"store_id":sid,"product_id":p["id"],"quantity":5,"unit_cost":70})
    body={"store_id":sid,"invoice_number":"SAME-SALE","payment_mode":"cash","lines":[{"product_id":p["id"],"quantity":2,"unit_price":100}]}
    assert client.post(f"/api/businesses/{bid}/sales",headers=h,json=body).status_code==201
    duplicate=client.post(f"/api/businesses/{bid}/sales",headers=h,json=body)
    assert duplicate.status_code==409 and "already saved" in duplicate.json()["detail"]
    too_many={**body,"invoice_number":"TOO-MANY","lines":[{"product_id":p["id"],"quantity":4,"unit_price":100}]}
    assert client.post(f"/api/businesses/{bid}/sales",headers=h,json=too_many).status_code==409
    inv=client.get(f"/api/businesses/{bid}/inventory",headers=h).json()[0]
    assert inv["stock"]==3

def test_tenant_references_and_duplicate_supplier_bill_are_blocked():
    h1,b1,s1,_=account(); h2,b2,s2,_=account(); p1=product(h1,b1,s1)
    supplier=client.post(f"/api/businesses/{b1}/suppliers",headers=h1,json={"name":"QA Vendor"}).json()
    assert client.post(f"/api/businesses/{b1}/inventory/movements",headers=h1,json={"store_id":s2,"product_id":p1["id"],"quantity":1}).status_code==400
    body={"store_id":s1,"supplier_id":supplier["id"],"invoice_number":"BILL-1","paid":0,"lines":[{"product_id":p1["id"],"quantity":2,"unit_price":60}]}
    assert client.post(f"/api/businesses/{b1}/purchases",headers=h1,json=body).status_code==201
    assert client.post(f"/api/businesses/{b1}/purchases",headers=h1,json=body).status_code==409
    assert client.get(f"/api/businesses/{b1}/inventory",headers=h2).status_code==404

def test_sales_and_purchases_accept_past_dates_and_reject_future_dates():
    h,bid,sid,_=account(); p=product(h,bid,sid); vendor=client.post(f"/api/businesses/{bid}/suppliers",headers=h,json={"name":"Past Date Vendor"}).json()
    past=(date.today()-timedelta(days=10)).isoformat()
    purchase=client.post(f"/api/businesses/{bid}/purchases",headers=h,json={"store_id":sid,"supplier_id":vendor["id"],"invoice_number":"PAST-P","transaction_date":past,"paid":0,"lines":[{"product_id":p["id"],"quantity":5,"unit_price":70}]})
    sale=client.post(f"/api/businesses/{bid}/sales",headers=h,json={"store_id":sid,"invoice_number":"PAST-S","transaction_date":past,"payment_mode":"cash","lines":[{"product_id":p["id"],"quantity":2,"unit_price":100}]})
    assert purchase.status_code==201 and sale.status_code==201
    with SessionLocal() as db:
        saved_purchase=db.scalar(select(Purchase).where(Purchase.id==purchase.json()["id"]))
        saved_sale=db.scalar(select(Sale).where(Sale.id==sale.json()["id"]))
        movement_dates=db.scalars(select(InventoryMovement.transaction_date).where(InventoryMovement.reference_id.in_([saved_purchase.id,saved_sale.id]))).all()
        assert saved_purchase.created_at.date().isoformat()==past
        assert saved_sale.created_at.date().isoformat()==past
        assert all(value.date().isoformat()==past for value in movement_dates)
    future=(date.today()+timedelta(days=1)).isoformat()
    rejected=client.post(f"/api/businesses/{bid}/sales",headers=h,json={"store_id":sid,"invoice_number":"FUTURE-S","transaction_date":future,"payment_mode":"cash","lines":[{"product_id":p["id"],"quantity":1,"unit_price":100}]})
    assert rejected.status_code==422 and "future" in rejected.json()["detail"].lower()

def test_upload_validation_and_staff_profit_privacy():
    h,bid,sid,_=account();
    fake=client.post(f"/api/businesses/{bid}/images?store_id={sid}&document_type=sales",headers=h,files={"file":("fake.png",b"not an image","image/png")})
    assert fake.status_code==415
    staff_mobile="7"+str(uuid.uuid4().int)[:9]
    staff_token=client.post("/api/auth/otp/verify",json={"mobile":staff_mobile,"code":"123456","name":"QA Staff"}).json()["access_token"]
    with SessionLocal() as db:
        staff=db.scalar(select(User).where(User.mobile==staff_mobile)); db.add(BusinessUser(business_id=bid,user_id=staff.id,role=Role.STAFF)); db.commit()
    staff_h={"Authorization":f"Bearer {staff_token}"}
    dashboard=client.get(f"/api/businesses/{bid}/dashboard",headers=staff_h)
    assert dashboard.status_code==200 and "estimated_operating_profit" not in dashboard.json()
    assert client.post(f"/api/businesses/{bid}/suppliers",headers=staff_h,json={"name":"Blocked Vendor"}).status_code==403

def test_image_confirmation_is_idempotent_and_posts_once():
    h,bid,sid,_=account(); p=product(h,bid,sid,"Image Test Atta")
    client.post(f"/api/businesses/{bid}/inventory/movements",headers=h,json={"store_id":sid,"product_id":p["id"],"quantity":10,"unit_cost":70})
    png=base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z4uUAAAAASUVORK5CYII=")
    uploaded=client.post(f"/api/businesses/{bid}/images?store_id={sid}&document_type=sales",headers=h,files={"file":("sale.png",png,"image/png")})
    assert uploaded.status_code==202
    extracted={"rows":[{"product_name":"Image Test Atta","quantity":2,"total_price":200,"confidence":.95}],"confidence":.95}
    with patch("app.operations.GeminiProvider.extract",new=AsyncMock(return_value=extracted)):
        assert client.post(f"/api/businesses/{bid}/images/{uploaded.json()['id']}/extract",headers=h).status_code==200
    first=client.post(f"/api/businesses/{bid}/images/{uploaded.json()['id']}/confirm",headers=h)
    second=client.post(f"/api/businesses/{bid}/images/{uploaded.json()['id']}/confirm",headers=h)
    assert first.status_code==200 and second.status_code==409
    assert client.get(f"/api/businesses/{bid}/inventory",headers=h).json()[0]["stock"]==8

def test_300_product_catalogue_remains_responsive():
    h,bid,sid,_=account()
    with SessionLocal() as db:
        db.add_all([Product(business_id=bid,store_id=sid,code=f"LOAD{i:03}",name=f"Load Product {i:03}",base_unit="piece",purchase_unit="piece",selling_unit="piece",active=True) for i in range(300)]);db.commit()
    started=time.perf_counter(); response=client.get(f"/api/businesses/{bid}/products",headers=h);elapsed=time.perf_counter()-started
    assert response.status_code==200 and len(response.json())==300
    assert elapsed<2.0

def test_ai_spelling_match_uses_only_existing_product():
    h,bid,sid,_=account(); existing=product(h,bid,sid,"Aashirvaad Atta 5 kg")
    decision={"match_id":existing["id"],"confidence":.96,"reason":"Brand spelling and pack size match."}
    with patch("app.operations.GeminiProvider.match_existing_product",new=AsyncMock(return_value=decision)) as matcher:
        response=client.post(f"/api/businesses/{bid}/products/match",headers=h,json={"name":"Ashirvaad Atta 5kg"})
    assert response.status_code==200
    assert response.json()["product"]["id"]==existing["id"]
    assert response.json()["matched_by"]=="ai_spelling"
    assert matcher.await_count==1
    assert len(client.get(f"/api/businesses/{bid}/products",headers=h).json())==1

def test_ai_product_match_rejects_invented_candidate():
    h,bid,sid,_=account(); product(h,bid,sid,"Parle G 100 g")
    decision={"match_id":"invented-id","confidence":.99,"reason":"Wrong candidate"}
    with patch("app.operations.GeminiProvider.match_existing_product",new=AsyncMock(return_value=decision)):
        response=client.post(f"/api/businesses/{bid}/products/match",headers=h,json={"name":"Completely Different Product"})
    assert response.status_code==409

def test_archived_product_is_reactivated_when_purchased_again():
    h,bid,sid,_=account(); existing=product(h,bid,sid,"Aashirvaad Atta 5 kg")
    assert client.delete(f"/api/businesses/{bid}/inventory/{existing['id']}",headers=h).status_code==200
    resolved=client.post(f"/api/businesses/{bid}/products/resolve",headers=h,json={"store_id":sid,"name":"Aashirvaad Atta 5 kg","unit":"bag","price":0})
    assert resolved.status_code==200 and resolved.json()["product"]["id"]==existing["id"]
    assert any(p["id"]==existing["id"] for p in client.get(f"/api/businesses/{bid}/products",headers=h).json())
