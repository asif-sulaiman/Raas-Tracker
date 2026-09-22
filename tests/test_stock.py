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


def test_reorder_migration_on_legacy_schema(tmp_path):
    import sqlite3
    from chem_stock import get_connection
    legacy = str(tmp_path / "legacy.db")
    raw = sqlite3.connect(legacy)
    raw.execute("CREATE TABLE chemicals (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name TEXT NOT NULL UNIQUE, current_qty REAL DEFAULT 0, "
                "balance_last_month REAL DEFAULT 0, unit TEXT DEFAULT 'KG')")
    raw.execute("INSERT INTO chemicals (name, current_qty) VALUES ('Old', 7)")
    raw.commit()
    raw.close()
    conn = get_connection(legacy)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(chemicals)").fetchall()}
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


def test_recipe_crud_statuses(admin_client):
    assert admin_client.post("/api/recipes",
                             json={"name": "R1", "yield": 5}).status_code == 200
    assert admin_client.post("/api/recipes",
                             json={"name": "R1", "yield": 5}).status_code == 409
    assert admin_client.delete("/api/recipes/Ghost").status_code == 404
    assert admin_client.delete("/api/recipes/R1").status_code == 200
    assert admin_client.delete("/api/recipes/R1").status_code == 404
