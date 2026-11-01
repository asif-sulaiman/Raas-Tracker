"""Register-based product picker (company products seen on PIs/invoices)."""
import flask_app


def _company(admin_client, db, name="RegCo"):
    admin_client.post("/api/companies", json={"name": name})
    return db.execute(
        "SELECT id FROM companies WHERE name = %s", (name,)).fetchone()[0]


def _sale(admin_client, company_id, product="ProdX", pi="PI-R1"):
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": pi, "client_name": "x", "company_id": company_id},
        "items": [{"product_name": product, "quantity": 10,
                   "unit_price": 5}]})
    assert r.status_code == 201
    return r.get_json()["id"]


def test_register_products(admin_client, db, user_client):
    cid = _company(admin_client, db)
    _sale(admin_client, cid, product="ProdX", pi="PI-R1")
    r = admin_client.get(f"/api/register/products?company_id={cid}")
    assert r.status_code == 200
    rows = r.get_json()
    assert any(p["product_name"] == "ProdX" and p["pi_number"] == "PI-R1"
               for p in rows)
    assert user_client.get(
        f"/api/register/products?company_id={cid}").status_code == 200
    c = flask_app.app.test_client()
    assert c.get(
        f"/api/register/products?company_id={cid}").status_code == 401
    assert admin_client.get(
        "/api/register/products?company_id=999999").status_code == 400
    assert admin_client.get("/api/register/products").status_code == 400
