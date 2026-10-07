"""P1-13 / P1-16: a mutation and its audit row land in ONE transaction.

`log_audit_action(..., atomic=True)` commits immediately (`audit.py:141-142`),
so wherever a mutation commits *before* calling it, a failing audit INSERT
leaves the change durably committed with no audit row and a 500 to the client.
The worst case is `adjust_stock_from_upload`, which audits INSIDE a per-row
loop: each iteration's commit flushes that row's `UPDATE chemicals` plus every
earlier row, and its `except` returns False with no rollback — a partially
applied batch reported as a failure, undetectable by the caller.

The decisive test pattern throughout: monkeypatch the module's
`log_audit_action` to raise, call the mutation, then assert THE ENTITY IS
STILL PRESENT. Today the mutation is already committed, so it is gone and the
test fails — which is exactly the defect.

Transaction boundaries are asserted, not assumed: the `db` fixture connection
sees its own uncommitted work, so "still present" would be true even if the
function committed nothing at all. Every check below therefore opens an
INDEPENDENT connection, which can only see durably committed state.
"""
import pytest


# ==================== helpers ====================

@pytest.fixture()
def other(pg_dsn):
    """A second connection, so assertions see only COMMITTED state."""
    from chem_stock import get_connection
    conn = get_connection(pg_dsn)
    yield conn
    conn.close()


def _rows(other, action, entity_id=None):
    if entity_id is None:
        return other.execute(
            "SELECT entity_type, entity_id FROM audit_logs WHERE action = %s",
            (action,)).fetchall()
    return other.execute(
        "SELECT entity_type, entity_id FROM audit_logs "
        "WHERE action = %s AND entity_id = %s", (action, entity_id)).fetchall()


def _boom(module, monkeypatch, only=None, fail_on=None, calls=None):
    """Make `module.log_audit_action` raise.

    `only` restricts the failure to one action, so a function that audits an
    intermediate step (`INVOICE_ITEM_ADD` while seeding lines) does not fail
    before reaching the site under test.

    With `fail_on`/`calls` the first N-1 calls still run the REAL audit writer
    (each of which commits) and the Nth raises — which is exactly the situation
    the partial-write test needs, because the row-1 commit is what flushes
    row 1's UPDATE.
    """
    from raas_tracker.audit import log_audit_action as real
    def _maybe(conn, action, *a, **kw):
        if only is not None and action != only:
            return real(conn, action, *a, **kw)
        if calls is None:
            raise RuntimeError("audit insert failed")
        calls.append(action)
        if len(calls) == fail_on:
            raise RuntimeError("audit insert failed")
        return real(conn, action, *a, **kw)
    monkeypatch.setattr(module, "log_audit_action", _maybe)


def _company(db, name="AtomicCo"):
    from raas_tracker.companies import create_company
    return create_company(db, name=name)


def _sale_with_items(db, pi_number="PI-ATOM", product="Acetone"):
    from raas_tracker.sales import add_sale
    cid = _company(db)
    add_sale(db, {"pi_number": pi_number, "client_name": "AtomicCo",
                 "company_id": cid},
             [{"product_name": product, "quantity": 100,
               "unit_price": 12.5, "unit": "KG"}])
    sale_id = db.execute("SELECT id FROM sales WHERE pi_number = %s",
                         (pi_number,)).fetchone()[0]
    item_id = db.execute("SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id",
                         (sale_id,)).fetchone()[0]
    return cid, sale_id, item_id


def _recipe(db, cid, name="AtomicRecipe", product="Acetone"):
    from raas_tracker.recipes import add_recipe
    assert add_recipe(db, name, total_quantity=10, water_percentage=10,
                      company_id=cid, product_name=product)
    return db.execute("SELECT id FROM recipes WHERE name = %s", (name,)).fetchone()[0]


def _upload_results(names):
    return {
        "stats": {"total": len(names), "matched": 0, "last_month_mismatches": 0,
                  "this_month_mismatches": 0, "both_mismatches": 0,
                  "not_in_db": len(names), "not_in_upload": 0,
                  "match_percentage": 0.0},
        "matches": [], "last_month_mismatches": [], "this_month_mismatches": [],
        "both_mismatches": [{"name": n, "batch_number": "B1", "expiry_date": "",
                             "upload_unit": "KG", "upload_last": 0,
                             "upload_this": 5, "unit_match": True}
                            for n in names],
        "not_in_db": [], "not_in_upload": [],
    }


# ==================== CLASS 1 — partial writes ====================

def test_adjust_stock_from_upload_applies_nothing_when_a_row_fails(db, other, monkeypatch):
    """The most important test in this file.

    `adjust_stock_from_upload` audits inside its row loop, so each audit's
    commit durably flushes that row plus every earlier row. A failure on row 2
    therefore leaves row 1 changed and row 2 not — a half-applied stock
    correction, reported as a failure and indistinguishable from success to the
    caller. The whole batch must be one transaction.
    """
    from raas_tracker.stock import add_chemical
    from raas_tracker.uploads import adjust_stock_from_upload, save_upload

    add_chemical(db, "Acid", 10, "KG")
    add_chemical(db, "Methanol", 20, "KG")
    upload_id = save_upload(db, "half.xlsx", _upload_results(["Acid", "Methanol"]))

    import raas_tracker.uploads as up
    calls = []
    _boom(up, monkeypatch, fail_on=2, calls=calls)

    assert adjust_stock_from_upload(db, upload_id) is False
    assert len(calls) == 2, "row 2's audit was never reached"

    qty = dict(other.execute(
        "SELECT lower(name), current_qty FROM chemicals ORDER BY name").fetchall())
    assert qty == {"acid": 10, "methanol": 20}, (
        "partial application: the batch left stock half-updated "
        f"(committed: {qty})")
    assert _rows(other, "ADJUST_STOCK") == [], \
        "stock was audited for rows that must not have been written"
    assert other.execute("SELECT status FROM uploads WHERE id = %s",
                         (upload_id,)).fetchone()[0] != "adjusted"


def test_adjust_stock_from_upload_happy_path_persists_every_row(db, other):
    from raas_tracker.stock import add_chemical
    from raas_tracker.uploads import adjust_stock_from_upload, save_upload
    add_chemical(db, "Acid", 10, "KG")
    add_chemical(db, "Methanol", 20, "KG")
    upload_id = save_upload(db, "whole.xlsx", _upload_results(["Acid", "Methanol"]))

    assert adjust_stock_from_upload(db, upload_id) is True

    qty = dict(other.execute(
        "SELECT lower(name), current_qty FROM chemicals ORDER BY name").fetchall())
    assert qty == {"acid": 5, "methanol": 5}
    assert len(_rows(other, "ADJUST_STOCK")) == 2
    assert other.execute("SELECT status FROM uploads WHERE id = %s",
                         (upload_id,)).fetchone()[0] == "adjusted"


# ==================== CLASS 2 — the six delete paths ====================

def test_delete_company_survives_a_failed_audit(db, other, monkeypatch):
    from raas_tracker.companies import delete_company
    cid = _company(db)
    import raas_tracker.companies as mod
    _boom(mod, monkeypatch)
    with pytest.raises(RuntimeError):
        delete_company(db, cid)
    assert other.execute("SELECT COUNT(*) FROM companies WHERE id = %s",
                         (cid,)).fetchone()[0] == 1, \
        "COMPANY_DELETE was committed before its audit row could be written"
    assert _rows(other, "COMPANY_DELETE") == []


def test_delete_recipe_survives_a_failed_audit(db, other, monkeypatch):
    from raas_tracker.recipes import delete_recipe
    cid, _sale, _item = _sale_with_items(db, "PI-DELREC")
    rid = _recipe(db, cid)
    import raas_tracker.recipes as mod
    _boom(mod, monkeypatch)
    with pytest.raises(RuntimeError):
        delete_recipe(db, cid, "AtomicRecipe")
    assert other.execute("SELECT COUNT(*) FROM recipes WHERE id = %s",
                         (rid,)).fetchone()[0] == 1, \
        "RECIPE_DELETE was committed before its audit row could be written"
    assert _rows(other, "RECIPE_DELETE") == []


def test_delete_recipe_item_survives_a_failed_audit(db, other, monkeypatch):
    from raas_tracker.recipes import add_recipe_item, delete_recipe_item
    from raas_tracker.stock import add_chemical
    cid, _sale, _item = _sale_with_items(db, "PI-DELRI")
    _recipe(db, cid)
    add_chemical(db, "Salt", 5, "KG")
    assert add_recipe_item(db, cid, "AtomicRecipe", "Salt", 10.0)
    item_id = db.execute(
        "SELECT ri.id FROM recipe_items ri JOIN recipes r ON r.id = ri.recipe_id "
        "WHERE r.name = 'AtomicRecipe'").fetchone()[0]

    import raas_tracker.recipes as mod
    _boom(mod, monkeypatch)
    with pytest.raises(RuntimeError):
        delete_recipe_item(db, cid, "AtomicRecipe", "Salt")
    assert other.execute("SELECT COUNT(*) FROM recipe_items WHERE id = %s",
                         (item_id,)).fetchone()[0] == 1, \
        "RECIPE_ITEM_DELETE was committed before its audit row could be written"
    assert _rows(other, "RECIPE_ITEM_DELETE") == []


def test_delete_shipment_survives_a_failed_audit(db, other, monkeypatch):
    from raas_tracker.sales import add_shipment, delete_shipment
    _cid, sale_id, _item = _sale_with_items(db, "PI-DELSHIP")
    sid = add_shipment(db, sale_id, "2026-09-10", invoice_number="INV-S9")

    import raas_tracker.sales as mod
    _boom(mod, monkeypatch)
    with pytest.raises(RuntimeError):
        delete_shipment(db, sale_id, sid)
    assert other.execute("SELECT COUNT(*) FROM shipments WHERE id = %s",
                         (sid,)).fetchone()[0] == 1, \
        "SHIPMENT_DELETE was committed before its audit row could be written"
    assert _rows(other, "SHIPMENT_DELETE") == []


def test_delete_sale_item_survives_a_failed_audit(db, other, monkeypatch):
    from raas_tracker.sales import add_sale_item, delete_sale_item
    _cid, sale_id, _item = _sale_with_items(db, "PI-DELSI")
    iid = add_sale_item(db, sale_id, "Extra", 5, 1.0, "KG")

    import raas_tracker.sales as mod
    _boom(mod, monkeypatch)
    with pytest.raises(RuntimeError):
        delete_sale_item(db, sale_id, iid)
    assert other.execute("SELECT COUNT(*) FROM sale_items WHERE id = %s",
                         (iid,)).fetchone()[0] == 1, \
        "SALE_ITEM_DELETE was committed before its audit row could be written"
    assert _rows(other, "SALE_ITEM_DELETE") == []


def test_api_delete_upload_survives_a_failed_audit(admin_client, db, other, monkeypatch):
    from raas_tracker.uploads import save_upload
    upload_id = save_upload(db, "gone.xlsx", _upload_results(["Acid"]))
    import flask_app
    _boom(flask_app, monkeypatch)
    resp = admin_client.delete(f"/api/uploads/{upload_id}")
    assert resp.status_code == 500
    assert other.execute("SELECT COUNT(*) FROM uploads WHERE id = %s",
                         (upload_id,)).fetchone()[0] == 1, \
        "UPLOAD_DELETE was committed before its audit row could be written"
    assert _rows(other, "UPLOAD_DELETE") == []


# ==================== CLASS 2 — representative non-delete sites ====================

def test_create_user_survives_a_failed_audit(db, other, monkeypatch):
    import raas_tracker.auth as mod
    _boom(mod, monkeypatch)
    with pytest.raises(RuntimeError):
        mod.create_user(db, "atomicuser", "pass-123456")
    assert other.execute("SELECT COUNT(*) FROM users WHERE username = %s",
                         ("atomicuser",)).fetchone()[0] == 0, \
        "USER_CREATE was committed before its audit row could be written"
    # No USER_CREATE row naming this user (the fixture's admin/user rows are
    # pre-existing and are not what this call wrote).
    assert other.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE action = 'USER_CREATE' "
        "AND new_value LIKE %s", ("%atomicuser%",)).fetchone()[0] == 0


def test_create_company_survives_a_failed_audit(db, other, monkeypatch):
    import raas_tracker.companies as mod
    _boom(mod, monkeypatch)
    with pytest.raises(RuntimeError):
        mod.create_company(db, name="NeverCommitted")
    assert other.execute("SELECT COUNT(*) FROM companies WHERE name = %s",
                         ("NeverCommitted",)).fetchone()[0] == 0
    assert _rows(other, "COMPANY_CREATE") == []


def test_add_chemical_survives_a_failed_audit(db, other, monkeypatch):
    import raas_tracker.stock as mod
    _boom(mod, monkeypatch)
    with pytest.raises(RuntimeError):
        mod.add_chemical(db, "GhostChem", 5, "KG")
    assert other.execute("SELECT COUNT(*) FROM chemicals WHERE name = %s",
                         ("GhostChem",)).fetchone()[0] == 0, \
        "ADD_CHEMICAL was committed before its audit row could be written"
    assert _rows(other, "ADD_CHEMICAL") == []


def test_add_recipe_survives_a_failed_audit(db, other, monkeypatch):
    cid, _sale, _item = _sale_with_items(db, "PI-ADDREC")
    import raas_tracker.recipes as mod
    _boom(mod, monkeypatch)
    with pytest.raises(RuntimeError):
        mod.add_recipe(db, "GhostRecipe", 10, 10, company_id=cid,
                       product_name="Acetone")
    assert other.execute("SELECT COUNT(*) FROM recipes WHERE name = %s",
                         ("GhostRecipe",)).fetchone()[0] == 0
    assert _rows(other, "RECIPE_CREATE") == []


def test_save_upload_survives_a_failed_audit(db, other, monkeypatch):
    import raas_tracker.uploads as mod
    _boom(mod, monkeypatch)
    with pytest.raises(RuntimeError):
        mod.save_upload(db, "ghost.xlsx", _upload_results(["Acid"]))
    assert other.execute("SELECT COUNT(*) FROM uploads WHERE filename = %s",
                         ("ghost.xlsx",)).fetchone()[0] == 0, \
        "UPLOAD_CREATE was committed before its audit row could be written"
    assert _rows(other, "UPLOAD_CREATE") == []


def test_add_sale_item_survives_a_failed_audit(db, other, monkeypatch):
    from raas_tracker.sales import add_sale_item
    _cid, sale_id, _item = _sale_with_items(db, "PI-ADDITEM")
    import raas_tracker.sales as mod
    _boom(mod, monkeypatch)
    with pytest.raises(RuntimeError):
        add_sale_item(db, sale_id, "GhostLine", 5, 1.0, "KG")
    assert other.execute(
        "SELECT COUNT(*) FROM sale_items WHERE product_name = %s",
        ("GhostLine",)).fetchone()[0] == 0, \
        "SALE_ITEM_ADD was committed before its audit row could be written"
    assert _rows(other, "SALE_ITEM_ADD") == []


def test_create_invoice_survives_a_failed_audit(db, other, monkeypatch):
    from raas_tracker.sales import create_invoice
    _cid, sale_id, _item = _sale_with_items(db, "PI-INVOICE")
    import raas_tracker.sales as mod
    # Only INVOICE_CREATE fails: the seeded INVOICE_ITEM_ADD rows are part of
    # this same transaction and their audit must go with it.
    _boom(mod, monkeypatch, only="INVOICE_CREATE")
    with pytest.raises(RuntimeError):
        create_invoice(db, sale_id, "INV-GHOST")
    assert other.execute("SELECT COUNT(*) FROM invoices WHERE invoice_number = %s",
                         ("INV-GHOST",)).fetchone()[0] == 0, \
        "INVOICE_CREATE was committed before its audit row could be written"
    assert _rows(other, "INVOICE_CREATE") == []


# ==================== CLASS 3 — swallowed rollback ====================
#
# These three returned False with no `conn.rollback()`, so the failed write was
# left sitting in the caller's OPEN transaction. The function told the caller it
# did nothing while the database still held the change, one `commit()` away —
# and the pooled connection's `close()` commits (db.py:65), so the very next
# caller of that connection would make the "failed" write durable.
#
# So the assertion here is on the CALLER's connection, not a second one: the
# failed work must be gone from the open transaction, not merely uncommitted.


def test_approve_upload_row_failure_discards_the_write(db, monkeypatch):
    from raas_tracker.uploads import (approve_upload_row, create_approval_workflow,
                                      save_upload)
    upload_id = save_upload(db, "flow.xlsx", _upload_results(["Acid"]))
    row_id = db.execute("SELECT id FROM upload_rows WHERE upload_id = %s",
                        (upload_id,)).fetchone()[0]
    wid = create_approval_workflow(db, upload_id, row_id)

    import raas_tracker.uploads as mod
    _boom(mod, monkeypatch)
    assert approve_upload_row(db, wid, "OK", "c", "admin") is False
    assert db.execute("SELECT status FROM approval_workflow WHERE id = %s",
                      (wid,)).fetchone()[0] == "pending", \
        "the approval is still in the caller's open transaction after a failure"
    # And it really is discarded, not merely unread: the next commit must not
    # resurrect it.
    db.commit()
    assert db.execute("SELECT status FROM approval_workflow WHERE id = %s",
                      (wid,)).fetchone()[0] == "pending"


def test_reject_upload_row_failure_discards_the_write(db, monkeypatch):
    from raas_tracker.uploads import (reject_upload_row, create_approval_workflow,
                                      save_upload)
    upload_id = save_upload(db, "flow2.xlsx", _upload_results(["Acid"]))
    row_id = db.execute("SELECT id FROM upload_rows WHERE upload_id = %s",
                        (upload_id,)).fetchone()[0]
    wid = create_approval_workflow(db, upload_id, row_id)

    import raas_tracker.uploads as mod
    _boom(mod, monkeypatch)
    assert reject_upload_row(db, wid, "BAD", "c", "admin") is False
    assert db.execute("SELECT status FROM approval_workflow WHERE id = %s",
                      (wid,)).fetchone()[0] == "pending"
    db.commit()
    assert db.execute("SELECT status FROM approval_workflow WHERE id = %s",
                      (wid,)).fetchone()[0] == "pending"


def test_lock_reconciliation_period_failure_discards_the_write(db, monkeypatch):
    from raas_tracker.uploads import (create_reconciliation_period,
                                      lock_reconciliation_period)
    pid = create_reconciliation_period(db, "Sep 2026", "2026-09-01", "2026-09-30")

    import raas_tracker.uploads as mod
    _boom(mod, monkeypatch)
    assert lock_reconciliation_period(db, pid, "admin") is False
    assert db.execute("SELECT status FROM reconciliation_periods WHERE id = %s",
                      (pid,)).fetchone()[0] != "locked", \
        "the lock is still pending after a reported failure"
    db.commit()
    assert db.execute("SELECT status FROM reconciliation_periods WHERE id = %s",
                      (pid,)).fetchone()[0] != "locked"


# ==================== CLASS 4 — audit listing order ====================

def test_audit_listing_orders_by_insertion_not_by_the_timestamp_text(db, other):
    """`audit_logs.timestamp` is TEXT and historical rows were written by the
    malformed `to_char` format (month in the minute slot, 12-hour clock), so a
    textual ORDER BY interleaves pre- and post-migration rows wrongly. `id` is
    the append-only IDENTITY and the order the ledger was actually written in,
    so the listing must use it.
    """
    from raas_tracker.audit import get_audit_logs
    # Two rows in this order, but whose timestamp TEXT sorts the other way.
    db.execute("INSERT INTO audit_logs (action, entity_type, timestamp) "
               "VALUES ('ORDER_PROBE', 'x', '2026-10-07 03:00:00')")
    db.execute("INSERT INTO audit_logs (action, entity_type, timestamp) "
               "VALUES ('ORDER_PROBE', 'x', '2026-10-07 01:00:00')")
    db.commit()

    logs = get_audit_logs(other, entity_type="x", limit=50)
    ids = [log["id"] for log in logs if log["action"] == "ORDER_PROBE"]
    assert ids == sorted(ids, reverse=True), (
        "audit listing is not in reverse-insertion order: "
        f"{ids} (timestamps would sort differently)")


# ==================== happy-path regression ====================

def test_happy_path_still_persists_mutation_and_audit(db, other):
    """The fix must not trade commit-before-audit for audit-without-commit."""
    from raas_tracker.companies import create_company, delete_company
    from raas_tracker.stock import add_chemical
    cid = create_company(db, name="HappyCo")
    assert other.execute("SELECT COUNT(*) FROM companies WHERE id = %s",
                         (cid,)).fetchone()[0] == 1
    assert _rows(other, "COMPANY_CREATE") == [("company", cid)]

    assert add_chemical(db, "RealChem", 7, "KG")
    assert other.execute("SELECT current_qty FROM chemicals WHERE name = %s",
                         ("RealChem",)).fetchone()[0] == 7
    assert len(_rows(other, "ADD_CHEMICAL")) == 1

    assert delete_company(db, cid) is True
    assert other.execute("SELECT COUNT(*) FROM companies WHERE id = %s",
                         (cid,)).fetchone()[0] == 0
    assert len(_rows(other, "COMPANY_DELETE")) == 1


def test_happy_path_upload_and_stock_adjustment(db, other):
    from raas_tracker.stock import add_chemical
    from raas_tracker.uploads import adjust_stock_from_upload, save_upload
    add_chemical(db, "Acid", 10, "KG")
    upload_id = save_upload(db, "ok.xlsx", _upload_results(["Acid"]))
    assert other.execute("SELECT COUNT(*) FROM uploads WHERE id = %s",
                         (upload_id,)).fetchone()[0] == 1
    assert _rows(other, "UPLOAD_CREATE") == [("upload", upload_id)]

    assert adjust_stock_from_upload(db, upload_id) is True
    assert other.execute("SELECT current_qty FROM chemicals WHERE name = 'Acid'"
                         ).fetchone()[0] == 5
    assert len(_rows(other, "ADJUST_STOCK")) == 1


def test_happy_path_recipe_and_sale_mutations(db, other):
    from raas_tracker.recipes import add_recipe, delete_recipe
    from raas_tracker.sales import add_sale_item, delete_sale_item
    cid, sale_id, _item = _sale_with_items(db, "PI-HAPPY")
    assert add_recipe(db, "HappyRecipe", 10, 10, company_id=cid,
                      product_name="Acetone")
    rid = other.execute("SELECT id FROM recipes WHERE name = 'HappyRecipe'"
                        ).fetchone()[0]
    assert _rows(other, "RECIPE_CREATE", rid) == [("recipe", rid)]
    assert delete_recipe(db, cid, "HappyRecipe") is True
    assert other.execute("SELECT COUNT(*) FROM recipes WHERE id = %s",
                         (rid,)).fetchone()[0] == 0
    assert _rows(other, "RECIPE_DELETE", rid) == [("recipe", rid)]

    iid = add_sale_item(db, sale_id, "Extra", 5, 1.0, "KG")
    assert other.execute("SELECT COUNT(*) FROM sale_items WHERE id = %s",
                         (iid,)).fetchone()[0] == 1
    assert delete_sale_item(db, sale_id, iid) is True
    assert other.execute("SELECT COUNT(*) FROM sale_items WHERE id = %s",
                         (iid,)).fetchone()[0] == 0
    assert _rows(other, "SALE_ITEM_DELETE", iid) == [("sale_item", iid)]


def test_happy_path_create_user_and_invoice(db, other):
    from raas_tracker.auth import create_user
    from raas_tracker.sales import create_invoice
    uid = create_user(db, "happyuser", "pass-123456")
    assert other.execute("SELECT COUNT(*) FROM users WHERE id = %s",
                         (uid,)).fetchone()[0] == 1
    assert _rows(other, "USER_CREATE", uid) == [("user", uid)]

    _cid, sale_id, _item = _sale_with_items(db, "PI-HAPPYINV")
    out = create_invoice(db, sale_id, "INV-HAPPY")
    assert other.execute("SELECT COUNT(*) FROM invoices WHERE id = %s",
                         (out["invoice_id"],)).fetchone()[0] == 1
    assert _rows(other, "INVOICE_CREATE", out["invoice_id"]) == [("invoice", out["invoice_id"])]
