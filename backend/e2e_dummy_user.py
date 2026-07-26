import json
import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "test_artifacts" / "dummy-user-9192345678"
REPORT = ARTIFACTS / "e2e-result.json"
client = TestClient(app)


def check(response, step):
    if response.status_code >= 400:
        raise RuntimeError(f"{step}: {response.status_code} {response.text}")
    return response.json()


def normalize(value):
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def staple(name):
    return bool(re.search(r"\b(sugar|chini|salt|namak|rice|chawal|atta|flour|dal|daal|pulse|pulses|lentil|lentils)\b", name, re.I))


def guarded(row):
    row = dict(row)
    name, unit = str(row.get("product_name", "")), str(row.get("unit", ""))
    if not staple(name):
        row["trade_form"] = "standard"
        return row
    supplied = str(row.get("trade_form", "")).lower()
    if supplied in {"packed", "loose"}:
        row["trade_form"] = supplied
        return row
    packed = re.search(r"\b(packet|packets|pkt|pouch|piece|pcs|bottle|box|bag|bags|packed)\b", f"{name} {unit}", re.I)
    brand = re.search(r"\b(tata|aashirvaad|ashirvaad|india gate|fortune|patanjali)\b", name, re.I)
    row["trade_form"] = "packed" if packed or brand else "loose"
    row["form_confidence"] = "confirmed" if packed or brand else "assumed"
    return row


def variant_name(row):
    name = str(row.get("product_name", "")).strip()
    if not staple(name) or row.get("trade_form") == "standard" or re.search(r"\b(loose|packed)\b", name, re.I):
        return name
    pack = ""
    if row.get("trade_form") == "packed" and float(row.get("pack_size", 0) or 0) > 0:
        pack = f" {row['pack_size']} {row.get('pack_unit') or row.get('unit') or 'kg'}"
    return f"{name} — {'Packed' if row.get('trade_form') == 'packed' else 'Loose'}{pack}"


auth = check(client.post("/api/auth/otp/verify", json={"mobile": "9192345678", "code": "123456", "name": "Test Shopkeeper"}), "create user")
headers = {"Authorization": f"Bearer {auth['access_token']}"}
businesses = check(client.get("/api/businesses", headers=headers), "list businesses")
if businesses:
    business = businesses[0]
else:
    business = check(client.post("/api/onboarding", headers=headers, json={
        "business_name": "Seven Day Test Kirana",
        "store_name": "Main Test Store",
        "city": "Indore",
        "state": "Madhya Pradesh",
        "pin_code": "452001",
        "preferred_language": "hinglish",
    }), "onboard")
business_id = business["id"]
store = check(client.get(f"/api/businesses/{business_id}/stores", headers=headers), "get store")[0]

vendor = check(client.post(f"/api/businesses/{business_id}/suppliers/resolve", headers=headers, json={"name": "Maa Annapurna Wholesale"}), "create vendor")["supplier"]

purchase_path = ARTIFACTS / "purchase-bill-2026-07-19.png"
with purchase_path.open("rb") as handle:
    document = check(client.post(
        f"/api/businesses/{business_id}/images?store_id={store['id']}&document_type=purchase",
        headers=headers,
        files={"file": (purchase_path.name, handle, "image/png")},
    ), "upload purchase image")
purchase_extraction = check(client.post(f"/api/businesses/{business_id}/images/{document['id']}/extract", headers=headers), "extract purchase image")

product_rows = [guarded(row) for row in purchase_extraction["data"].get("rows", [])]
purchase_lines = []
for row in product_rows:
    quantity = float(row.get("quantity", 0) or 0)
    total = float(row.get("total_price", 0) or row.get("amount", 0) or 0)
    if quantity <= 0 or total <= 0:
        raise RuntimeError(f"purchase extraction produced invalid row: {row}")
    label = variant_name(row)
    resolved = check(client.post(f"/api/businesses/{business_id}/products/resolve", headers=headers, json={
        "store_id": store["id"],
        "name": label,
        "unit": "packet" if row.get("trade_form") == "packed" else row.get("unit") or None,
        "mrp": float(row.get("mrp", 0) or 0),
        "price": 0,
    }), f"resolve purchase product {label}")["product"]
    purchase_lines.append({"product_id": resolved["id"], "quantity": quantity, "unit_price": total / quantity})

purchase = check(client.post(f"/api/businesses/{business_id}/purchases", headers=headers, json={
    "store_id": store["id"],
    "supplier_id": vendor["id"],
    "invoice_number": "TEST-PUR-001",
    "transaction_date": "2026-07-19",
    "paid": 5880,
    "lines": purchase_lines,
}), "post purchase")

sales_results = []
for path in sorted(ARTIFACTS.glob("daily-sales-*.png")):
    transaction_date = path.stem.removeprefix("daily-sales-")
    with path.open("rb") as handle:
        sales_document = check(client.post(
            f"/api/businesses/{business_id}/images?store_id={store['id']}&document_type=sales",
            headers=headers,
            files={"file": (path.name, handle, "image/png")},
        ), f"upload sales image {transaction_date}")
    extracted = check(client.post(f"/api/businesses/{business_id}/images/{sales_document['id']}/extract", headers=headers), f"extract sales image {transaction_date}")
    catalogue = check(client.get(f"/api/businesses/{business_id}/products", headers=headers), "refresh products")
    sale_lines = []
    for raw in extracted["data"].get("rows", []):
        row = guarded(raw)
        quantity = float(row.get("quantity", 0) or 0)
        total = float(row.get("total_price", 0) or row.get("amount", 0) or 0)
        label = variant_name(row)
        exact = next((product for product in catalogue if normalize(product["name"]) == normalize(label)), None)
        if not exact:
            matched = client.post(f"/api/businesses/{business_id}/products/match", headers=headers, json={"name": label})
            if matched.status_code < 400:
                exact = matched.json()["product"]
        if not exact:
            family_tokens = [token for token in normalize(label).split() if token not in {"loose", "packed", "kg", "g"}]
            exact = max(catalogue, key=lambda product: sum(token in normalize(product["name"]) for token in family_tokens))
        if quantity <= 0 or total <= 0:
            raise RuntimeError(f"sales extraction produced invalid row on {transaction_date}: {raw}")
        sale_lines.append({"product_id": exact["id"], "quantity": quantity, "unit_price": total / quantity})
    sale = check(client.post(f"/api/businesses/{business_id}/sales", headers=headers, json={
        "store_id": store["id"],
        "invoice_number": f"TEST-SALE-{transaction_date}",
        "transaction_date": transaction_date,
        "payment_mode": "cash",
        "lines": sale_lines,
    }), f"post sales {transaction_date}")
    sales_results.append({"date": transaction_date, "sale_id": sale["id"], "rows": len(sale_lines), "total": sale["net"]})

products = check(client.get(f"/api/businesses/{business_id}/products", headers=headers), "verify products")
inventory = check(client.get(f"/api/businesses/{business_id}/inventory", headers=headers), "verify inventory")
daily = check(client.get(f"/api/businesses/{business_id}/sales-daily", headers=headers), "verify daily sales")
dashboard = check(client.get(f"/api/businesses/{business_id}/dashboard", headers=headers), "verify dashboard")

result = {
    "mobile": "9192345678",
    "otp": "123456",
    "business_id": business_id,
    "store_id": store["id"],
    "purchase_id": purchase["id"],
    "purchase_extracted_rows": len(product_rows),
    "sales": sales_results,
    "products": [{"id": item["id"], "name": item["name"], "unit": item["selling_unit"]} for item in products],
    "inventory": inventory,
    "daily_sales": daily,
    "dashboard": dashboard,
}
REPORT.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
print(json.dumps({
    "business": business["name"],
    "products": len(products),
    "purchase_rows": len(product_rows),
    "sales_days": len(sales_results),
    "sales_rows": sum(item["rows"] for item in sales_results),
    "net_sales": dashboard.get("net_sales"),
    "inventory_rows": len(inventory),
    "report": str(REPORT),
}, indent=2))
