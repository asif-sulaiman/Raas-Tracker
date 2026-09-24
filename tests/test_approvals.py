"""Upload approval atomic unit gate + flagged staging (Phase A3)."""
from chem_stock import (
    add_chemical,
    adjust_stock_from_upload,
    approve_upload,
    approve_upload_row,
    compare_stock_upload,
    create_approval_workflow,
    get_pending_approvals,
    get_upload_history,
    save_upload,
)


def _seed(db):
    add_chemical(db, "Alpha", 100, "KG")
    add_chemical(db, "Gamma", 0, "KG")


def _upload(db, rows, filename="U.pdf"):
    out = compare_stock_upload(db, rows)
    uid = save_upload(db, filename, out)
    return uid, out


def _row_id(db, upload_id, name):
    return db.execute(
        "SELECT id FROM upload_rows WHERE upload_id = %s AND chemical_name = %s",
        (upload_id, name)).fetchone()[0]


def test_flagged_status_on_unmapped(db):
    _seed(db)
    uid, out = _upload(db, [{"name": "Alpha", "balance_last_month": 100,
                             "balance_this_month": 100, "upload_unit": "NOPE"}])
    assert out["unmapped_units"] == [
        {"name": "Alpha", "upload_unit": "NOPE", "db_unit": "KG"}]
    hist = get_upload_history(db)
    assert [h for h in hist if h["id"] == uid][0]["status"] == "flagged"


def test_completed_status_when_clean(db):
    _seed(db)
    uid, out = _upload(db, [{"name": "Alpha", "balance_last_month": 100,
                             "balance_this_month": 100, "upload_unit": "KG"}])
    assert out["unmapped_units"] == []
    hist = get_upload_history(db)
    assert [h for h in hist if h["id"] == uid][0]["status"] == "completed"


def test_approve_row_blocked_on_unmapped(db):
    _seed(db)
    uid, _ = _upload(db, [{"name": "Alpha", "balance_last_month": 100,
                           "balance_this_month": 100, "upload_unit": "NOPE"}])
    wid = create_approval_workflow(db, uid, _row_id(db, uid, "Alpha"))
    assert approve_upload_row(db, wid, "X", "trying") is False
    assert get_pending_approvals(db, uid)[0]["status"] == "pending"
    blocked = db.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE action = 'APPROVE_BLOCKED'").fetchone()[0]
    assert blocked >= 1


def test_approve_upload_atomic_block(db):
    _seed(db)
    uid, _ = _upload(db, [
        {"name": "Alpha", "balance_last_month": 100,
         "balance_this_month": 100, "upload_unit": "KG"},
        {"name": "Gamma", "balance_last_month": 0,
         "balance_this_month": 50, "upload_unit": "NOPE"},
    ])
    assert approve_upload(db, uid, reviewed_by="admin") is False
    # Nothing approved, nothing applied, status untouched.
    assert get_pending_approvals(db, uid) == []
    status = db.execute("SELECT status FROM uploads WHERE id = %s",
                        (uid,)).fetchone()[0]
    assert status == "flagged"


def test_adjust_skips_unmapped(db):
    _seed(db)
    uid, _ = _upload(db, [{"name": "Alpha", "balance_last_month": 100,
                           "balance_this_month": 999, "upload_unit": "NOPE"}])
    wid = create_approval_workflow(db, uid, _row_id(db, uid, "Alpha"))
    # Force-approve past the gate (simulates legacy data): apply must skip.
    db.execute("UPDATE approval_workflow SET status = 'approved' WHERE id = %s", (wid,))
    db.commit()
    assert adjust_stock_from_upload(db, uid, reviewed_by="admin") is True
    qty = db.execute("SELECT current_qty FROM chemicals WHERE name = 'Alpha'").fetchone()[0]
    assert qty == 100


def test_clean_batch_approves_converts_and_adjusts(db):
    _seed(db)
    uid, _ = _upload(db, [
        {"name": "Alpha", "balance_last_month": 100,
         "balance_this_month": 110, "upload_unit": "KG"},
        {"name": "Gamma", "balance_last_month": 0,
         "balance_this_month": 2000, "upload_unit": "G"},
    ])
    assert approve_upload(db, uid, reviewed_by="admin") is True
    assert adjust_stock_from_upload(db, uid, reviewed_by="admin") is True
    qtys = {r[0]: r[1] for r in db.execute("SELECT name, current_qty FROM chemicals")}
    assert qtys["Alpha"] == 110
    assert qtys["Gamma"] == 2.0


def test_adjust_matches_case_insensitively(db):
    add_chemical(db, "MixedCase", 10, "KG")
    uid, out = _upload(db, [{"name": "MIXEDCASE", "balance_last_month": 10,
                             "balance_this_month": 25, "upload_unit": "KG"}])
    assert out["unmapped_units"] == []
    assert approve_upload(db, uid, reviewed_by="admin") is True
    assert adjust_stock_from_upload(db, uid, reviewed_by="admin") is True
    qty = db.execute("SELECT current_qty FROM chemicals WHERE name = 'MixedCase'").fetchone()[0]
    assert qty == 25


def test_create_conversion_validation(admin_client):
    assert admin_client.post("/api/unit-conversions",
                             json={"from_unit": "", "to_unit": "KG",
                                   "factor": 2}).status_code == 400
    for bad in [0, -1, "abc", None]:
        assert admin_client.post("/api/unit-conversions",
                                 json={"from_unit": "BARREL", "to_unit": "KG",
                                       "factor": bad}).status_code == 400
    r = admin_client.post("/api/unit-conversions",
                          json={"from_unit": "drum", "to_unit": "kg", "factor": 200})
    assert r.status_code == 200
    assert r.get_json()["from_unit"] == "DRUM"
    rows = admin_client.get("/api/unit-conversions").get_json()
    assert [c for c in rows if c["from_unit"] == "DRUM" and c["factor"] == 200]
    # Upsert overwrites.
    assert admin_client.post("/api/unit-conversions",
                             json={"from_unit": "DRUM", "to_unit": "KG",
                                   "factor": 210}).status_code == 200
    rows = admin_client.get("/api/unit-conversions").get_json()
    assert [c for c in rows if c["from_unit"] == "DRUM" and c["factor"] == 210]


def test_approve_apply_routes(admin_client, db):
    from chem_stock import get_unmapped_rows
    add_chemical(db, "Alpha", 100, "KG")
    uid, _ = _upload(db, [{"name": "Alpha", "balance_last_month": 100,
                           "balance_this_month": 120, "upload_unit": "NOPE"}],
                     filename="FLAGGED.pdf")
    r = admin_client.post(f"/api/uploads/{uid}/approve")
    assert r.status_code == 400
    body = r.get_json()
    assert body["unmapped"] == [{"row_id": body["unmapped"][0]["row_id"],
                                 "chemical_name": "Alpha",
                                 "upload_unit": "NOPE", "db_unit": "KG"}]
    assert get_unmapped_rows(db, uid)[0]["chemical_name"] == "Alpha"
    # Apply before approve is rejected.
    assert admin_client.post(f"/api/uploads/{uid}/apply").status_code == 400
    # Map the unit, then the full flow works.
    assert admin_client.post("/api/unit-conversions",
                             json={"from_unit": "NOPE", "to_unit": "KG",
                                   "factor": 1}).status_code == 200
    assert admin_client.post(f"/api/uploads/{uid}/approve").status_code == 200
    r = admin_client.post(f"/api/uploads/{uid}/apply")
    assert r.status_code == 200 and r.get_json() == {"adjusted": True}
    qty = db.execute("SELECT current_qty FROM chemicals WHERE name = 'Alpha'").fetchone()[0]
    assert qty == 120


def test_approve_apply_ghost_and_unauth(admin_client, db):
    import flask_app
    # Fresh client: the shared `client` fixture IS admin_client after login.
    anon = flask_app.app.test_client()
    assert admin_client.post("/api/uploads/999999/approve").status_code == 404
    assert admin_client.post("/api/uploads/999999/apply").status_code == 404
    assert anon.post("/api/uploads/1/approve").status_code == 401
    assert anon.post("/api/unit-conversions",
                     json={"from_unit": "A", "to_unit": "B",
                           "factor": 1}).status_code == 401
