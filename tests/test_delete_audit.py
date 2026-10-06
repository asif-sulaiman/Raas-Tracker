"""P1-5: the four unaudited admin deletes now leave an audit trail.

None of these wrote any `audit_logs` row:
- `DELETE /api/sales/<id>/items/<item_id>` — a money-affecting PI line
- `DELETE /api/sales/<id>/shipments/<id>`
- `DELETE /api/uploads/<id>` — cascades to `upload_rows`
- `POST  /api/users/<id>/revoke` — the mass session kill switch

A delete destroys its own evidence, so each row captures an identifying summary
of what was removed, read *before* the DELETE. The fields captured (product,
quantity, unit price, ship date, upload filename, username) are business data,
not the bank/contact PII that `COMPANY_UPDATE` deliberately withholds.

Two invariants also pinned here:
- a **refused** delete (409 on an invoiced line, 404 on a missing row) writes
  no row, because nothing was deleted;
- `revoke_user_sessions` stays **silent**. It is shared with the voluntary
  password-change path, so auditing inside it would add a row to every password
  change. The kill switch is audited at its admin route instead.
"""
import pytest


def _rows(db, action):
    return db.execute(
        "SELECT entity_type, entity_id, user_id, old_value, new_value, ip_address "
        "FROM audit_logs WHERE action = %s ORDER BY id", (action,)).fetchall()


def _count(db, action):
    return len(_rows(db, action))


def _sale_with_item(db, pi_number="PI-DEL"):
    """A sale with TWO items.

    The route enforces "a sale must keep at least one product item", so a
    single-item sale is refused with 400 before the delete logic is reached.
    """
    from raas_tracker.companies import create_company
    from raas_tracker.sales import add_sale
    cid = create_company(db, name="DelCo")
    add_sale(db, {"pi_number": pi_number, "client_name": "DelCo", "company_id": cid},
             [{"product_name": "Acetone", "quantity": 100,
               "unit_price": 12.5, "unit": "KG"},
              {"product_name": "Methanol", "quantity": 50,
               "unit_price": 8.0, "unit": "KG"}])
    sale_id = db.execute("SELECT id FROM sales WHERE pi_number = %s",
                         (pi_number,)).fetchone()[0]
    item_id = db.execute("SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id",
                         (sale_id,)).fetchone()[0]
    return sale_id, item_id


def _upload(db, filename="delete-me.xlsx", with_row=True):
    from raas_tracker.uploads import save_upload
    results = {
        "stats": {"total": 0, "matched": 0, "last_month_mismatches": 0,
                  "this_month_mismatches": 0, "both_mismatches": 0,
                  "not_in_db": 0, "not_in_upload": 0, "match_percentage": 0.0},
        "matches": [], "last_month_mismatches": [], "this_month_mismatches": [],
        "both_mismatches": [], "not_in_db": [], "not_in_upload": [],
    }
    upload_id = save_upload(db, filename, results)
    if with_row:
        db.execute(
            "INSERT INTO upload_rows (upload_id, chemical_name, batch_number, "
            "expiry_date, upload_unit, balance_last_month, balance_this_month, "
            "matched_in_db, unit_match) VALUES (%s, 'Acid', 'B1', '', 'KG', 1, 2, 0, 1)",
            (upload_id,))
        db.commit()
    return upload_id


# ==================== PI line (money-affecting) ====================

def test_deleting_a_pi_line_is_audited(admin_client, db):
    sale_id, item_id = _sale_with_item(db, "PI-DEL-1")
    r = admin_client.delete(f"/api/sales/{sale_id}/items/{item_id}")
    assert r.status_code == 200, r.get_json()

    rows = _rows(db, "SALE_ITEM_DELETE")
    assert len(rows) == 1, "deleting a PI line left no audit row"
    entity_type, entity_id, user_id, old_value, _new_value, ip = rows[0]
    assert (entity_type, entity_id, user_id) == ("sale_item", item_id, "admin")
    assert ip == "127.0.0.1"
    # The row is gone, so the summary is the only surviving evidence.
    assert "Acetone" in old_value
    assert "100" in old_value
    assert "12.5" in old_value


def test_refused_invoiced_pi_line_delete_writes_no_audit_row(admin_client, db):
    """409 means nothing was deleted, so there must be no delete row."""
    sale_id, item_id = _sale_with_item(db, "PI-DEL-2")
    inv_id = db.execute(
        "INSERT INTO invoices (sale_id, invoice_number) VALUES (%s, 'INV-D1') "
        "RETURNING id", (sale_id,)).fetchone()[0]
    db.execute(
        "INSERT INTO invoice_items (invoice_id, sale_item_id, product_name, unit, "
        "quantity, unit_price, line_total) "
        "VALUES (%s, %s, 'Acetone', 'KG', 100, 12.5, 1250.00)", (inv_id, item_id))
    db.commit()

    r = admin_client.delete(f"/api/sales/{sale_id}/items/{item_id}")
    assert r.status_code == 409, r.get_json()
    assert _count(db, "SALE_ITEM_DELETE") == 0


def test_missing_pi_line_delete_writes_no_audit_row(admin_client, db):
    """A delete that matched no row must not write a delete row.

    The status code is deliberately not asserted: `delete_sale_item` ignores
    `rowcount`, so this route answers 200 even when nothing was deleted. That
    is a pre-existing defect, reported separately — changing a route's response
    is outside P1-5's scope. What matters here is that the audit trail stays
    truthful about what actually happened.
    """
    sale_id, _item_id = _sale_with_item(db, "PI-DEL-3")
    admin_client.delete(f"/api/sales/{sale_id}/items/999999")
    assert _count(db, "SALE_ITEM_DELETE") == 0


# ==================== shipment ====================

def test_deleting_a_shipment_is_audited(admin_client, db):
    sale_id, _item_id = _sale_with_item(db, "PI-DEL-4")
    url = f"/api/sales/{sale_id}/shipments"
    assert admin_client.post(url, json={
        "ship_date": "2026-09-10", "invoice_number": "INV-S1",
        "invoice_date": "2026-09-09"}).status_code == 201
    ship_id = admin_client.get(f"/api/sales/{sale_id}").get_json()["shipments"][0]["id"]

    assert admin_client.delete(f"{url}/{ship_id}").status_code == 200
    rows = _rows(db, "SHIPMENT_DELETE")
    assert len(rows) == 1, "deleting a shipment left no audit row"
    entity_type, entity_id, user_id, old_value, _new_value, ip = rows[0]
    assert (entity_type, entity_id, user_id) == ("shipment", ship_id, "admin")
    assert ip == "127.0.0.1"
    assert "2026-09-10" in old_value


def test_missing_shipment_delete_writes_no_audit_row(admin_client, db):
    sale_id, _item_id = _sale_with_item(db, "PI-DEL-5")
    r = admin_client.delete(f"/api/sales/{sale_id}/shipments/999999")
    assert r.status_code == 404, r.get_json()
    assert _count(db, "SHIPMENT_DELETE") == 0


# ==================== upload (cascades) ====================

def test_deleting_an_upload_is_audited(admin_client, db):
    upload_id = _upload(db, "quarterly-stock.xlsx")
    assert admin_client.delete(f"/api/uploads/{upload_id}").status_code == 200

    rows = _rows(db, "UPLOAD_DELETE")
    assert len(rows) == 1, "deleting an upload left no audit row"
    entity_type, entity_id, user_id, old_value, _new_value, ip = rows[0]
    assert (entity_type, entity_id, user_id) == ("upload", upload_id, "admin")
    assert ip == "127.0.0.1"
    assert "quarterly-stock" in old_value, "the filename is the only identifier left"
    assert "rows=1" in old_value, "the cascade removed rows; the count is evidence"


def test_missing_upload_delete_writes_no_audit_row(admin_client, db):
    assert admin_client.delete("/api/uploads/999999").status_code == 404
    assert _count(db, "UPLOAD_DELETE") == 0


# ==================== session kill switch ====================

def _user_with_sessions(db, username="user", count=2):
    from raas_tracker.auth import create_session
    uid = db.execute("SELECT id FROM users WHERE username = %s",
                     (username,)).fetchone()[0]
    for _ in range(count):
        create_session(db, uid)
    return uid


def test_admin_session_kill_switch_is_audited(admin_client, db):
    uid = _user_with_sessions(db, "user", 2)
    r = admin_client.post(f"/api/users/{uid}/revoke")
    assert r.status_code == 200, r.get_json()

    rows = _rows(db, "SESSION_REVOKE")
    assert len(rows) == 1, "the session kill switch left no audit row"
    entity_type, entity_id, user_id, _old_value, new_value, ip = rows[0]
    assert (entity_type, entity_id, user_id) == ("user", uid, "admin")
    assert ip == "127.0.0.1"
    assert "user" in new_value
    assert "sessions=2" in new_value


def test_password_change_writes_no_session_revoke_row(user_client, db):
    """The shared revoke helper must stay silent.

    `set_password` calls `revoke_user_sessions` on every voluntary change, so
    auditing inside that helper would add a row to every password change. This
    pins the decision to audit at the admin route only.
    """
    assert user_client.put("/api/auth/password", json={
        "current_password": "user-pass-123",
        "new_password": "brand-new-pass-1"}).status_code == 200
    assert _count(db, "SESSION_REVOKE") == 0
    assert _count(db, "PASSWORD_CHANGED") == 1


def test_session_kill_switch_counts_only_live_sessions(admin_client, db):
    """An already-revoked session must not be counted.

    The `revoked = 0` predicate sits in the JOIN's ON clause deliberately:
    moving it to WHERE would turn the LEFT JOIN into an inner join, so a user
    with zero live sessions would yield no row at all and the kill switch would
    execute with no audit row. This pins the counting semantics.
    """
    uid = _user_with_sessions(db, "user", 2)
    db.execute("UPDATE sessions SET revoked = 1 WHERE user_id = %s "
               "AND id IN (SELECT id FROM sessions WHERE user_id = %s "
               "ORDER BY id LIMIT 1)", (uid, uid))
    db.commit()

    assert admin_client.post(f"/api/users/{uid}/revoke").status_code == 200
    _e, _i, _u, _o, new_value, _ip = _rows(db, "SESSION_REVOKE")[-1]
    assert "sessions=1" in new_value, "an already-revoked session was counted"


def test_kill_switch_on_user_with_no_live_sessions_still_audits(admin_client, db):
    """Zero live sessions must still leave a row, with sessions=0."""
    uid = _user_with_sessions(db, "user", 1)
    db.execute("UPDATE sessions SET revoked = 1 WHERE user_id = %s", (uid,))
    db.commit()

    assert admin_client.post(f"/api/users/{uid}/revoke").status_code == 200
    rows = _rows(db, "SESSION_REVOKE")
    assert len(rows) == 1, "the kill switch ran but wrote no audit row"
    assert "sessions=0" in rows[0][4], "a zero count must be recorded, not dropped"


def test_admin_session_revoke_requires_admin(user_client, db):
    uid = _user_with_sessions(db, "user", 1)
    assert user_client.post(f"/api/users/{uid}/revoke").status_code == 403
    assert _count(db, "SESSION_REVOKE") == 0


def test_shipment_delete_requires_admin(admin_client, user_client, db):
    sale_id, _item_id = _sale_with_item(db, "PI-DEL-6")
    url = f"/api/sales/{sale_id}/shipments"
    assert admin_client.post(url, json={"ship_date": "2026-09-10"}).status_code == 201
    ship_id = admin_client.get(f"/api/sales/{sale_id}").get_json()["shipments"][0]["id"]
    assert user_client.delete(f"{url}/{ship_id}").status_code == 403
    assert _count(db, "SHIPMENT_DELETE") == 0