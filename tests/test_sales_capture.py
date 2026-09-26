"""P1: capture fields — company/unit on PI entry, shipments, new tables."""
import flask_app


def _create_sale(client, **kw):
    payload = {"sale": {"pi_number": kw.get("pi_number", "PI-P1"),
                        "pi_date": "2026-09-01",
                        "client_name": kw.get("client_name"),
                        "company_id": kw.get("company_id")},
               "items": kw.get("items", [{"product_name": "ProdA",
                                          "quantity": 100,
                                          "unit_price": 3.25,
                                          "unit": "KG"}])}
    return client.post("/api/sales", json=payload)


def test_create_sale_with_company_and_units(admin_client, db):
    admin_client.post("/api/companies", json={"name": "CaptureCo"})
    cid = db.execute(
        "SELECT id FROM companies WHERE name = 'CaptureCo'").fetchone()[0]
    r = _create_sale(admin_client, pi_number="PI-C1", company_id=cid,
                     items=[{"product_name": "ProdA", "quantity": 100,
                             "unit_price": 3.25, "unit": "DRUM"}])
    assert r.status_code == 201
    sale = db.execute(
        "SELECT company_id, client_name FROM sales "
        "WHERE pi_number = 'PI-C1'").fetchone()
    assert sale[0] == cid and sale[1] == "CaptureCo"
    unit = db.execute(
        "SELECT unit FROM sale_items WHERE sale_id = %s",
        (r.get_json()["id"],)).fetchone()[0]
    assert unit == "DRUM"


def test_create_sale_bad_company_400(admin_client):
    assert _create_sale(
        admin_client, pi_number="PI-BAD", company_id=999999).status_code == 400


def test_create_sale_unit_defaults_kg(admin_client, db):
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": "PI-U", "client_name": "UCo"},
        "items": [{"product_name": "P", "quantity": 1, "unit_price": 1}]})
    assert r.status_code == 201
    unit = db.execute(
        "SELECT unit FROM sale_items WHERE sale_id = %s",
        (r.get_json()["id"],)).fetchone()[0]
    assert unit == "KG"


def test_shipments_crud_and_gates(admin_client, user_client):
    sid = _create_sale(admin_client, pi_number="PI-S1").get_json()["id"]
    url = f"/api/sales/{sid}/shipments"
    assert user_client.post(
        url, json={"ship_date": "2026-09-10"}).status_code == 403
    c = flask_app.app.test_client()
    assert c.post(url, json={"ship_date": "2026-09-10"}).status_code == 401
    assert admin_client.post(url, json={}).status_code == 400
    assert admin_client.post(
        "/api/sales/999999/shipments",
        json={"ship_date": "2026-09-10"}).status_code == 404
    r = admin_client.post(url, json={
        "ship_date": "2026-09-10", "invoice_number": "INV-1",
        "invoice_date": "2026-09-09", "notes": "partial 1/2"})
    assert r.status_code == 201
    detail = admin_client.get(f"/api/sales/{sid}").get_json()
    assert len(detail["shipments"]) == 1
    assert detail["shipments"][0]["invoice_number"] == "INV-1"
    shid = detail["shipments"][0]["id"]
    assert user_client.delete(
        f"{url}/{shid}").status_code == 403
    assert admin_client.delete(f"{url}/{shid}").status_code == 200
    assert admin_client.get(f"/api/sales/{sid}").get_json()["shipments"] == []


def test_new_tables_and_columns_exist(db):
    cols = {r[0] for r in db.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'sales'").fetchall()}
    assert {"maturity_date", "comments", "company_id"} <= cols
    item_cols = {r[0] for r in db.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'sale_items'").fetchall()}
    assert "unit" in item_cols
    tables = {r[0] for r in db.execute(
        "SELECT tablename FROM pg_tables "
        "WHERE schemaname = 'public'").fetchall()}
    assert {"shipments", "production_runs",
            "production_run_items", "companies"} <= tables
