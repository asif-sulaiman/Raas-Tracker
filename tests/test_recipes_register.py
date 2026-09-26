"""P2: register-based master recipes (company x product, no auto-create)."""
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


def _recipe(admin_client, name, cid, product, **kw):
    payload = {"name": name, "yield": kw.get("yield", 100),
               "company_id": cid, "product_name": product}
    return admin_client.post("/api/recipes", json=payload)


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


def test_create_master_recipe(admin_client, db):
    cid = _company(admin_client, db)
    _sale(admin_client, cid)
    r = _recipe(admin_client, "ProdX -- RegCo", cid, "ProdX")
    assert r.status_code == 201
    row = db.execute(
        "SELECT company_id, product_name FROM recipes "
        "WHERE name = 'ProdX -- RegCo'").fetchone()
    assert (row[0], row[1]) == (cid, "ProdX")
    r2 = _recipe(admin_client, "ProdX -- RegCo", cid, "ProdX")
    assert r2.status_code == 409
    body = r2.get_json()
    assert "already exists" in body["error"]
    assert body["existing"]["name"] == "ProdX -- RegCo"


def test_create_recipe_validation(admin_client, db):
    cid = _company(admin_client, db)
    _sale(admin_client, cid)
    assert admin_client.post(
        "/api/recipes",
        json={"name": "NoCo", "product_name": "ProdX"}).status_code == 400
    assert _recipe(
        admin_client, "X", 999999, "ProdX").status_code == 400
    assert _recipe(
        admin_client, "X", cid, "GhostProduct").status_code == 400


def test_recipe_list_shows_company(admin_client, db):
    cid = _company(admin_client, db, name="ListCo")
    _sale(admin_client, cid, product="ListProd", pi="PI-RL")
    _recipe(admin_client, "ListProd -- ListCo", cid, "ListProd")
    rows = admin_client.get("/api/recipes").get_json()
    row = [x for x in rows if x["name"] == "ListProd -- ListCo"][0]
    assert row["company_name"] == "ListCo"
    assert row["product_name"] == "ListProd"
