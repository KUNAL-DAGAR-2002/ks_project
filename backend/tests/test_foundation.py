import os
import uuid
os.environ["KIRANA_DATABASE_URL"] = "sqlite:///./test_kirana_saathi.db"
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def auth(mobile: str, name: str = "Demo Owner"):
    response = client.post("/api/auth/otp/verify", json={"mobile": mobile, "code": "123456", "name": name})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}

def test_health_and_otp_validation():
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.post("/api/auth/otp/request", json={"mobile": "123"}).status_code == 422
    assert client.post("/api/auth/otp/verify", json={"mobile": "9876543210", "code": "000000", "name": "Owner"}).status_code == 400

def test_login_name_is_updated_for_returning_user():
    mobile="9866666666"
    first=auth(mobile,"Old Name")
    second=auth(mobile,"New Name")
    assert client.get("/api/me",headers=second).json()["name"]=="New Name"

def test_explicit_login_and_signup_are_safe():
    mobile = "9" + str(uuid.uuid4().int)[:9]
    missing = client.post("/api/auth/otp/verify", json={
        "mobile": mobile, "code": "123456", "intent": "login"
    })
    assert missing.status_code == 404
    created = client.post("/api/auth/otp/verify", json={
        "mobile": mobile, "code": "123456", "name": "Signup Owner", "intent": "signup"
    })
    assert created.status_code == 200
    duplicate = client.post("/api/auth/otp/verify", json={
        "mobile": mobile, "code": "123456", "name": "Wrong Name", "intent": "signup"
    })
    assert duplicate.status_code == 409
    login = client.post("/api/auth/otp/verify", json={
        "mobile": mobile, "code": "123456", "intent": "login"
    })
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/api/me", headers=headers).json()["name"] == "Signup Owner"

def test_email_password_and_admin_authentication():
    email=f"owner-{uuid.uuid4().hex[:10]}@example.com"
    signup=client.post("/api/auth/signup",json={"email":email,"password":"StrongPass123!","name":"Email Owner","business_name":"Email Owner Store","city":"Delhi","pin_code":"110001"})
    assert signup.status_code==201
    assert client.post("/api/auth/signup",json={"email":email,"password":"StrongPass123!","name":"Again"}).status_code==409
    assert client.post("/api/auth/login",json={"email":email,"password":"wrong"}).status_code==401
    login=client.post("/api/auth/login",json={"email":email.upper(),"password":"StrongPass123!"})
    assert login.status_code==200
    me=client.get("/api/me",headers={"Authorization":f"Bearer {login.json()['access_token']}"}).json()
    assert me["email"]==email and "mobile" not in me
    businesses=client.get("/api/businesses",headers={"Authorization":f"Bearer {login.json()['access_token']}"}).json()
    assert len(businesses)==1 and businesses[0]["name"]=="Email Owner Store"
    assert client.post("/api/admin/login",json={"username":"id","password":"wrong"}).status_code==401
    admin=client.post("/api/admin/login",json={"username":"id","password":"root"})
    assert admin.status_code==200
    overview=client.get("/api/admin/overview",headers={"Authorization":f"Bearer {admin.json()['access_token']}"})
    assert overview.status_code==200 and "users" in overview.json() and "ai_tokens" in overview.json()["summary"]

def test_admin_can_manage_membership_and_delete_user():
    email=f"member-{uuid.uuid4().hex[:10]}@example.com"
    signup=client.post("/api/auth/signup",json={"email":email,"password":"StrongPass123!","name":"Plan Member","business_name":"Plan Test Store","city":"Delhi","pin_code":"110001"})
    user_headers={"Authorization":f"Bearer {signup.json()['access_token']}"}
    businesses=client.get("/api/businesses",headers=user_headers)
    assert businesses.status_code==200 and len(businesses.json())==1
    business_id=businesses.json()[0]["id"]
    initial=client.get(f"/api/businesses/{business_id}/subscription",headers=user_headers)
    assert initial.status_code==200 and initial.json()["access_active"] is False and initial.json()["can_start_trial"] is True
    trial=client.post(f"/api/businesses/{business_id}/subscription/trial",headers=user_headers)
    assert trial.status_code==200 and trial.json()["status"]=="trial" and trial.json()["days_remaining"]==7
    assert client.post(f"/api/businesses/{business_id}/subscription/trial",headers=user_headers).status_code==409
    admin=client.post("/api/admin/login",json={"username":"id","password":"root"}).json()
    admin_headers={"Authorization":f"Bearer {admin['access_token']}"}
    overview=client.get("/api/admin/overview",headers=admin_headers).json()
    user=next(row for row in overview["users"] if row["email"]==email)
    granted=client.post(f"/api/admin/users/{user['id']}/subscription",headers=admin_headers)
    assert granted.status_code==200
    refreshed=client.get("/api/admin/overview",headers=admin_headers).json()
    subscription=next(row for row in refreshed["users"] if row["id"]==user["id"])["businesses"][0]["subscription"]
    assert subscription["plan"]=="starter" and subscription["status"]=="active" and subscription["monthly_price"]==599
    assert subscription["access_active"] is True and subscription["days_remaining"]>=30
    assert client.delete(f"/api/admin/users/{user['id']}/subscription",headers=admin_headers).status_code==200
    revoked=client.get(f"/api/businesses/{business_id}/subscription",headers=user_headers).json()
    assert revoked["status"]=="ended" and revoked["access_active"] is False and revoked["can_start_trial"] is False
    assert client.delete(f"/api/admin/users/{user['id']}",headers=admin_headers).status_code==200
    assert client.post("/api/auth/login",json={"email":email,"password":"StrongPass123!"}).status_code==401

def test_onboarding_and_tenant_isolation():
    owner_a, owner_b = auth("9876543211"), auth("9876543212")
    payload = {"business_name":"Sharma Kirana","store_name":"Main Store","city":"Delhi","state":"Delhi","pin_code":"110001","preferred_language":"hinglish","working_style":["notebook"],"enabled_modules":["sales","inventory"]}
    created = client.post("/api/onboarding", json=payload, headers=owner_a)
    assert created.status_code in (201, 409)
    businesses = client.get("/api/businesses", headers=owner_a).json()
    assert businesses
    tenant_id = businesses[0]["id"]
    assert client.get(f"/api/businesses/{tenant_id}", headers=owner_b).status_code == 404
