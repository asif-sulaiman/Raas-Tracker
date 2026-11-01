"""Stock comparison matrix + chemical/recipe status-code tests."""
from chem_stock import add_chemical, compare_stock_upload


def _seed(db):
    db.execute(
        "INSERT INTO chemicals (name, current_qty, balance_last_month, unit) VALUES "
        "('Alpha', 100, 90, 'KG'), ('Beta', 50, 50, 'KG'), ('Gamma', 0, 10, 'KG')"
    )
    db.commit()


def test_exact_match(db):
    _seed(db)
    out = compare_stock_upload(db, [{"name": "Alpha", "balance_last_month": 90,
                                     "balance_this_month": 100}])
    assert len(out["matches"]) == 1
    assert out["not_in_db"] == [] and out["not_in_upload"] != []


def test_last_month_mismatch_only(db):
    _seed(db)
    out = compare_stock_upload(db, [{"name": "Alpha", "balance_last_month": 80,
                                     "balance_this_month": 100}])
    assert len(out["last_month_mismatches"]) == 1
    assert out["this_month_mismatches"] == []


def test_this_month_mismatch_only(db):
    _seed(db)
    out = compare_stock_upload(db, [{"name": "Alpha", "balance_last_month": 90,
                                     "balance_this_month": 111}])
    assert len(out["this_month_mismatches"]) == 1
    assert out["last_month_mismatches"] == []


def test_both_mismatch(db):
    _seed(db)
    out = compare_stock_upload(db, [{"name": "Gamma", "balance_last_month": 0,
                                     "balance_this_month": 5}])
    assert len(out["both_mismatches"]) == 1


def test_not_in_db_and_not_in_upload(db):
    _seed(db)
    out = compare_stock_upload(db, [{"name": "Zed", "balance_last_month": 1,
                                     "balance_this_month": 1}])
    assert len(out["not_in_db"]) == 1
    assert out["not_in_db"][0]["name"] == "Zed"
    assert {r["name"] for r in out["not_in_upload"]} == {"Alpha", "Beta", "Gamma"}


def test_empty_name_skipped(db):
    _seed(db)
    out = compare_stock_upload(db, [{"name": "  ", "balance_last_month": 0,
                                     "balance_this_month": 0}])
    assert out["not_in_db"] == []


def test_unit_mismatch_flagged(db):
    _seed(db)
    out = compare_stock_upload(db, [{"name": "Alpha", "balance_last_month": 90,
                                     "balance_this_month": 100, "upload_unit": "NOPE"}])
    rows = out["matches"] + out["last_month_mismatches"] + out["this_month_mismatches"] \
        + out["both_mismatches"]
    assert rows and all(r.get("unit_match") is False for r in rows)


def test_add_duplicate_chemical_409(admin_client):
    assert admin_client.post("/api/chemicals",
                             json={"name": "Dup", "qty": 1, "unit": "KG"}).status_code == 200
    r = admin_client.post("/api/chemicals", json={"name": "Dup", "qty": 1, "unit": "KG"})
    assert r.status_code == 409
    body = r.get_json()
    assert body["success"] is False
    assert body["name"] == "Dup"
    assert "already exists" in body["error"]


def test_update_missing_chemical_404(admin_client):
    r = admin_client.post("/api/chemicals/update", json={"name": "Ghost", "delta": 1})
    assert r.status_code == 404
    assert r.get_json()["success"] is False


def test_reorder_migration_on_legacy_schema(pg_dsn):
    from chem_stock import get_connection
    conn = get_connection(pg_dsn)
    conn.execute("INSERT INTO chemicals (name, current_qty) VALUES ('Old', 7)")
    conn.execute("ALTER TABLE chemicals DROP COLUMN reorder_level")
    conn.commit()
    conn.close()
    conn = get_connection(pg_dsn)
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'chemicals'")}
    assert "reorder_level" in cols
    assert conn.execute("SELECT reorder_level FROM chemicals WHERE name = 'Old'").fetchone()[0] == 0
    conn.close()


def test_reorder_level_roundtrip(admin_client):
    admin_client.post("/api/chemicals", json={"name": "RL", "qty": 30, "unit": "KG"})
    rows = admin_client.get("/api/chemicals").get_json()
    assert [c for c in rows if c["name"] == "RL"][0]["reorder_level"] == 0
    r = admin_client.put("/api/chemicals/reorder", json={"name": "RL", "reorder_level": 25})
    assert r.status_code == 200
    assert r.get_json() == {"success": True, "name": "RL", "reorder_level": 25.0}
    rows = admin_client.get("/api/chemicals").get_json()
    assert [c for c in rows if c["name"] == "RL"][0]["reorder_level"] == 25.0


def test_reorder_level_validation(admin_client):
    admin_client.post("/api/chemicals", json={"name": "RL2", "qty": 5, "unit": "KG"})
    assert admin_client.put("/api/chemicals/reorder",
                            json={"name": "RL2", "reorder_level": -1}).status_code == 400
    assert admin_client.put("/api/chemicals/reorder",
                            json={"name": "RL2", "reorder_level": "abc"}).status_code == 400
    assert admin_client.put("/api/chemicals/reorder",
                            json={"name": "Ghost", "reorder_level": 5}).status_code == 404
    assert admin_client.put("/api/chemicals/reorder", json={"reorder_level": 5}).status_code == 400


def test_upload_history_carries_dashboard_fields(admin_client, db):
    from chem_stock import save_upload
    results = {
        "matches": [{"name": "Alpha", "db_last": 90, "db_this": 100,
                     "upload_last": 90, "upload_this": 100}],
        "last_month_mismatches": [],
        "this_month_mismatches": [],
        "both_mismatches": [],
        "not_in_db": [{"name": "Zed", "upload_last": 1, "upload_this": 1}],
        "not_in_upload": [{"name": "Beta", "db_last": 50, "db_this": 50}],
        "stats": {"total": 3, "matched": 1, "last_month_mismatches": 0,
                  "this_month_mismatches": 0, "both_mismatches": 0,
                  "not_in_db": 1, "not_in_upload": 1, "match_percentage": 33.33},
    }
    save_upload(db, "AUGUST_2026.pdf", results)
    rows = admin_client.get("/api/uploads").get_json()
    assert len(rows) == 1
    row = rows[0]
    for field in ("id", "filename", "upload_date", "status", "total_chemicals",
                  "matched", "last_month_mismatches", "this_month_mismatches",
                  "both_mismatches", "not_in_db", "not_in_upload", "match_percentage"):
        assert field in row, field
    assert row["filename"] == "AUGUST_2026.pdf"
    assert row["matched"] == 1 and row["not_in_db"] == 1
    assert row["match_percentage"] == 33.33


def test_recipe_crud_statuses(admin_client, db):
    admin_client.post("/api/companies", json={"name": "TestCo"})
    cid = db.execute("SELECT id FROM companies WHERE name = 'TestCo'").fetchone()[0]
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": "PI-TEST", "client_name": "TestCo", "company_id": cid},
        "items": [{"product_name": "TestProduct", "quantity": 5, "unit_price": 10, "unit": "KG"}]
    })
    sale_id = db.execute("SELECT id FROM sales WHERE pi_number = 'PI-TEST'").fetchone()[0]
    invoice_id = _invoice_for(db, sale_id, "INV-CRUD")
    assert admin_client.post("/api/recipes",
                             json={"name": "R1 -- TestCo", "company_id": cid,
                                   "invoice_id": invoice_id}).status_code == 201
    assert admin_client.post("/api/recipes",
                             json={"name": "R1 -- TestCo", "company_id": cid,
                                   "invoice_id": invoice_id}).status_code == 409
    assert admin_client.delete(f"/api/recipes/Ghost?company_id={cid}").status_code == 404
    assert admin_client.delete(f"/api/recipes/R1 -- TestCo?company_id={cid}").status_code == 200
    assert admin_client.delete(f"/api/recipes/R1 -- TestCo?company_id={cid}").status_code == 404


def test_recipe_mutations_audited(admin_client, db):
    admin_client.post("/api/companies", json={"name": "TestCo"})
    cid = db.execute("SELECT id FROM companies WHERE name = 'TestCo'").fetchone()[0]
    admin_client.post("/api/chemicals", json={"name": "AudChem", "qty": 10, "unit": "KG"})
    admin_client.post("/api/sales", json={
        "sale": {"pi_number": "PI-AUD", "client_name": "TestCo", "company_id": cid},
        "items": [{"product_name": "AudChem", "quantity": 10, "unit_price": 10, "unit": "KG"}]
    })
    sale_id = db.execute("SELECT id FROM sales WHERE pi_number = 'PI-AUD'").fetchone()[0]
    invoice_id = _invoice_for(db, sale_id, "INV-AUD")
    assert admin_client.post("/api/recipes",
                             json={"name": "AudR -- TestCo", "company_id": cid,
                                   "invoice_id": invoice_id}).status_code == 201
    assert admin_client.post("/api/recipes/AudR -- TestCo/items",
                             json={"chemical": "AudChem", "percentage": 20, "company_id": cid}).status_code == 200
    assert admin_client.put("/api/recipes/AudR -- TestCo/items/AudChem",
                            json={"percentage": 25, "company_id": cid}).status_code == 200
    assert admin_client.delete(f"/api/recipes/AudR -- TestCo/items/AudChem?company_id={cid}").status_code == 200
    assert admin_client.delete(f"/api/recipes/AudR -- TestCo?company_id={cid}").status_code == 200
    actions = {r[0] for r in db.execute(
        "SELECT action FROM audit_logs WHERE entity_type LIKE 'recipe%'").fetchall()}
    assert {"RECIPE_CREATE", "RECIPE_ITEM_ADD", "RECIPE_ITEM_UPDATE",
            "RECIPE_ITEM_DELETE", "RECIPE_DELETE"} <= actions
    actors = {r[0] for r in db.execute(
        "SELECT DISTINCT user_id FROM audit_logs WHERE entity_type LIKE 'recipe%'").fetchall()}
    assert actors == {"admin"}


def _invoice_for(db, sale_id, invoice_number, quantity=5):
    from raas_tracker.sales import create_invoice
    item_id = db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id LIMIT 1",
        (sale_id,)).fetchone()[0]
    return create_invoice(
        db, sale_id, invoice_number,
        lines=[{"sale_item_id": item_id, "quantity": quantity}],
        invoice_date="2026-01-15")["invoice_id"]


def test_recipe_percentage_cap(db):
    import pytest
    from raas_tracker.recipes import add_recipe, add_recipe_item, update_recipe_item
    from raas_tracker.companies import create_company
    from raas_tracker.sales import add_sale
    cid = create_company(db, name="TestCo")
    sale_id = add_sale(
        db, {"pi_number": "PI-TEST", "client_name": "TestCo", "company_id": cid},
        [{"product_name": "TestProduct", "quantity": 5, "unit_price": 10,
          "unit": "KG"}])
    invoice_id = _invoice_for(db, sale_id, "INV-CAP")
    recipe = add_recipe(db, "Cap", cid, invoice_id)
    add_chemical(db, "CapA", 5, "KG")
    add_chemical(db, "CapB", 5, "KG")
    add_chemical(db, "CapC", 5, "KG")
    assert add_recipe_item(db, recipe["id"], "TestProduct", "CapA", 60.0) is True
    with pytest.raises(ValueError):
        add_recipe_item(db, recipe["id"], "TestProduct", "CapB", 41.0)
    assert add_recipe_item(db, recipe["id"], "TestProduct", "CapB", 30.0) is True
    item_a = db.execute(
        "SELECT id FROM recipe_items WHERE chemical_id IN "
        "(SELECT id FROM chemicals WHERE name = 'CapA')").fetchone()[0]
    item_b = db.execute(
        "SELECT id FROM recipe_items WHERE chemical_id IN "
        "(SELECT id FROM chemicals WHERE name = 'CapB')").fetchone()[0]
    with pytest.raises(ValueError):
        update_recipe_item(db, recipe["id"], item_a, 71.0)
    assert update_recipe_item(db, recipe["id"], item_b, 20.0) is True
    # Boundary: exactly 100% total is allowed.
    assert update_recipe_item(db, recipe["id"], item_a, 80.0) is True
    with pytest.raises(ValueError):
        add_recipe_item(db, recipe["id"], "TestProduct", "CapC", 1.0)
    with pytest.raises(ValueError):
        update_recipe_item(db, recipe["id"], item_a, -1.0)


def test_recipe_percentage_cap_api(admin_client, db):
    admin_client.post("/api/companies", json={"name": "TestCo"})
    cid = db.execute("SELECT id FROM companies WHERE name = 'TestCo'").fetchone()[0]
    admin_client.post("/api/sales", json={
        "sale": {"pi_number": "PI-TEST", "client_name": "TestCo", "company_id": cid},
        "items": [{"product_name": "CapC", "quantity": 5, "unit_price": 10, "unit": "KG"}]
    })
    sale_id = db.execute("SELECT id FROM sales WHERE pi_number = 'PI-TEST'").fetchone()[0]
    item_id = db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id LIMIT 1",
        (sale_id,)).fetchone()[0]
    r = admin_client.post(f"/api/sales/{sale_id}/invoices", json={
        "invoice_number": "INV-CAPR", "invoice_date": "2026-01-15",
        "lines": [{"sale_item_id": item_id, "quantity": 5}]})
    assert r.status_code == 201, r.get_json()
    invoice_id = r.get_json()["invoice_id"]
    admin_client.post("/api/chemicals", json={"name": "CapC", "qty": 5, "unit": "KG"})
    admin_client.post("/api/chemicals", json={"name": "CapD", "qty": 5, "unit": "KG"})
    r = admin_client.post("/api/recipes", json={
        "name": "CapR -- TestCo", "company_id": cid, "invoice_id": invoice_id
    })
    assert r.status_code == 201, r.get_json()
    assert admin_client.post("/api/recipes/CapR -- TestCo/items",
                             json={"chemical": "CapC", "percentage": 90, "company_id": cid}).status_code == 200
    # 90 + 11 exceeds 100% -> rejected, recipe unchanged.
    r = admin_client.post("/api/recipes/CapR -- TestCo/items",
                          json={"chemical": "CapD", "percentage": 11, "company_id": cid})
    assert r.status_code == 400
    assert admin_client.put("/api/recipes/CapR -- TestCo/items/CapC",
                            json={"percentage": 101, "company_id": cid}).status_code == 400


def test_chemical_name_case_insensitive(db):
    from chem_stock import add_chemical
    assert add_chemical(db, "Acid", 10, "KG") is True
    assert add_chemical(db, "acid", 5, "KG") is False
    assert add_chemical(db, "ACID", 5, "KG") is False
    rows = db.execute("SELECT name, current_qty FROM chemicals").fetchall()
    assert rows == [("Acid", 10)]
