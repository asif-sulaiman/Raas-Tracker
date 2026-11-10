"""Slice 1 integration — production lock order + purchase/production concurrency.

Two layers of coverage:

1. A deterministic unit test pinning the production chemical sort key to
   ascending ``chemical_id`` (``recipes._aggregate_requirements``). Production
   used to sort by ``chemical_name``; purchases lock by ``chemical_id``. The two
   orders must agree or a production run and a purchase correction over the same
   chemicals acquire their row locks in opposite orders and deadlock. This test
   is the TDD red->green witness for the one-line sort-key change.

2. Four barrier-coordinated two-connection tests (A-D from the Task 1 lock
   audit). Each thread opens its OWN raw ``psycopg`` connection (a connection is
   not safe to share across threads, and the Flask test client is not thread-safe
   either), sets ``SET LOCAL lock_timeout = '5s'`` so a blocked lock acquisition
   surfaces as an error instead of hanging, and is released into its critical
   section by a ``threading.Barrier``. No timing-only sleeps: every wait is
   barrier-bounded and every DB wait is lock_timeout-bounded. A genuine deadlock
   surfaces as ``DeadlockDetected`` / ``LockNotAvailable`` and fails the test.

The services own their commit/rollback, so the threads drive them directly on
the raw connections (the same topology waitress uses: N threads, one connection
each). The ``db`` fixture truncates between cases and seeds the setup data.
"""
import threading
from urllib.parse import quote

import psycopg
import pytest

from raas_tracker import purchases, recipes
from raas_tracker._exceptions import LifecycleConflict

# Generous Python-side bounds: the barrier only forces overlap, and a slow box
# should not be reported as a defect. The DB-side lock_timeout (5s) is the real
# guard against an indefinite hang.
BARRIER_TIMEOUT = 20
LOCK_TIMEOUT = "5s"


# --------------------------------------------------------------------------- #
# setup helpers (patterns from tests/test_production_atomicity.py)
# --------------------------------------------------------------------------- #
def _company(admin_client, db, name):
    r = admin_client.post("/api/companies", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return db.execute("SELECT id FROM companies WHERE name = %s",
                      (name,)).fetchone()[0]


def _sale(admin_client, company_id, pi, products=("SortProd",)):
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": pi, "client_name": "x", "company_id": company_id},
        "items": [{"product_name": p, "quantity": 10, "unit_price": 5,
                   "unit": "KG"} for p in products]})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _remainder_lines(db, sale_id):
    rows = db.execute(
        """SELECT si.id,
                  si.quantity - COALESCE(SUM(ii.quantity), 0) AS remaining
             FROM sale_items si
             LEFT JOIN invoice_items ii ON ii.sale_item_id = si.id
            WHERE si.sale_id = %s
            GROUP BY si.id, si.quantity
            ORDER BY si.id""", (sale_id,)).fetchall()
    return [{"sale_item_id": r[0], "quantity": float(r[1])}
            for r in rows if r[1] is not None and float(r[1]) > 0]


def _invoice(admin_client, db, sale_id, number):
    r = admin_client.post(f"/api/sales/{sale_id}/invoices", json={
        "invoice_number": number, "invoice_date": "2026-01-15",
        "lines": _remainder_lines(db, sale_id)})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["invoice_id"]


def _chemical(admin_client, db, name, qty):
    r = admin_client.post("/api/chemicals",
                          json={"name": name, "qty": qty, "unit": "KG"})
    assert r.status_code == 200, r.get_json()
    return db.execute("SELECT id FROM chemicals WHERE name = %s",
                      (name,)).fetchone()[0]


def _recipe(admin_client, company_id, invoice_id, name, formulations):
    """formulations: {product_name: [(chemical_name, percentage), ...]}."""
    r = admin_client.post("/api/recipes", json={
        "name": name, "company_id": company_id, "invoice_id": invoice_id})
    assert r.status_code == 201, r.get_json()
    recipe_id = r.get_json()["recipe"]["id"]
    for product, ingredients in formulations.items():
        for chem, pct in ingredients:
            r = admin_client.post(
                f"/api/recipes/by-id/{recipe_id}/products/"
                f"{quote(product)}/items",
                json={"chemical": chem, "percentage": pct})
            assert r.status_code == 200, r.get_json()
    return recipe_id


def _stock(db, name):
    return db.execute("SELECT current_qty FROM chemicals WHERE name = %s",
                      (name,)).fetchone()[0]


def _purchase_payload(lines, number, delivery_type="in_house", client_id=None):
    body = {"supplier_name": "Concurrency Supplies", "purchase_number": number,
            "purchase_date": "2026-01-15", "delivery_type": delivery_type,
            "lines": lines}
    if client_id is not None:
        body["client_id"] = client_id
    return body


def _existing_line(chem_id, name, quantity):
    return {"chemical_name": name, "chemical_id": chem_id,
            "quantity": quantity, "unit": "KG", "is_new": False}


# --------------------------------------------------------------------------- #
# two-connection runner
# --------------------------------------------------------------------------- #
def _run_pair(pg_dsn, worker):
    """Run ``worker(index, conn)`` on two threads, released simultaneously.

    Each thread opens its own non-autocommit psycopg connection and opens its
    transaction with ``SET LOCAL lock_timeout`` BEFORE the barrier, so the guard
    is active for the whole service call that follows. Outcomes are captured
    (not re-raised) because Test D expects one thread to lose with a typed
    conflict; the test body decides what is acceptable. A failure before the
    barrier aborts it so the peer is not left waiting.
    """
    barrier = threading.Barrier(2, timeout=BARRIER_TIMEOUT)
    outcomes = [None, None]

    def _run(index):
        conn = psycopg.connect(pg_dsn, autocommit=False)
        try:
            # SET does not accept bind parameters; LOCK_TIMEOUT is a fixed
            # module constant (never user input), same pattern as db.py's own
            # SET LOCAL statement_timeout.
            conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
            barrier.wait(timeout=BARRIER_TIMEOUT)
            outcomes[index] = ("ok", worker(index, conn))
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test body
            outcomes[index] = ("err", exc)
            try:
                barrier.abort()
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    threads = [threading.Thread(target=_run, args=(i,), daemon=True)
               for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=BARRIER_TIMEOUT * 3)
        assert not t.is_alive(), "worker thread hung past the join timeout"
    assert all(o is not None for o in outcomes), "a worker never recorded an outcome"
    return outcomes


def _assert_no_lock_error(outcomes):
    """A deadlock or lock timeout is a concurrency defect, not an expected loss."""
    for status, value in outcomes:
        if status == "err":
            assert not isinstance(value, (psycopg.errors.LockNotAvailable,
                                          psycopg.errors.DeadlockDetected)), \
                f"lock contention surfaced as an error (deadlock proxy): {value!r}"


# --------------------------------------------------------------------------- #
# 1. TDD witness: production requirements are sorted by chemical_id
# --------------------------------------------------------------------------- #
def test_aggregate_requirements_sorted_by_chemical_id(admin_client, db):
    """Production must acquire chemical locks in id order, matching purchases.

    Two chemicals are created so id-order != name-order: 'Zinc Sort' first
    (lower id), 'Aqua Sort' second (higher id). Name-sort yields [Aqua, Zinc]
    (higher id first); id-sort yields [Zinc, Aqua]. The assertion fails on the
    old name-sort and passes once the key is chemical_id.
    """
    zinc_id = _chemical(admin_client, db, "Zinc Sort", 100)
    aqua_id = _chemical(admin_client, db, "Aqua Sort", 100)
    assert zinc_id < aqua_id                      # id-order != name-order
    cid = _company(admin_client, db, "SortKeyCo")
    sid = _sale(admin_client, cid, "PI-SORTKEY-1")
    inv = _invoice(admin_client, db, sid, "INV-SORTKEY-1")
    rid = _recipe(admin_client, cid, inv, "SortKeyRecipe",
                  {"SortProd": [("Zinc Sort", 10), ("Aqua Sort", 10)]})

    recipe = recipes._require_recipe(db, rid)
    rows = recipes._aggregate_requirements(
        db, recipe, [{"product_name": "SortProd", "quantity": 10}],
        require_balanced=False)
    ids = [r["chemical_id"] for r in rows]
    assert ids == sorted(ids), f"requirements not id-sorted: {ids}"
    assert set(ids) == {zinc_id, aqua_id}


# --------------------------------------------------------------------------- #
# Test A: purchase correction vs production on overlapping chemicals
# --------------------------------------------------------------------------- #
def test_correction_vs_production_no_deadlock(admin_client, db, pg_dsn):
    """A correction (incoming lines reversed) and a production run share two
    chemicals. Both must lock in id order; one blocks, both commit, and the
    final stock reflects both deltas with no lost update.
    """
    zinc_id = _chemical(admin_client, db, "Zinc A", 100)   # lower id
    aqua_id = _chemical(admin_client, db, "Aqua B", 100)   # higher id
    assert zinc_id < aqua_id

    # An in-house purchase posting 10 of each -> stock 110 each, version 1.
    r = admin_client.post("/api/chemical-purchases", json=_purchase_payload(
        [_existing_line(zinc_id, "Zinc A", 10),
         _existing_line(aqua_id, "Aqua B", 10)], "PO-CONC-A"))
    assert r.status_code == 201, r.get_json()
    pid = r.get_json()["id"]
    item_by_chem = {i["chemical_id"]: i["id"] for i in r.get_json()["items"]}

    cid = _company(admin_client, db, "ConcACo")
    sid = _sale(admin_client, cid, "PI-CONC-A")
    inv = _invoice(admin_client, db, sid, "INV-CONC-A")
    rid = _recipe(admin_client, cid, inv, "ConcARecipe",
                  {"SortProd": [("Zinc A", 10), ("Aqua B", 10)]})

    def _correction(index, conn):
        # Incoming patch order is reversed (B then A); the service sorts to id.
        return purchases.update_purchase(
            conn, pid, expected_version=1, reason="concurrent correction",
            line_patches=[{"id": item_by_chem[aqua_id], "quantity": 15},
                          {"id": item_by_chem[zinc_id], "quantity": 15}],
            actor="admin")

    def _production(index, conn):
        return recipes.create_production_run(
            conn, cid, rid, [{"product_name": "SortProd", "quantity": 10}],
            batch_number="BATCH-CONC-A", atomic=True)

    def worker(index, conn):
        # The runner's barrier releases both threads here at the same instant,
        # after each has opened its transaction with the lock_timeout guard.
        return _correction(index, conn) if index == 0 else _production(index, conn)

    outcomes = _run_pair(pg_dsn, worker)
    _assert_no_lock_error(outcomes)
    assert all(status == "ok" for status, _ in outcomes), \
        f"both writers must commit: {outcomes!r}"

    # 110 + 5 (correction) - 1 (production) = 114, independent of commit order.
    assert _stock(db, "Zinc A") == 114
    assert _stock(db, "Aqua B") == 114
    assert db.execute("SELECT version FROM chemical_purchases WHERE id = %s",
                      (pid,)).fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM production_runs").fetchone()[0] == 1


# --------------------------------------------------------------------------- #
# Test B: two purchases with reversed incoming row order
# --------------------------------------------------------------------------- #
def test_two_purchases_reversed_row_order_no_deadlock(admin_client, db, pg_dsn):
    """Purchase 1 sends [Y, X], purchase 2 sends [X, Y]. Both must internally
    lock id-ascending, so neither deadlocks and stock is the sum of both.
    """
    x_id = _chemical(admin_client, db, "X Chem", 0)   # lower id
    y_id = _chemical(admin_client, db, "Y Chem", 0)   # higher id
    assert x_id < y_id

    def worker(index, conn):
        if index == 0:
            lines = [_existing_line(y_id, "Y Chem", 20),
                     _existing_line(x_id, "X Chem", 10)]   # reversed [Y, X]
            number = "PO-CONC-B1"
        else:
            lines = [_existing_line(x_id, "X Chem", 30),
                     _existing_line(y_id, "Y Chem", 40)]   # [X, Y]
            number = "PO-CONC-B2"
        return purchases.create_purchase(
            conn, supplier_name="Concurrency Supplies", purchase_number=number,
            purchase_date="2026-01-15", delivery_type="in_house",
            client_id=None, lines=lines, actor="admin")

    outcomes = _run_pair(pg_dsn, worker)
    _assert_no_lock_error(outcomes)
    assert all(status == "ok" for status, _ in outcomes), \
        f"both purchases must commit: {outcomes!r}"

    assert _stock(db, "X Chem") == 40    # 10 + 30
    assert _stock(db, "Y Chem") == 60    # 20 + 40
    assert db.execute("SELECT COUNT(*) FROM chemical_purchases").fetchone()[0] == 2


# --------------------------------------------------------------------------- #
# Test C: concurrent new-material registration (identity race)
# --------------------------------------------------------------------------- #
def test_concurrent_new_material_registration_one_identity(admin_client, db, pg_dsn):
    """Two purchases register the same new material (differing case) at once.

    INSERT ... ON CONFLICT (lower(name)) DO NOTHING + re-SELECT FOR UPDATE must
    converge both onto one identity: exactly one chemical row, both purchases
    reference it, stock is the sum, and no deadlock.
    """
    def worker(index, conn):
        name = "Race Material" if index == 0 else "race material"
        qty = 10 if index == 0 else 20
        return purchases.create_purchase(
            conn, supplier_name="Concurrency Supplies",
            purchase_number=f"PO-CONC-C{index}", purchase_date="2026-01-15",
            delivery_type="in_house", client_id=None,
            lines=[{"chemical_name": name, "quantity": qty, "unit": "KG",
                    "is_new": True}],
            actor="admin")

    outcomes = _run_pair(pg_dsn, worker)
    _assert_no_lock_error(outcomes)
    assert all(status == "ok" for status, _ in outcomes), \
        f"both registrations must converge and commit: {outcomes!r}"

    rows = db.execute(
        "SELECT id FROM chemicals WHERE lower(name) = 'race material'").fetchall()
    assert len(rows) == 1, "exactly one identity for the raced name"
    chem_id = rows[0][0]
    # The canonical name is whichever case won the INSERT; assert by id.
    qty = db.execute("SELECT current_qty FROM chemicals WHERE id = %s",
                     (chem_id,)).fetchone()[0]
    assert qty == 30    # 10 + 20, no duplicate row
    items = db.execute(
        "SELECT DISTINCT chemical_id FROM chemical_purchase_items").fetchall()
    assert [r[0] for r in items] == [chem_id]
    assert db.execute("SELECT COUNT(*) FROM chemical_purchases").fetchone()[0] == 2


# --------------------------------------------------------------------------- #
# Test D: repeated cancellation (idempotent guard under the header lock)
# --------------------------------------------------------------------------- #
def test_concurrent_cancellation_reverses_stock_once(admin_client, db, pg_dsn):
    """Two threads cancel the same purchase at the same expected version.

    One wins the header FOR UPDATE and reverses stock (version bumps); the other
    reads cancelled=TRUE and loses with a LifecycleConflict (409). Stock is
    reversed exactly once and only one cancellation is audited.
    """
    r = admin_client.post("/api/chemical-purchases", json=_purchase_payload(
        [{"chemical_name": "Once Cancel Race", "quantity": 50, "unit": "KG",
          "is_new": True}], "PO-CONC-D"))
    assert r.status_code == 201, r.get_json()
    pid = r.get_json()["id"]
    assert _stock(db, "Once Cancel Race") == 50

    def worker(index, conn):
        return purchases.cancel_purchase(
            conn, pid, expected_version=1, reason=f"cancel-{index}", actor="admin")

    outcomes = _run_pair(pg_dsn, worker)
    _assert_no_lock_error(outcomes)

    statuses = [status for status, _ in outcomes]
    assert statuses.count("ok") == 1, f"exactly one cancel may win: {outcomes!r}"
    losers = [value for status, value in outcomes if status == "err"]
    assert len(losers) == 1 and isinstance(losers[0], LifecycleConflict), \
        f"the loser must get a typed 409 conflict: {losers!r}"

    assert _stock(db, "Once Cancel Race") == 0          # reversed exactly once
    row = db.execute("SELECT cancelled, version FROM chemical_purchases "
                     "WHERE id = %s", (pid,)).fetchone()
    assert row[0] is True and row[1] == 2
    assert db.execute("SELECT COUNT(*) FROM audit_logs "
                      "WHERE action = 'PURCHASE_CANCEL' AND entity_id = %s",
                      (pid,)).fetchone()[0] == 1


# --------------------------------------------------------------------------- #
# Slice 2: a referenced outward source cannot be mutated out from under a run
# --------------------------------------------------------------------------- #
def _outward_purchase(admin_client, db, client_id, chem_id, name, *,
                      quantity=25, number="PO-SRC-CONC"):
    """One outward receipt for THIS client of an existing material.

    Outward lines never touch stock, so the purchase holds no chemical lock:
    the only row both threads below contend on is the purchase header, which
    makes the race deterministic (the reference already exists, so either order
    gives the same verdict) instead of timing-dependent.
    """
    r = admin_client.post("/api/chemical-purchases", json=_purchase_payload(
        [_existing_line(chem_id, name, quantity)], number,
        delivery_type="outward", client_id=client_id))
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"], r.get_json()["items"][0]["id"]


def _outward_recipe(admin_client, db, cid, invoice_id, name, chem_name, line_id,
                    pct=50):
    """A recipe whose only ingredient is sourced from an outward purchase line."""
    r = admin_client.post("/api/recipes", json={
        "name": name, "company_id": cid, "invoice_id": invoice_id})
    assert r.status_code == 201, r.get_json()
    recipe_id = r.get_json()["recipe"]["id"]
    r = admin_client.post(
        f"/api/recipes/by-id/{recipe_id}/products/{quote('SortProd')}/items",
        json={"chemical": chem_name, "percentage": pct, "source_type": "outward",
              "outward_purchase_line_id": line_id})
    assert r.status_code == 200, r.get_json()
    return recipe_id


def _line_quantity(db, line_id):
    return db.execute("SELECT quantity FROM chemical_purchase_items WHERE id = %s",
                      (line_id,)).fetchone()[0]


def test_referenced_source_correction_cannot_bypass_production(admin_client, db,
                                                              pg_dsn):
    """Correction vs production on a frozen source line, in either arrival order.

    The recipe reference pre-exists, so the freeze rule holds whichever thread
    wins the header lock: production commits (and locks the purchase header FOR
    UPDATE before re-validating), the corrector loses with a typed 409, and the
    receipt quantity is unchanged. Neither order may deadlock, and the outward
    material must not touch in-house stock.
    """
    chem_id = _chemical(admin_client, db, "Outward Mat", 0)
    cid = _company(admin_client, db, "SrcConcCo")
    sid = _sale(admin_client, cid, "PI-SRC-CONC-1")
    inv = _invoice(admin_client, db, sid, "INV-SRC-CONC-1")
    pid, line_id = _outward_purchase(admin_client, db, cid, chem_id,
                                     "Outward Mat")
    rid = _outward_recipe(admin_client, db, cid, inv, "SrcConcRecipe",
                          "Outward Mat", line_id)

    def worker(index, conn):
        if index == 0:
            return purchases.update_purchase(
                conn, pid, expected_version=1, reason="try to move a frozen source",
                line_patches=[{"id": line_id, "quantity": 99}], actor="admin")
        return recipes.create_production_run(
            conn, cid, rid, [{"product_name": "SortProd", "quantity": 10}],
            batch_number="BATCH-SRC-CONC", atomic=True)

    outcomes = _run_pair(pg_dsn, worker)
    _assert_no_lock_error(outcomes)

    status, value = outcomes[0]
    assert status == "err", f"a referenced line is immutable: {value!r}"
    assert isinstance(value, LifecycleConflict), outcomes
    assert "referenced" in str(value).lower(), outcomes
    assert outcomes[1][0] == "ok", f"production must never lose to the freeze: {outcomes[1]!r}"

    assert float(_line_quantity(db, line_id)) == 25.0
    assert db.execute("SELECT version FROM chemical_purchases WHERE id = %s",
                      (pid,)).fetchone()[0] == 1
    assert _stock(db, "Outward Mat") == 0, "outward never deducts in-house stock"
    evidence = db.execute(
        "SELECT source_type, deducted_qty, required_qty FROM production_run_items "
        "WHERE outward_purchase_line_id = %s", (line_id,)).fetchall()
    assert evidence == [("outward", 0, 5.0)], evidence


def test_referenced_source_cancel_cannot_bypass_production(admin_client, db,
                                                           pg_dsn):
    """Same race against cancellation: the whole purchase is blocked while any
    line is referenced, so a run can never be left pointing at a cancelled line."""
    chem_id = _chemical(admin_client, db, "Cancel Mat", 0)
    cid = _company(admin_client, db, "CancelConcCo")
    sid = _sale(admin_client, cid, "PI-SRC-CONC-2")
    inv = _invoice(admin_client, db, sid, "INV-SRC-CONC-2")
    pid, line_id = _outward_purchase(admin_client, db, cid, chem_id,
                                     "Cancel Mat", number="PO-SRC-CONC-2")
    rid = _outward_recipe(admin_client, db, cid, inv, "CancelConcRecipe",
                          "Cancel Mat", line_id)

    def worker(index, conn):
        if index == 0:
            return purchases.cancel_purchase(
                conn, pid, expected_version=1, reason="try to cancel a used source",
                actor="admin")
        return recipes.create_production_run(
            conn, cid, rid, [{"product_name": "SortProd", "quantity": 10}],
            batch_number="BATCH-SRC-CONC-2", atomic=True)

    outcomes = _run_pair(pg_dsn, worker)
    _assert_no_lock_error(outcomes)

    status, value = outcomes[0]
    assert status == "err" and isinstance(value, LifecycleConflict), outcomes
    assert "referenced" in str(value).lower(), outcomes
    assert outcomes[1][0] == "ok", outcomes[1]
    assert db.execute("SELECT cancelled FROM chemical_purchases WHERE id = %s",
                      (pid,)).fetchone()[0] is False
    assert db.execute("SELECT COUNT(*) FROM production_run_items "
                      "WHERE outward_purchase_line_id = %s", (line_id,)).fetchone()[0] == 1


def test_client_reassignment_cannot_invalidate_a_referenced_source(admin_client,
                                                                   db):
    """The outward source belongs to the PI's client, so moving the PI to another
    client would silently orphan it: the guard blocks the move, and production
    re-validates the pairing even when the move was forced through the back door.
    """
    chem_id = _chemical(admin_client, db, "Move Mat", 0)
    cid_a = _company(admin_client, db, "MoveCoA")
    cid_b = _company(admin_client, db, "MoveCoB")
    sid = _sale(admin_client, cid_a, "PI-SRC-MOVE")
    inv = _invoice(admin_client, db, sid, "INV-SRC-MOVE")
    pid, line_id = _outward_purchase(admin_client, db, cid_a, chem_id,
                                     "Move Mat", number="PO-SRC-MOVE")
    rid = _outward_recipe(admin_client, db, cid_a, inv, "MoveRecipe",
                          "Move Mat", line_id)
    sale_item_id = db.execute("SELECT id FROM sale_items WHERE sale_id = %s",
                              (sid,)).fetchone()[0]

    # Same PI number, same quantity/price (no rebasing) - only the client moves.
    r = admin_client.put(f"/api/sales/{sid}", json={
        "header": {"pi_number": "PI-SRC-MOVE", "company_id": cid_b},
        "items": [{"id": sale_item_id, "product_name": "SortProd", "quantity": 10,
                   "unit_price": 5, "unit": "KG"}],
        "removedIds": []})
    assert r.status_code == 409, r.get_json()
    assert "client" in r.get_json()["error"].lower(), r.get_json()
    assert db.execute("SELECT company_id FROM sales WHERE id = %s",
                      (sid,)).fetchone()[0] == cid_a

    # Bypass the guard with raw SQL: production must refuse at run time rather
    # than record evidence for another client's receipt.
    db.execute("UPDATE sales SET company_id = %s WHERE id = %s", (cid_b, sid))
    db.commit()
    with pytest.raises(ValueError) as exc:
        recipes.create_production_run(
            db, cid_a, rid, [{"product_name": "SortProd", "quantity": 10}],
            batch_number="BATCH-SRC-MOVE", atomic=True)
    db.rollback()
    assert "client" in str(exc.value).lower(), exc.value
    assert db.execute("SELECT COUNT(*) FROM production_runs").fetchone()[0] == 0