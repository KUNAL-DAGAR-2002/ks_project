import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "test_artifacts" / "dummy-user-9192345678"
client = TestClient(app)


def check(response, step):
    if response.status_code >= 400:
        raise RuntimeError(f"{step}: {response.status_code} {response.text}")
    return response.json()


token = check(client.post("/api/auth/otp/verify", json={"mobile": "9192345678", "code": "123456", "name": "Test Shopkeeper"}), "login")["access_token"]
headers = {"Authorization": f"Bearer {token}"}
business = check(client.get("/api/businesses", headers=headers), "business")[0]
business_id = business["id"]
store = check(client.get(f"/api/businesses/{business_id}/stores", headers=headers), "store")[0]
products = check(client.get(f"/api/businesses/{business_id}/products", headers=headers), "products")
existing_sales = {row["invoice_number"] for row in check(client.get(f"/api/businesses/{business_id}/sales", headers=headers), "sales")}


def product(fragment):
    return next(row for row in products if fragment.casefold() in row["name"].casefold())


sales_results = []
for index, path in enumerate(sorted(ARTIFACTS.glob("daily-sales-*.png"))):
    transaction_date = path.stem.removeprefix("daily-sales-")
    invoice = f"TEST-SALE-{transaction_date}"
    with path.open("rb") as handle:
        uploaded = check(client.post(
            f"/api/businesses/{business_id}/images?store_id={store['id']}&document_type=sales",
            headers=headers,
            files={"file": (path.name, handle, "image/png")},
        ), f"upload {path.name}")

    rows = [
        (product("Sugar"), 2, 100 + index * 2),
        (product("Tata Salt"), 1, 30),
        (product("Toor Dal"), 1, 135 + index),
        (product("Parle-G"), 3, 36),
    ]
    if index % 2 == 0:
        rows.append((product("India Gate Rice"), 1, 450))

    if invoice in existing_sales:
        sales_results.append({"date": transaction_date, "status": "already_posted", "image_document_id": uploaded["id"]})
        continue
    sale = check(client.post(f"/api/businesses/{business_id}/sales", headers=headers, json={
        "store_id": store["id"],
        "invoice_number": invoice,
        "transaction_date": transaction_date,
        "payment_mode": "cash",
        "lines": [{"product_id": item["id"], "quantity": quantity, "unit_price": total / quantity} for item, quantity, total in rows],
    }), f"post {invoice}")
    sales_results.append({"date": transaction_date, "status": "posted", "sale_id": sale["id"], "image_document_id": uploaded["id"], "total": sale["net"]})

inventory = check(client.get(f"/api/businesses/{business_id}/inventory", headers=headers), "inventory")
daily_sales = check(client.get(f"/api/businesses/{business_id}/sales-daily", headers=headers), "daily sales")
dashboard = check(client.get(f"/api/businesses/{business_id}/dashboard", headers=headers), "dashboard")
result = {
    "mobile": "9192345678",
    "otp": "123456",
    "business": business["name"],
    "sales_images_uploaded": len(sales_results),
    "sales_results": sales_results,
    "products": [{"id": row["id"], "name": row["name"], "unit": row["selling_unit"]} for row in products],
    "inventory": inventory,
    "daily_sales": daily_sales,
    "dashboard": dashboard,
    "ai_sales_extraction": "blocked_by_gemini_free_tier_quota; fixture rows used to complete downstream E2E checks",
}
(ARTIFACTS / "e2e-result.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
print(json.dumps({
    "sales_images_uploaded": len(sales_results),
    "daily_sales_days": len(daily_sales),
    "net_sales": dashboard["net_sales"],
    "inventory_rows": len(inventory),
    "result": str(ARTIFACTS / "e2e-result.json"),
}, indent=2))
