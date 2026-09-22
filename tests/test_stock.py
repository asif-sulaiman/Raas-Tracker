"""Stock comparison matrix + chemical/recipe status-code tests."""
from chem_stock import compare_stock_upload


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
    assert r.get_json() == {"success": False, "name": "Dup"}


def test_update_missing_chemical_404(admin_client):
    r = admin_client.post("/api/chemicals/update", json={"name": "Ghost", "delta": 1})
    assert r.status_code == 404
    assert r.get_json()["success"] is False


def test_recipe_crud_statuses(admin_client):
    assert admin_client.post("/api/recipes",
                             json={"name": "R1", "yield": 5}).status_code == 200
    assert admin_client.post("/api/recipes",
                             json={"name": "R1", "yield": 5}).status_code == 409
    assert admin_client.delete("/api/recipes/Ghost").status_code == 404
    assert admin_client.delete("/api/recipes/R1").status_code == 200
    assert admin_client.delete("/api/recipes/R1").status_code == 404
