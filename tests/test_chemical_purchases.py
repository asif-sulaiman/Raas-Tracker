"""Slice 1 — Chemical Purchases: atomic multi-line receipts, corrections, cancel.

TDD red suite for `raas_tracker/purchases.py` + the purchase routes. Every test
here was written before the implementation and watched fail (ImportError while
`purchases.py` did not exist, then behaviour failures). Drives the real Flask
test client + the isolated pgserver database from conftest; the one rollback
test drives the service directly so a mid-transaction audit failure is exercised
without the HTTP error handler masking it.
"""
import json

import psycopg
import pytest

from raas_tracker import purchases


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _line(name, quantity, unit="KG", is_new=True, chemical_id=None,
          reorder_level=None):
    line = {"chemical_name": name, "quantity": quantity, "unit": unit,
            "is_new": is_new}
    if chemical_id is not None:
        line["chemical_id"] = chemical_id
    if reorder_level is not None:
        line["reorder_level"] = reorder_level
    return line


def _payload(lines, delivery_type="in_house", client_id=None,
             supplier="Acme Supplies", number="PO-1001", date="2026-01-15"):
    body = {"supplier_name": supplier, "purchase_number": number,
            "purchase_date": date, "delivery_type": delivery_type,
            "lines": lines}
    if client_id is not None:
        body["client_id"] = client_id
    return body


def _post(admin_client, body):
    return admin_client.post("/api/chemical-purchases", json=body)


def _seed_chem(admin_client, db, name, qty, unit="KG"):
    r = admin_client.post("/api/chemicals",
                          json={"name": name, "qty": qty, "unit": unit})
    assert r.status_code == 200, r.get_json()
    return db.execute("SELECT id FROM chemicals WHERE name = %s",
                      (name,)).fetchone()[0]


def _chem(db, name):
    return db.execute(
        "SELECT id, current_qty, unit, in_house_registered, reorder_level "
        "FROM chemicals WHERE name = %s", (name,)).fetchone()


def _new_client(db, name):
    cid = db.execute("INSERT INTO companies (name) VALUES (%s) RETURNING id",
                     (name,)).fetchone()[0]
    db.commit()
    return cid


def _material(client, name):
    r = client.get("/api/chemicals/inventory")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    # Slice 3 composes the purchasing forecast into this same response, so
    # "coverage" is no longer a projection-free placeholder. Nothing in this
    # file plans a PI, so the honest guard is that coverage is reported and
    # claims no missing work — not that the key is empty.
    assert body["coverage"] == {"state": "complete", "open_pis_without_plan": [],
                                "incomplete_plans": []}, body["coverage"]
    assert body["unstocked_requirements"] == []
    return next((m for m in body["materials"] if m["name"] == name), None)


# --------------------------------------------------------------------------- #
# 1. create: new + existing lines commit together; in-house stock increases
# --------------------------------------------------------------------------- #
def test_create_purchase_new_and_existing_lines(admin_client, db):
    existing_id = _seed_chem(admin_client, db, "Existing Acid", 10)
    r = _post(admin_client, _payload([
        _line("New Salt", 50, is_new=True, reorder_level=5),
        _line("Existing Acid", 25, is_new=False, chemical_id=existing_id),
    ]))
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["id"]
    assert len(body["items"]) == 2
    assert body["version"] == 1
    assert body["delivery_type"] == "in_house"

    new = _chem(db, "New Salt")
    assert new is not None
    assert new[1] == 50                 # current_qty
    assert new[3] is True               # in_house_registered
    assert new[4] == 5                  # reorder_level
    assert _chem(db, "Existing Acid")[1] == 35   # 10 + 25
    assert db.execute("SELECT COUNT(*) FROM chemical_purchase_items "
                      "WHERE purchase_id = %s", (body["id"],)).fetchone()[0] == 2


# --------------------------------------------------------------------------- #
# 2. an invalid later line leaves no partial write
# --------------------------------------------------------------------------- #
def test_invalid_later_line_rolls_back_everything(admin_client, db):
    existing_id = _seed_chem(admin_client, db, "Stocked", 10)
    r = _post(admin_client, _payload([
        _line("Fresh Chem", 50, is_new=True),
        _line("Stocked", 0, is_new=False, chemical_id=existing_id),
    ]))
    assert r.status_code == 400
    assert db.execute("SELECT COUNT(*) FROM chemical_purchases").fetchone()[0] == 0
    assert _chem(db, "Fresh Chem") is None
    assert _chem(db, "Stocked")[1] == 10


# --------------------------------------------------------------------------- #
# 3. outward never changes stock; new outward material stays hidden
# --------------------------------------------------------------------------- #
def test_outward_purchase_no_stock_change(admin_client, db):
    cid = _new_client(db, "Outward Client")
    r = _post(admin_client, _payload(
        [_line("Outward Only", 30, is_new=True)],
        delivery_type="outward", client_id=cid))
    assert r.status_code == 201, r.get_json()
    row = _chem(db, "Outward Only")
    assert row is not None
    assert row[1] == 0                  # no stock change
    assert row[3] is False              # in_house_registered = FALSE
    assert r.get_json()["client_id"] == cid


# --------------------------------------------------------------------------- #
# 4. outward requires a client
# --------------------------------------------------------------------------- #
def test_outward_requires_client(admin_client, db):
    r = _post(admin_client, _payload(
        [_line("No Client", 5)], delivery_type="outward"))
    assert r.status_code == 400
    assert db.execute("SELECT COUNT(*) FROM chemical_purchases").fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# 5. in_house rejects a client (route 400 + DB CHECK backstop)
# --------------------------------------------------------------------------- #
def test_in_house_rejects_client(admin_client, db):
    cid = _new_client(db, "InHouseCo")
    r = _post(admin_client, _payload(
        [_line("Wrong Client", 5)], delivery_type="in_house", client_id=cid))
    assert r.status_code == 400
    assert db.execute("SELECT COUNT(*) FROM chemical_purchases").fetchone()[0] == 0
    # The database CHECK is the final authority, not just the Pydantic model.
    with pytest.raises(psycopg.IntegrityError):
        db.execute(
            "INSERT INTO chemical_purchases "
            "(supplier_name, purchase_number, purchase_date, delivery_type, "
            " client_id, created_by) "
            "VALUES ('S', 'PO-CHK', '2026-01-15', 'in_house', %s, 'admin')",
            (cid,))
    db.rollback()


# --------------------------------------------------------------------------- #
# 6. the same material twice in one purchase is rejected
# --------------------------------------------------------------------------- #
def test_duplicate_material_in_purchase_rejected(admin_client, db):
    r = _post(admin_client, _payload([
        _line("Dup Chem", 10, is_new=True),
        _line("dup chem", 20, is_new=True),   # case-insensitive duplicate
    ]))
    assert r.status_code == 400
    assert db.execute("SELECT COUNT(*) FROM chemical_purchases").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM chemicals "
                      "WHERE lower(name) = 'dup chem'").fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# 7. a "new" name that already exists converges on the existing identity
# --------------------------------------------------------------------------- #
def test_same_name_new_material_identity(admin_client, db):
    existing_id = _seed_chem(admin_client, db, "Race Acid", 10)
    r = _post(admin_client, _payload([
        _line("race acid", 20, is_new=True),   # differs only by case
    ]))
    assert r.status_code == 201, r.get_json()
    assert db.execute("SELECT COUNT(*) FROM chemicals "
                      "WHERE lower(name) = 'race acid'").fetchone()[0] == 1
    assert _chem(db, "Race Acid")[1] == 30     # 10 + 20, no duplicate row
    item = r.get_json()["items"][0]
    assert item["chemical_id"] == existing_id


# --------------------------------------------------------------------------- #
# 8. outward-only material is absent from /api/chemicals but in the catalog
# --------------------------------------------------------------------------- #
def test_hidden_material_not_in_chemicals_list(admin_client, db):
    _seed_chem(admin_client, db, "Visible Chem", 5)
    cid = _new_client(db, "Hidden Client")
    assert _post(admin_client, _payload(
        [_line("Hidden Outward", 10)], delivery_type="outward",
        client_id=cid)).status_code == 201

    names = {c["name"] for c in admin_client.get("/api/chemicals").get_json()}
    assert "Visible Chem" in names
    assert "Hidden Outward" not in names

    catalog = admin_client.get("/api/chemicals/catalog").get_json()
    hidden = next(c for c in catalog if c["name"] == "Hidden Outward")
    assert hidden["in_house_registered"] is False


# --------------------------------------------------------------------------- #
# 9. an approved upload that stocks a hidden identity promotes it
# --------------------------------------------------------------------------- #
def test_hidden_material_promoted_on_stock(admin_client, db):
    cid = _new_client(db, "Promote Client")
    assert _post(admin_client, _payload(
        [_line("Promote Me", 10)], delivery_type="outward",
        client_id=cid)).status_code == 201
    assert _chem(db, "Promote Me")[3] is False

    uid = db.execute("INSERT INTO uploads (filename, status) "
                     "VALUES ('count.csv', 'uploaded') RETURNING id").fetchone()[0]
    db.execute("INSERT INTO upload_rows "
               "(upload_id, chemical_name, balance_this_month, upload_unit) "
               "VALUES (%s, 'Promote Me', 40, 'KG')", (uid,))
    db.commit()

    from raas_tracker.uploads import adjust_stock_from_upload
    assert adjust_stock_from_upload(db, uid, reviewed_by="admin") is True
    row = _chem(db, "Promote Me")
    assert row[1] == 40
    assert row[3] is True               # promoted on legitimate stocking
    names = {c["name"] for c in admin_client.get("/api/chemicals").get_json()}
    assert "Promote Me" in names


# --------------------------------------------------------------------------- #
# 10. latest receipt fields come from the newest valid in-house purchase
# --------------------------------------------------------------------------- #
def test_latest_receipt_fields(admin_client, db):
    assert _post(admin_client, _payload(
        [_line("Receipt Chem", 100)], supplier="First Supplier",
        number="PO-1", date="2026-01-10")).status_code == 201
    cid = _chem(db, "Receipt Chem")[0]
    assert _post(admin_client, _payload(
        [_line("Receipt Chem", 200, is_new=False, chemical_id=cid)],
        supplier="Second Supplier", number="PO-2",
        date="2026-02-01")).status_code == 201

    m = _material(admin_client, "Receipt Chem")
    assert m is not None
    assert m["last_purchase_date"] == "2026-02-01"
    assert m["last_purchase_quantity"] == 200
    assert m["supplier_name"] == "Second Supplier"
    assert m["projected_quantity"] is None
    assert m["required_quantity"] is None


# --------------------------------------------------------------------------- #
# 11. a cancelled purchase is excluded; the previous receipt becomes latest
# --------------------------------------------------------------------------- #
def test_cancelled_purchase_excluded_from_latest(admin_client, db):
    assert _post(admin_client, _payload(
        [_line("Cancel Latest", 100)], supplier="Older",
        number="PO-1", date="2026-01-10")).status_code == 201
    cid = _chem(db, "Cancel Latest")[0]
    r2 = _post(admin_client, _payload(
        [_line("Cancel Latest", 200, is_new=False, chemical_id=cid)],
        supplier="Newer", number="PO-2", date="2026-02-01"))
    assert r2.status_code == 201
    pid = r2.get_json()["id"]

    rc = admin_client.post(f"/api/chemical-purchases/{pid}/cancel",
                           json={"expected_version": 1, "reason": "void receipt"})
    assert rc.status_code == 200, rc.get_json()
    assert _chem(db, "Cancel Latest")[1] == 100   # 300 - 200 reversed

    m = _material(admin_client, "Cancel Latest")
    assert m["last_purchase_date"] == "2026-01-10"
    assert m["last_purchase_quantity"] == 100
    assert m["supplier_name"] == "Older"


# --------------------------------------------------------------------------- #
# 12. a correction applies only the stock difference
# --------------------------------------------------------------------------- #
def test_correction_applies_delta(admin_client, db):
    r = _post(admin_client, _payload([_line("Correct Chem", 100)]))
    assert r.status_code == 201
    pid = r.get_json()["id"]
    item_id = r.get_json()["items"][0]["id"]
    assert _chem(db, "Correct Chem")[1] == 100

    ru = admin_client.put(f"/api/chemical-purchases/{pid}", json={
        "expected_version": 1, "reason": "quantity fix",
        "line_patches": [{"id": item_id, "quantity": 150}]})
    assert ru.status_code == 200, ru.get_json()
    assert _chem(db, "Correct Chem")[1] == 150     # +50 delta, not +150
    assert ru.get_json()["version"] == 2
    assert ru.get_json()["items"][0]["quantity"] == 150


# --------------------------------------------------------------------------- #
# 13. cancellation reverses the CURRENT posted quantity (post-correction)
# --------------------------------------------------------------------------- #
def test_cancellation_after_correction(admin_client, db):
    r = _post(admin_client, _payload([_line("Correct Cancel", 100)]))
    pid = r.get_json()["id"]
    item_id = r.get_json()["items"][0]["id"]
    assert admin_client.put(f"/api/chemical-purchases/{pid}", json={
        "expected_version": 1, "reason": "fix",
        "line_patches": [{"id": item_id, "quantity": 150}]}).status_code == 200
    assert _chem(db, "Correct Cancel")[1] == 150

    rc = admin_client.post(f"/api/chemical-purchases/{pid}/cancel",
                           json={"expected_version": 2, "reason": "cancel"})
    assert rc.status_code == 200, rc.get_json()
    assert _chem(db, "Correct Cancel")[1] == 0      # reversed 150, not 100
    assert rc.get_json()["cancelled"] is True


# --------------------------------------------------------------------------- #
# 14. a reversal that would drive stock negative is blocked, nothing changes
# --------------------------------------------------------------------------- #
def test_negative_reversal_blocked(admin_client, db):
    r = _post(admin_client, _payload([_line("Block Chem", 100)]))
    pid = r.get_json()["id"]
    assert admin_client.post("/api/chemicals/update",
                             json={"name": "Block Chem", "delta": -60,
                                   "reason": "consumed"}).status_code == 200
    assert _chem(db, "Block Chem")[1] == 40

    rc = admin_client.post(f"/api/chemical-purchases/{pid}/cancel",
                           json={"expected_version": 1, "reason": "cancel"})
    assert rc.status_code == 400
    assert _chem(db, "Block Chem")[1] == 40         # unchanged
    row = db.execute("SELECT cancelled, version FROM chemical_purchases "
                     "WHERE id = %s", (pid,)).fetchone()
    assert row[0] is False and row[1] == 1


# --------------------------------------------------------------------------- #
# 15. repeated cancellation is a 409 and never reverses stock twice
# --------------------------------------------------------------------------- #
def test_repeated_cancellation_409(admin_client, db):
    r = _post(admin_client, _payload([_line("Once Cancel", 50)]))
    pid = r.get_json()["id"]
    assert admin_client.post(f"/api/chemical-purchases/{pid}/cancel",
                             json={"expected_version": 1,
                                   "reason": "first"}).status_code == 200
    assert _chem(db, "Once Cancel")[1] == 0

    rc = admin_client.post(f"/api/chemical-purchases/{pid}/cancel",
                           json={"expected_version": 2, "reason": "again"})
    assert rc.status_code == 409
    assert _chem(db, "Once Cancel")[1] == 0         # not reversed twice


# --------------------------------------------------------------------------- #
# 16. a stale expected_version is a 409
# --------------------------------------------------------------------------- #
def test_stale_version_409(admin_client, db):
    r = _post(admin_client, _payload([_line("Stale Chem", 50)]))
    pid = r.get_json()["id"]
    item_id = r.get_json()["items"][0]["id"]
    ru = admin_client.put(f"/api/chemical-purchases/{pid}", json={
        "expected_version": 99, "reason": "stale",
        "line_patches": [{"id": item_id, "quantity": 80}]})
    assert ru.status_code == 409
    assert _chem(db, "Stale Chem")[1] == 50
    assert db.execute("SELECT version FROM chemical_purchases WHERE id = %s",
                      (pid,)).fetchone()[0] == 1


# --------------------------------------------------------------------------- #
# 17. an audit failure rolls back the whole purchase (service-level)
# --------------------------------------------------------------------------- #
def test_audit_failure_rolls_back(admin_client, db, monkeypatch):
    existing_id = _seed_chem(admin_client, db, "Audit Stocked", 10)

    def _boom(*args, **kwargs):
        raise RuntimeError("audit sink down")

    monkeypatch.setattr(purchases, "log_audit_action", _boom)
    with pytest.raises(RuntimeError, match="audit sink down"):
        purchases.create_purchase(
            db, supplier_name="Audit Co", purchase_number="PO-AUDIT",
            purchase_date="2026-01-15", delivery_type="in_house",
            client_id=None,
            lines=[{"chemical_name": "Audit Chem", "quantity": 50,
                    "unit": "KG", "is_new": True},
                   {"chemical_name": "Audit Stocked", "quantity": 25,
                    "unit": "KG", "is_new": False,
                    "chemical_id": existing_id}],
            actor="admin")

    assert _chem(db, "Audit Chem") is None
    assert _chem(db, "Audit Stocked")[1] == 10
    assert db.execute("SELECT COUNT(*) FROM chemical_purchases").fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# 18. mutations are admin-only; reads are any authenticated identity
# --------------------------------------------------------------------------- #
def test_admin_only_mutations(user_client):
    body = _payload([_line("User Chem", 5)])
    assert user_client.post("/api/chemical-purchases",
                            json=body).status_code == 403
    assert user_client.put("/api/chemical-purchases/1",
                           json={"expected_version": 1,
                                 "reason": "x"}).status_code == 403
    assert user_client.post("/api/chemical-purchases/1/cancel",
                            json={"expected_version": 1,
                                  "reason": "x"}).status_code == 403
    # Reads stay open to any signed-in identity.
    assert user_client.get("/api/chemical-purchases").status_code == 200
    assert user_client.get("/api/chemicals/catalog").status_code == 200
    assert user_client.get("/api/chemicals/inventory").status_code == 200


# --------------------------------------------------------------------------- #
# 19. a once-stocked material stays visible after cancellation (stock 0)
# --------------------------------------------------------------------------- #
def test_once_stocked_visible_after_cancel(admin_client, db):
    r = _post(admin_client, _payload([_line("Once Stocked", 60)]))
    pid = r.get_json()["id"]
    assert _chem(db, "Once Stocked")[3] is True
    assert admin_client.post(f"/api/chemical-purchases/{pid}/cancel",
                             json={"expected_version": 1,
                                   "reason": "cancel"}).status_code == 200
    assert _chem(db, "Once Stocked")[1] == 0
    assert _chem(db, "Once Stocked")[3] is True     # never flips back
    names = {c["name"] for c in admin_client.get("/api/chemicals").get_json()}
    assert "Once Stocked" in names


# --------------------------------------------------------------------------- #
# 20. a backdated purchase never overrides a newer latest receipt
# --------------------------------------------------------------------------- #
def test_backdated_purchase_latest(admin_client, db):
    assert _post(admin_client, _payload(
        [_line("Backdate Chem", 100)], supplier="Newer",
        number="PO-NEW", date="2026-02-01")).status_code == 201
    cid = _chem(db, "Backdate Chem")[0]
    assert _post(admin_client, _payload(
        [_line("Backdate Chem", 50, is_new=False, chemical_id=cid)],
        supplier="Older", number="PO-OLD", date="2026-01-01")).status_code == 201

    m = _material(admin_client, "Backdate Chem")
    assert m["last_purchase_date"] == "2026-02-01"
    assert m["last_purchase_quantity"] == 100
    assert m["supplier_name"] == "Newer"
    assert _chem(db, "Backdate Chem")[1] == 150


# --------------------------------------------------------------------------- #
# 21. a correction may not blank the receipt's identity, and it must be audited
#     with a real before/after payload (review W2)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field", ["supplier_name", "purchase_number"])
def test_correction_rejects_a_blank_identity(admin_client, db, field):
    """`{"supplier_name": "  "}` used to store an EMPTY supplier on a NOT NULL
    column, while create refuses exactly that input.

    `purchase_number` is the worse of the two: it is the string snapshotted
    onto every run's evidence and the only handle an operator has on a receipt.
    The model strips whitespace, so the refusal is a 400 naming the field — the
    same field-error shape every other validation here uses.
    """
    r = _post(admin_client, _payload([_line("Blank Identity", 10)]))
    assert r.status_code == 201, r.get_json()
    pid = r.get_json()["id"]

    ru = admin_client.put(f"/api/chemical-purchases/{pid}", json={
        "expected_version": 1, "reason": "blank the header", field: "   "})
    assert ru.status_code == 400, ru.get_json()
    payload = ru.get_json()
    named = {row["field"] for row in payload.get("details", [])}
    assert field in named or field in (payload.get("error") or ""), payload

    # Nothing was written and no version was burned.
    row = db.execute("SELECT supplier_name, purchase_number, version "
                     "FROM chemical_purchases WHERE id = %s", (pid,)).fetchone()
    assert row == ("Acme Supplies", "PO-1001", 1), row


def test_update_purchase_service_refuses_a_blank_identity(admin_client, db):
    """The rule belongs to the service, not only to the route's Pydantic model:
    a CLI or direct caller must not be able to store a blank supplier either."""
    r = _post(admin_client, _payload([_line("Service Blank", 10)]))
    pid = r.get_json()["id"]

    with pytest.raises(ValueError, match="supplier_name"):
        purchases.update_purchase(db, pid, expected_version=1,
                                 reason="blank it", supplier_name="   ",
                                 actor="admin")
    row = db.execute("SELECT supplier_name, version FROM chemical_purchases "
                     "WHERE id = %s", (pid,)).fetchone()
    assert row == ("Acme Supplies", 1), row

    with pytest.raises(ValueError, match="purchase_number"):
        purchases.update_purchase(db, pid, expected_version=1,
                                  reason="blank it", purchase_number="\t ",
                                  actor="admin")
    row = db.execute("SELECT purchase_number, version FROM chemical_purchases "
                    "WHERE id = %s", (pid,)).fetchone()
    assert row == ("PO-1001", 1), row


def test_correction_audits_previous_and_new_values(admin_client, db):
    """A corrected receipt amount has to be RECOVERABLE from the audit row.

    The version stamp (`old_value="version=1"`) recorded nothing about what the
    receipt said before, so a corrected supplier, number, date or quantity was
    unrecoverable — while the sibling service in this branch snapshots the
    whole old formulation. Header fields plus every changed line go in now.
    Only changed lines are listed (a receipt may carry 100 of them and the
    audit column is bounded), but the header is recorded whole.
    """
    r = _post(admin_client, _payload(
        [_line("Audit Old", 100), _line("Audit Held", 50)],
        supplier="Old Supplier", number="PO-AUDIT-HDR", date="2026-01-15"))
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    pid = body["id"]
    by_name = {item["chemical_name"]: item for item in body["items"]}
    old_line = by_name["Audit Old"]

    ru = admin_client.put(f"/api/chemical-purchases/{pid}", json={
        "expected_version": 1, "reason": "supplier invoiced the wrong quantity",
        "supplier_name": "New Supplier", "purchase_number": "PO-AUDIT-HDR-2",
        "purchase_date": "2026-02-02",
        "line_patches": [{"id": old_line["id"], "quantity": 120}]})
    assert ru.status_code == 200, ru.get_json()

    row = db.execute("SELECT old_value, new_value FROM audit_logs "
                     "WHERE action = 'PURCHASE_UPDATE' AND entity_id = %s "
                     "ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
    assert row is not None, "the correction must leave an audit row"
    old = json.loads(row[0])
    new = json.loads(row[1])

    assert (old["supplier_name"], old["purchase_number"],
            old["purchase_date"]) == ("Old Supplier", "PO-AUDIT-HDR",
                                      "2026-01-15"), old
    assert {line["id"]: line["quantity"] for line in old["lines"]} == {
        old_line["id"]: 100}, old
    assert (new["supplier_name"], new["purchase_number"],
            new["purchase_date"]) == ("New Supplier", "PO-AUDIT-HDR-2",
                                      "2026-02-02"), new
    assert {line["id"]: line["quantity"] for line in new["lines"]} == {
        old_line["id"]: 120}, new
    # The untouched line is not claimed to have changed...
    assert all(line["id"] != by_name["Audit Held"]["id"] for line in new["lines"])
    # ...and the reason + version trail the payload carries is still there.
    assert new["reason"] == "supplier invoiced the wrong quantity", new
    assert (old["version"], new["version"]) == (1, 2), (old, new)


# --------------------------------------------------------------------------- #
# 22. the two new read routes must not turn a transient DB failure into a 500
#     (review W4) and must release the connection unconditionally (W5.3)
# --------------------------------------------------------------------------- #
class _Releasing:
    """Wrap a pooled connection so a route can be watched releasing it."""

    def __init__(self, conn):
        self._conn = conn
        self.closed = False

    def close(self):
        self.closed = True
        self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.mark.parametrize(
    "service,url",
    [("project_purchasing", "/api/chemicals/inventory"),
     ("get_demo", "/api/sales/1/demo-recipe")],
    ids=["inventory", "demo-get"],
)
def test_transient_db_failure_on_a_read_route_is_503(admin_client, monkeypatch,
                                                   service, url):
    """`except Exception:` in front of the service call swallowed the very
    exceptions the app maps to 503 by TYPE, and answered "server bug" for what
    is "try again". Roll back, release, and re-raise instead.
    """
    import flask_app

    seen = {}
    inner = flask_app.get_db

    def _get_db():
        seen["conn"] = _Releasing(inner())
        return seen["conn"]

    def _boom(*_args, **_kwargs):
        raise psycopg.OperationalError("connection failed: server closed it")

    monkeypatch.setattr(flask_app, "get_db", _get_db)
    monkeypatch.setattr(flask_app, service, _boom)

    r = admin_client.get(url)
    assert r.status_code == 503, r.get_json()
    assert r.get_json() == {"error": "database unavailable, retry shortly"}
    assert r.headers.get("Retry-After") == "5"
    assert seen["conn"].closed is True, "the pooled connection must be released"


def test_ordinary_bug_on_the_read_route_is_still_500(admin_client, monkeypatch):
    """The re-raise must not become a new catch-all: a real defect still answers
    500 with the generic body, and still releases the connection."""
    import flask_app

    seen = {}
    inner = flask_app.get_db

    def _get_db():
        seen["conn"] = _Releasing(inner())
        return seen["conn"]

    def _boom(*_args, **_kwargs):
        raise RuntimeError("a real defect")

    monkeypatch.setattr(flask_app, "get_db", _get_db)
    monkeypatch.setattr(flask_app, "project_purchasing", _boom)

    r = admin_client.get("/api/chemicals/inventory")
    assert r.status_code == 500, r.get_json()
    assert r.get_json() == {"error": "internal server error"}
    assert "Retry-After" not in r.headers
    assert seen["conn"].closed is True


def test_inventory_route_closes_even_when_the_inject_step_aborts(admin_client,
                                                                monkeypatch):
    """W5.3: the close cannot sit on the success path of a `try/except` the
    error path returns out of — an abort the except clause cannot match would
    hand a still-open transaction back to the pool.
    """
    import flask_app

    seen = {}
    inner = flask_app.get_db

    def _get_db():
        seen["conn"] = _Releasing(inner())
        return seen["conn"]

    class _Abort(BaseException):
        """Not an `Exception`, so no `except Exception:` can catch or release."""

    def _boom(*_args, **_kwargs):
        raise _Abort("interrupted mid-request")

    monkeypatch.setattr(flask_app, "get_db", _get_db)
    monkeypatch.setattr(flask_app, "project_purchasing", _boom)

    with pytest.raises(_Abort):
        admin_client.get("/api/chemicals/inventory")
    assert seen["conn"].closed is True


# --------------------------------------------------------------------------- #
# 23. an unexpected client field is a 400, never a silently ignored key (W5.1)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "method,path,body",
    [("post", "/api/chemical-purchases",
      _payload([_line("Forbid Chem", 10)], number="PO-FORBID-1")),
     ("put", "/api/chemical-purchases/1",
      {"expected_version": 1, "reason": "typo"}),
     ("post", "/api/chemical-purchases/1/cancel",
      {"expected_version": 1, "reason": "typo"})],
    ids=["create", "update", "cancel"],
)
def test_purchase_write_models_forbid_unknown_fields(admin_client, method, path,
                                                    body):
    """The recipe write models in this branch already refuse extras; the
    purchase family must not be the one place a mistyped key is dropped on the
    floor (a `suplier_name` typo then posts a receipt under the wrong name).
    """
    sent = dict(body)
    sent["suplier_name"] = "typo, not a field"
    r = getattr(admin_client, method)(path, json=sent)
    assert r.status_code == 400, r.get_json()
    details = r.get_json().get("details") or []
    assert any("suplier_name" in row["field"] for row in details), r.get_json()


def test_purchase_write_models_still_accept_a_valid_body(admin_client, db):
    """The refusal above is the EXTRA key, not the body: each model still
    accepts what the SPA actually sends."""
    r = _post(admin_client, _payload([_line("Forbid OK Chem", 10)],
                                     number="PO-FORBID-2"))
    assert r.status_code == 201, r.get_json()
    pid = r.get_json()["id"]
    assert _post(admin_client, _payload([_line("Forbid OK Chem 2", 5)],
                                        number="PO-FORBID-3")).status_code == 201

    assert admin_client.put(f"/api/chemical-purchases/{pid}", json={
        "expected_version": 1, "reason": "supplier name fix",
        "supplier_name": "Later Supplier"}).status_code == 200
    assert admin_client.post(f"/api/chemical-purchases/{pid}/cancel", json={
        "expected_version": 2, "reason": "voided"}).status_code == 200


def test_demo_write_models_forbid_unknown_fields(admin_client):
    """One case for the demo family (brief W5.1 says one per family is enough):
    a draft save with a key the model never declared is a 400 naming it."""
    r = admin_client.put("/api/sales/1/demo-recipe/draft", json={
        "expected_revision": 1, "products": [], "prodcts": []})
    assert r.status_code == 400, r.get_json()
    details = r.get_json().get("details") or []
    assert any("prodcts" in row["field"] for row in details), r.get_json()


# --------------------------------------------------------------------------- #
# 24. a malformed filter on the purchase list is a 400, not an unfiltered list
#     (review W5.2)
# --------------------------------------------------------------------------- #
def test_purchase_list_refuses_a_malformed_filter(admin_client, db):
    """`request.args.get("client_id", type=int)` drops an unparseable value, so
    `?client_id=abc` silently returned EVERY client's receipts — the worst
    default for a tenant filter a typo can disarm."""
    cid = _new_client(db, "Filter Co")
    assert _post(admin_client, _payload(
        [_line("Filter Chem", 10)], delivery_type="outward",
        client_id=cid)).status_code == 201

    for param in ("client_id", "chemical_id"):
        r = admin_client.get(f"/api/chemical-purchases?{param}=not-a-number")
        assert r.status_code == 400, (param, r.get_json())
        details = r.get_json().get("details") or []
        assert any(row["field"] == param for row in details), (param, r.get_json())

    # A well-formed filter and an absent one both still work.
    assert admin_client.get(
        f"/api/chemical-purchases?client_id={cid}").status_code == 200
    assert admin_client.get("/api/chemical-purchases").status_code == 200
