import os
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

def test_onboarding_and_tenant_isolation():
    owner_a, owner_b = auth("9876543211"), auth("9876543212")
    payload = {"business_name":"Sharma Kirana","store_name":"Main Store","city":"Delhi","state":"Delhi","pin_code":"110001","preferred_language":"hinglish","working_style":["notebook"],"enabled_modules":["sales","inventory"]}
    created = client.post("/api/onboarding", json=payload, headers=owner_a)
    assert created.status_code in (201, 409)
    businesses = client.get("/api/businesses", headers=owner_a).json()
    assert businesses
    tenant_id = businesses[0]["id"]
    assert client.get(f"/api/businesses/{tenant_id}", headers=owner_b).status_code == 404
