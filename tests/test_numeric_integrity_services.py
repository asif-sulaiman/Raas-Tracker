"""Phase 2-B: the OUTPUT boundary — nothing non-finite can be WRITTEN.

Phase 2-A put two walls at the HTTP **input** boundary:

* ``_FiniteJSONProvider`` rejects the bare literals ``NaN`` / ``Infinity`` /
  ``-Infinity`` before a route body ever runs, and
* ``_FiniteModel(allow_inf_nan=False)`` covers the eight money/quantity models,
  so even a quoted ``"Infinity"`` cannot survive coercion.

Both are properties of *one* ingress: a Flask JSON request body. Every other
caller walks straight past them — a service calling another service, a cell
parsed out of an uploaded spreadsheet, a CLI argument, a JSON file on disk, a
test. Phase 2-B is the wall underneath, at the point of the write.

Every test here calls a service **directly** — no test client, no parser, no
Pydantic model. That is the point: reverting Phase 2-A must not weaken any of
them, and together they are the evidence that 2-B stands on its own.

Each rejection asserts the exception **and** that the database is unchanged. A
guard that raises after a partial write is not a fix, and the worst place for
one is a service that touches a row before it validates — ``record_sale_payment``
writes ``sales.maturity_date`` *before* the payment INSERT, so a guard placed
after that line would leave a mutation behind on every rejection.

The happy paths matter as much as the rejections. Every guard here is about
FINITENESS only: overpayments, big-but-finite magnitudes, and the ``0`` /
negative values that remain legal on the columns this file guards must behave
exactly as they did before.

**One exception, added after the first pass.** The sign rule on
``sale_items.quantity`` / ``unit_price`` is a deliberate reversal and lives in
``sales._positive_values``; it is pinned in full in
``tests/test_sale_item_sign_rule.py``. Two tests here were inverted in place
because they asserted the opposite — both are marked INVERTED at the point of
change and say why.
"""
import json

import pytest

from raas_tracker.companies import create_company
from raas_tracker.recipes import (
    add_recipe,
    add_recipe_item,
    update_recipe,
    update_recipe_item,
)
from raas_tracker.sales import (
    InvoicedLineConflict,
    add_sale,
    add_sale_item,
    create_invoice,
    get_sale_by_id,
    get_sale_invoice_total,
    get_sale_total_paid,
    mark_invoice_paid,
    move_sale_to_stage,
    record_sale_payment,
    update_sale_full,
    update_sale_item,
    update_sale_payment_record,
)
from raas_tracker.stock import (
    add_chemical,
    add_unit_conversion,
    import_from_json,
    set_reorder_level,
    update_stock,
)
from raas_tracker.uploads import adjust_stock_from_upload, compare_stock_upload, save_upload

NON_FINITE = pytest.mark.parametrize(
    "bad", [float("inf"), float("-inf"), float("nan")], ids=["inf", "-inf", "nan"])


# ----------------------------------------------------------------- helpers --

def _sale(db, pi="PI-NF", total=100.0, company_id=None):
    return add_sale(db, {"pi_number": pi, "client_name": "NF Co", "company_id": company_id},
                    [{"product_name": "W", "quantity": 10, "unit_price": total / 10}])


def _sale_at_payment_due(db):
    """A sale parked in ``payment_due`` — the state the cascade completes FROM."""
    sid = _sale(db)
    move_sale_to_stage(db, sid, "payment_due", "test")
    return sid


def _sale_row(db, sale_id):
    return db.execute(
        "SELECT stage, payment_amount, maturity_date FROM sales WHERE id = %s",
        (sale_id,)).fetchone()


def _payment_count(db, sale_id=None):
    if sale_id is None:
        return db.execute("SELECT count(*) FROM sale_payments").fetchone()[0]
    return db.execute("SELECT count(*) FROM sale_payments WHERE sale_id = %s",
                      (sale_id,)).fetchone()[0]


def _audit_count(db, action):
    return db.execute("SELECT count(*) FROM audit_logs WHERE action = %s",
                      (action,)).fetchone()[0]


def _company_with_product(db, name="NF Co", product="Soap"):
    """A company plus a registered sale line, which `add_recipe` demands."""
    cid = create_company(db, name=name)
    add_sale(db, {"pi_number": f"PI-{name}", "client_name": name, "company_id": cid},
             [{"product_name": product, "quantity": 5, "unit_price": 10, "unit": "KG"}])
    return cid


# ==================== 1. record_sale_payment — the forgery cascade ============

@NON_FINITE
def test_record_sale_payment_rejects_non_finite_and_writes_nothing(db, bad):
    sid = _sale_at_payment_due(db)
    with pytest.raises(ValueError):
        record_sale_payment(db, sid, "2026-10-30", bad)

    assert _payment_count(db, sid) == 0
    assert get_sale_total_paid(db, sid) == 0
    assert _audit_count(db, "SALE_PAYMENT") == 0


def test_record_sale_payment_infinity_cannot_complete_the_sale(db):
    """The headline forgery: ``inf >= invoice_total`` used to auto-complete it.

    ``record_sale_payment`` -> ``_complete_if_paid`` sees ``inf >= 100`` and
    moves the sale to ``completed``. Money is marked received that never
    arrived, and ``sales.stage`` drives the whole pipeline.
    """
    sid = _sale_at_payment_due(db)
    create_invoice(db, sid, "INV-CASCADE")
    assert _sale_row(db, sid)[0] == "payment_due"

    with pytest.raises(ValueError):
        record_sale_payment(db, sid, "2026-10-30", float("inf"))

    assert _sale_row(db, sid)[0] == "payment_due", "a forged payment completed the sale"
    assert get_sale_by_id(db, sid)["stage"] == "payment_due"
    assert db.execute(
        "SELECT count(*) FROM sales_stage_history "
        "WHERE sale_id = %s AND to_stage = 'completed'", (sid,)).fetchone()[0] == 0


def test_record_sale_payment_infinity_cannot_pay_the_invoice(db):
    """The other half of the cascade: ``_resync_invoice_payment_status`` flips
    the linked invoice to 'paid' because ``inf >= amount``."""
    sid = _sale_at_payment_due(db)
    inv = create_invoice(db, sid, "INV-PAID")["invoice_id"]
    before = db.execute("SELECT status FROM invoices WHERE id = %s", (inv,)).fetchone()[0]
    assert before != "paid"

    with pytest.raises(ValueError):
        record_sale_payment(db, sid, "2026-10-30", float("inf"))

    assert db.execute("SELECT status FROM invoices WHERE id = %s",
                      (inv,)).fetchone()[0] == before
    assert _payment_count(db) == 0


def test_record_sale_payment_rejection_does_not_touch_maturity_date(db):
    """The partial-write trap, stated as a test.

    ``record_sale_payment`` writes ``sales.maturity_date`` at :404, *before* the
    INSERT at :412. A finiteness guard placed after that line would still leave
    the header mutated on every rejection.
    """
    sid = _sale_at_payment_due(db)
    assert _sale_row(db, sid)[2] is None

    with pytest.raises(ValueError):
        record_sale_payment(db, sid, "2026-10-30", float("inf"),
                            maturity_date="2026-12-31")

    assert _sale_row(db, sid)[2] is None, "a rejected payment still rewrote maturity_date"


def test_record_sale_payment_still_rejects_zero_and_negative(db):
    """The pre-existing business rule is untouched — and note that for ``nan``
    it never fired in the first place, which is why finiteness is a separate
    check rather than a reordering of this one."""
    sid = _sale_at_payment_due(db)
    for bad in (0, 0.0, -5):
        with pytest.raises(ValueError):
            record_sale_payment(db, sid, "2026-10-30", bad)
    assert _payment_count(db, sid) == 0


def test_record_sale_payment_still_accepts_finite_amounts(db):
    """Finiteness is not a magnitude cap and not a policy change."""
    sid = _sale_at_payment_due(db)
    assert record_sale_payment(db, sid, "2026-10-30", 0.01)["total_paid"] == 0.01
    # Big-but-finite: finite AND storable in a REAL (float4) column.
    assert record_sale_payment(db, sid, "2026-10-31", 1e30)["total_paid"] == 1e30
    assert _payment_count(db, sid) == 2
    # ...and the guard does not stop the legitimate full-payment completion.
    record_sale_payment(db, sid, "2026-11-01", 99.99)
    assert _sale_row(db, sid)[0] == "completed"


# ========================== 2. mark_invoice_paid ============================

@NON_FINITE
def test_mark_invoice_paid_rejects_non_finite_and_writes_nothing(db, bad):
    sid = _sale(db)
    inv = create_invoice(db, sid, "INV-MIP")["invoice_id"]
    before = db.execute("SELECT status FROM invoices WHERE id = %s", (inv,)).fetchone()[0]
    assert before != "paid"

    with pytest.raises(ValueError):
        mark_invoice_paid(db, inv, bad, "2026-10-30")

    assert _payment_count(db) == 0
    assert get_sale_total_paid(db, sid) == 0
    assert db.execute("SELECT status FROM invoices WHERE id = %s",
                      (inv,)).fetchone()[0] == before
    assert _audit_count(db, "INVOICE_PAYMENT") == 0


def test_mark_invoice_paid_still_rejects_zero_and_negative(db):
    sid = _sale(db)
    inv = create_invoice(db, sid, "INV-ZERO")["invoice_id"]
    for bad in (0, 0.0, -1):
        with pytest.raises(ValueError):
            mark_invoice_paid(db, inv, bad, "2026-10-30")
    assert _payment_count(db, sid) == 0


def test_mark_invoice_paid_still_accepts_partial_and_overpayment(db):
    """Overpayment is accepted by design ("money received is truth"). A
    finiteness guard must not quietly become an amount cap."""
    sid = _sale(db)
    inv = create_invoice(db, sid, "INV-OK")["invoice_id"]
    assert mark_invoice_paid(db, inv, 40, "2026-10-30")["status"] != "paid"
    assert mark_invoice_paid(db, inv, 1e6, "2026-10-31")["status"] == "paid"
    assert _payment_count(db, sid) == 2


# =============== 3. add_sale_item / update_sale_item — no guard at all =======

@NON_FINITE
def test_add_sale_item_rejects_non_finite_and_writes_nothing(db, bad):
    sid = _sale(db)
    before = db.execute(
        "SELECT count(*) FROM sale_items WHERE sale_id = %s", (sid,)).fetchone()[0]

    with pytest.raises(ValueError):
        add_sale_item(db, sid, "PoisonLine", bad, 10.0, "KG")
    with pytest.raises(ValueError):
        add_sale_item(db, sid, "PoisonLine", 5, bad, "KG")

    assert db.execute("SELECT count(*) FROM sale_items WHERE sale_id = %s",
                      (sid,)).fetchone()[0] == before
    assert _audit_count(db, "SALE_ITEM_ADD") == 0


@NON_FINITE
def test_update_sale_item_rejects_non_finite_and_changes_nothing(db, bad):
    sid = _sale(db)
    item_id = db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id", (sid,)).fetchone()[0]
    stored = db.execute("SELECT quantity, unit_price FROM sale_items WHERE id = %s",
                        (item_id,)).fetchone()
    assert stored == (10.0, 10.0)

    with pytest.raises(ValueError):
        update_sale_item(db, item_id, quantity=bad)
    with pytest.raises(ValueError):
        update_sale_item(db, item_id, unit_price=bad)

    assert db.execute("SELECT quantity, unit_price FROM sale_items WHERE id = %s",
                      (item_id,)).fetchone() == stored
    assert _audit_count(db, "SALE_ITEM_UPDATE") == 0


def test_sale_items_reject_zero_and_negative(db):
    """INVERTED — a direct reversal of a ratified decision, not a bug fix.

    This test used to read ``test_sale_items_still_accept_zero_and_negative``
    and asserted that ``0`` and negatives were ACCEPTED. That was Phase 2-B's
    first position: finiteness only, because a zero-priced quotation line is
    legitimate. The trade-off was put to the user explicitly — a line whose
    price is not yet known can no longer be recorded — and the strict rule was
    chosen over it. Do not "fix" this test by re-allowing zero.

    The rule now lives in ``sales._positive_values`` and is pinned in full,
    across all four writers of these columns, in
    ``tests/test_sale_item_sign_rule.py``. What is asserted here is the
    narrower claim that belongs to this file: the finiteness guard did not
    become a no-op when the sign check was layered on top of it.
    """
    sid = _sale(db)
    before = db.execute("SELECT count(*) FROM sale_items WHERE sale_id = %s",
                        (sid,)).fetchone()[0]
    for bad in (0, 0.0, -3, -1.5):
        with pytest.raises(ValueError):
            add_sale_item(db, sid, "BadLine", bad, 10.0, "KG")
        with pytest.raises(ValueError):
            add_sale_item(db, sid, "BadLine", 5, bad, "KG")
    assert db.execute("SELECT count(*) FROM sale_items WHERE sale_id = %s",
                      (sid,)).fetchone()[0] == before
    assert _audit_count(db, "SALE_ITEM_ADD") == 0


def test_update_sale_item_still_allows_descriptive_only_edits(db):
    sid = _sale(db)
    item_id = db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id", (sid,)).fetchone()[0]
    assert update_sale_item(db, item_id, product_name="Renamed", unit="L") is True
    assert update_sale_item(db, item_id) is False  # nothing to do


def test_update_sale_item_still_refuses_to_rebase_an_invoiced_line(db):
    """Ordering matters: the 409 ``InvoicedLineConflict`` must keep winning over
    a 400, or a non-finite value on an invoiced line would silently change the
    error shape the frontend already handles."""
    sid = _sale(db)
    create_invoice(db, sid, "INV-LINE")
    item_id = db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id", (sid,)).fetchone()[0]
    with pytest.raises(InvoicedLineConflict):
        update_sale_item(db, item_id, quantity=float("inf"))


# ================= 4. the upload path — a different ingress =================

@NON_FINITE
def test_compare_stock_upload_rejects_non_finite_cells_and_writes_nothing(db, bad):
    """A spreadsheet cell is not a JSON body, so neither Phase 2-A wall applies.

    ``compare_stock_upload`` reads ``float(item.get("balance_last_month", 0) or
    0)`` straight off a parsed row. ``inf or 0`` is ``inf`` — the ``or 0``
    fallback only catches ``None``/``0``/empty — so the value survives into
    ``upload_rows`` and then into ``chemicals.current_qty``.
    """
    add_chemical(db, "Acid", 100, "KG")
    with pytest.raises(ValueError):
        compare_stock_upload(db, [
            {"name": "Acid", "balance_last_month": bad, "balance_this_month": 100}])
    with pytest.raises(ValueError):
        compare_stock_upload(db, [
            {"name": "Acid", "balance_last_month": 100, "balance_this_month": bad}])

    assert db.execute("SELECT current_qty FROM chemicals "
                      "WHERE name = 'Acid'").fetchone()[0] == 100
    assert db.execute("SELECT count(*) FROM upload_rows").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM uploads").fetchone()[0] == 0


@NON_FINITE
def test_adjust_stock_refuses_a_non_finite_upload_row(db, bad):
    """The value is RE-READ from ``upload_rows`` — so lines 96-97 alone are not
    the whole answer.

    ``adjust_stock_from_upload`` selects ``ur.balance_this_month`` out of the
    table and writes it straight into ``chemicals.current_qty``. The guard that
    keeps it out of the table does not automatically keep it out of stock, so
    the write path is guarded in its own right (Phase 0 found 0 poisoned rows
    today, but the column is durable, nullable REAL — a legacy row or any
    future writer would land here).
    """
    add_chemical(db, "Acid", 100, "KG")
    # A row that reached the table by some route other than the guarded compare.
    uid = db.execute(
        "INSERT INTO uploads (filename, status, total_chemicals, matched) "
        "VALUES ('poison.xlsx', 'approved', 1, 0) RETURNING id").fetchone()[0]
    db.execute(
        "INSERT INTO upload_rows (upload_id, chemical_name, balance_last_month, "
        "balance_this_month, matched_in_db) VALUES (%s, 'Acid', 100, %s, 0)",
        (uid, bad))
    db.commit()

    assert adjust_stock_from_upload(db, uid, reviewed_by="admin") is False

    assert db.execute("SELECT current_qty FROM chemicals "
                      "WHERE name = 'Acid'").fetchone()[0] == 100
    assert _audit_count(db, "ADJUST_STOCK") == 0


def test_upload_path_still_accepts_ordinary_cells(db):
    """The whole reconciliation flow, including the zero and float-valued cells
    a real stock sheet carries."""
    add_chemical(db, "Acid", 100, "KG")
    results = compare_stock_upload(db, [
        {"name": "Acid", "balance_last_month": 0, "balance_this_month": 120},
        {"name": "New", "balance_last_month": 0, "balance_this_month": 7.5}])
    assert results["stats"]["this_month_mismatches"] == 1
    assert results["stats"]["not_in_db"] == 1
    uid = save_upload(db, "ok.xlsx", results)
    assert adjust_stock_from_upload(db, uid, reviewed_by="admin") is True
    assert db.execute("SELECT current_qty FROM chemicals "
                      "WHERE name = 'Acid'").fetchone()[0] == 120
    assert db.execute("SELECT count(*) FROM upload_rows "
                      "WHERE upload_id = %s", (uid,)).fetchone()[0] == 2


# ============== 5. sweep — every other numeric write in the service layer ====

@NON_FINITE
def test_update_sale_payment_record_rejects_non_finite_and_changes_nothing(db, bad):
    """``PaymentPatchIn`` is Phase-2-A guarded, but this is a third writer into
    the same ``sale_payments.payment_amount`` column as
    ``record_sale_payment``."""
    sid = _sale_at_payment_due(db)
    pid = record_sale_payment(db, sid, "2026-10-30", 40.0)["payment_id"]

    with pytest.raises(ValueError):
        update_sale_payment_record(db, sid, pid, payment_amount=bad)

    assert db.execute("SELECT payment_amount FROM sale_payments "
                      "WHERE id = %s", (pid,)).fetchone()[0] == 40.0
    assert get_sale_total_paid(db, sid) == 40.0
    assert _sale_row(db, sid)[0] == "payment_due"


@NON_FINITE
def test_update_sale_full_rejects_non_finite_items_and_writes_nothing(db, bad):
    """``update_sale_full`` guards only ``qty < 0``, which ``inf``/``nan`` pass,
    and it writes ``sale_items.quantity`` / ``unit_price`` — the very columns
    ``add_sale_item`` / ``update_sale_item`` now guard."""
    sid = _sale(db, pi="PI-FULL")
    before_items = db.execute(
        "SELECT id, quantity, unit_price FROM sale_items WHERE sale_id = %s ORDER BY id",
        (sid,)).fetchall()

    with pytest.raises(ValueError):
        update_sale_full(db, sid, {"pi_number": "PI-FULL-RENAMED"},
                         [{"product_name": "W", "quantity": bad, "unit_price": 10}], [])
    with pytest.raises(ValueError):
        update_sale_full(db, sid, {"pi_number": "PI-FULL-RENAMED"},
                         [{"product_name": "W", "quantity": 10, "unit_price": bad}], [])
    # A later item in the same batch: the guard must run before ANY write, so a
    # clean first item does not land while a poisoned one is rejected.
    with pytest.raises(ValueError):
        update_sale_full(db, sid, {"pi_number": "PI-FULL-RENAMED"},
                         [{"product_name": "W", "quantity": 99, "unit_price": 99},
                          {"product_name": "X", "quantity": bad, "unit_price": 1}], [])

    assert db.execute("SELECT pi_number FROM sales "
                      "WHERE id = %s", (sid,)).fetchone()[0] == "PI-FULL"
    assert db.execute(
        "SELECT id, quantity, unit_price FROM sale_items WHERE sale_id = %s ORDER BY id",
        (sid,)).fetchall() == before_items
    assert _audit_count(db, "SALE_UPDATE") == 0


def test_update_sale_full_rejects_zero_and_still_accepts_finite(db):
    """INVERTED — see ``test_sale_items_reject_zero_and_negative`` above. This
    test used to be ``test_update_sale_full_still_accepts_zero_and_finite`` and
    asserted a zero-quantity / zero-price line was written, on the reasoning
    that "zero quantity and zero price are legal PI lines — they are how a
    quotation carries a not-yet-priced line". That reasoning was overruled by
    the user. ``update_sale_full`` previously refused negatives only
    (``qty < 0``), so zero was the half of the old behaviour that survived;
    it now refuses both.

    What is left of the original intent is the part that still holds: a
    zero-priced quotation line cannot be recorded, but a genuinely small or
    large finite price can, and the derived invoice total must still be exact.
    """
    sid = _sale(db, pi="PI-FULL-OK")
    item_id = db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id", (sid,)).fetchone()[0]
    with pytest.raises(ValueError):
        update_sale_full(db, sid, {"pi_number": "PI-FULL-OK2"},
                         [{"id": item_id, "product_name": "W",
                           "quantity": 0, "unit_price": 0},
                          {"product_name": "X", "quantity": 3, "unit_price": 2.5}], [])
    assert get_sale_invoice_total(db, sid) == 100.0, "the rejected batch still wrote"

    out = update_sale_full(db, sid, {"pi_number": "PI-FULL-OK2"},
                           [{"id": item_id, "product_name": "W",
                             "quantity": 0.001, "unit_price": 0.01},
                            {"product_name": "X", "quantity": 3, "unit_price": 2.5}], [])
    assert out is not None
    # 0.001 x 0.01 = 0.00001, which ::numeric(2) rounds away, so the derived
    # total is 7.50 exactly — the rounding is the point, not an accident.
    assert get_sale_invoice_total(db, sid) == pytest.approx(7.5)


@NON_FINITE
def test_add_sale_rejects_non_finite_items_and_writes_no_sale(db, bad):
    """``add_sale`` is a fourth writer into ``sale_items``. Its INSERT into
    ``sales`` happens first, so the guard must run before every statement or a
    rejection leaves an uncommitted header behind."""
    with pytest.raises(ValueError):
        add_sale(db, {"pi_number": "PI-BAD"},
                 [{"product_name": "W", "quantity": bad, "unit_price": 10}])
    with pytest.raises(ValueError):
        add_sale(db, {"pi_number": "PI-BAD"},
                 [{"product_name": "W", "quantity": 1, "unit_price": bad}])

    assert db.execute("SELECT count(*) FROM sales").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM sale_items").fetchone()[0] == 0
    assert _audit_count(db, "SALE_CREATE") == 0


@NON_FINITE
def test_add_chemical_rejects_non_finite(db, bad):
    with pytest.raises(ValueError):
        add_chemical(db, "Poison", bad, "KG")
    assert db.execute("SELECT count(*) FROM chemicals").fetchone()[0] == 0


@NON_FINITE
def test_update_stock_rejects_non_finite_delta(db, bad):
    """``new_qty = current_qty + delta`` and ``inf < 0`` is False, so the
    shortage guard never trips. ``cli.py`` reaches this with
    ``float(sys.argv[3])``."""
    add_chemical(db, "Acid", 10, "KG")
    with pytest.raises(ValueError):
        update_stock(db, "Acid", bad)
    assert db.execute("SELECT current_qty FROM chemicals "
                      "WHERE name = 'Acid'").fetchone()[0] == 10


@NON_FINITE
def test_set_reorder_level_rejects_non_finite(db, bad):
    """The HTTP route has been guarded by ``_req_float`` since Phase 2-A
    (MEDIUM-1); the service itself still took ``inf`` straight into a REAL
    column from any direct caller."""
    add_chemical(db, "Acid", 10, "KG")
    assert set_reorder_level(db, "Acid", 5) is True
    with pytest.raises(ValueError):
        set_reorder_level(db, "Acid", bad)
    assert db.execute("SELECT reorder_level FROM chemicals "
                      "WHERE name = 'Acid'").fetchone()[0] == 5
    assert _audit_count(db, "SET_REORDER_LEVEL") == 1


@NON_FINITE
def test_add_unit_conversion_rejects_non_finite_factor(db, bad):
    """The factor governs every quantity conversion in the product; ``inf``
    would make ``convert_quantity`` return ``inf`` for every stock movement.

    ``KG -> G`` is a seeded row, so the assertion is that the factor is
    UNCHANGED rather than that no row exists.
    """
    seeded = db.execute(
        "SELECT factor FROM unit_conversions WHERE from_unit = 'KG' AND to_unit = 'G'"
    ).fetchone()
    assert seeded is not None
    with pytest.raises(ValueError):
        add_unit_conversion(db, "kg", "g", bad)
    assert db.execute(
        "SELECT factor FROM unit_conversions WHERE from_unit = 'KG' AND to_unit = 'G'"
    ).fetchone()[0] == seeded[0]
    assert _audit_count(db, "UNIT_CONVERSION_UPSERT") == 0


@NON_FINITE
def test_import_from_json_rejects_non_finite_file_values(db, tmp_path, monkeypatch, bad):
    """``import-json`` is the one genuine file/parse numeric ingress: it runs
    ``json.load`` on disk and ``float()`` on whatever is in the file, then
    writes ``chemicals.current_qty`` / ``balance_last_month``. Nothing in
    Phase 2-A's HTTP walls can see it.

    ``get_connection`` is pointed at the fixture so the rejected connection —
    which the function leaks, since it has no try/finally — cannot leave an
    idle-in-transaction session holding a lock against the next test's TRUNCATE.
    """
    import raas_tracker.stock as stock_mod

    add_chemical(db, "Acid", 10, "KG")
    payload = tmp_path / "stock.json"
    payload.write_text(json.dumps([
        {"product_name": "Acid", "balance_this_month": bad, "balance_last_month": 10}]),
        encoding="utf-8")
    monkeypatch.setattr(stock_mod, "get_connection", lambda *a, **k: db)

    with pytest.raises(ValueError):
        import_from_json(str(payload))

    assert db.execute("SELECT current_qty, balance_last_month FROM chemicals "
                      "WHERE name = 'Acid'").fetchone() == (10, 0)


@NON_FINITE
def test_recipe_writers_reject_non_finite(db, bad):
    """``water_percentage < 0 or > 100`` is False for ``nan``, and
    ``percentage < 0`` is False for ``nan`` and ``inf`` — so a poisoned formula
    snapshot lands in ``recipes.total_quantity`` / ``recipe_items.percentage``
    and every later production run multiplies by it. ``cli.py`` reaches all of
    these with ``float(sys.argv[...])``."""
    cid = _company_with_product(db)
    add_chemical(db, "Water", 500, "KG")

    with pytest.raises(ValueError):
        add_recipe(db, "PoisonRecipe", bad, 0, company_id=cid, product_name="Soap")
    with pytest.raises(ValueError):
        add_recipe(db, "PoisonRecipe", 1, bad, company_id=cid, product_name="Soap")
    assert db.execute("SELECT count(*) FROM recipes").fetchone()[0] == 0

    assert add_recipe(db, "GoodRecipe", 1, 0, company_id=cid, product_name="Soap") is True
    with pytest.raises(ValueError):
        add_recipe_item(db, cid, "GoodRecipe", "Water", bad)
    assert db.execute("SELECT count(*) FROM recipe_items").fetchone()[0] == 0

    # ...and the same for the update path, on a line that really exists.
    assert add_recipe_item(db, cid, "GoodRecipe", "Water", 40.0) is True
    with pytest.raises(ValueError):
        update_recipe_item(db, cid, "GoodRecipe", "Water", bad)
    assert db.execute("SELECT percentage, required_qty_per_unit FROM recipe_items").fetchone() == (40.0, 0.4)

    with pytest.raises(ValueError):
        update_recipe(db, cid, "GoodRecipe", total_quantity=bad)
    with pytest.raises(ValueError):
        update_recipe(db, cid, "GoodRecipe", water_percentage=bad)

    assert db.execute("SELECT total_quantity, water_percentage FROM recipes "
                      "WHERE name = 'GoodRecipe'").fetchone() == (1, 0)
    assert _audit_count(db, "RECIPE_UPDATE") == 0
    assert _audit_count(db, "RECIPE_ITEM_UPDATE") == 0


# ================================ happy paths ===============================

def test_stock_writers_still_accept_zero_and_finite(db):
    assert add_chemical(db, "Acid", 0, "KG") is True
    assert set_reorder_level(db, "Acid", 0) is True
    assert db.execute("SELECT reorder_level FROM chemicals "
                      "WHERE name = 'Acid'").fetchone()[0] == 0
    assert set_reorder_level(db, "Acid", 2.5) is True
    assert update_stock(db, "Acid", 5) is True
    assert db.execute("SELECT current_qty, reorder_level FROM chemicals "
                      "WHERE name = 'Acid'").fetchone() == (5, 2.5)
    assert update_stock(db, "Acid", -5) is True
    with pytest.raises(ValueError):
        update_stock(db, "Acid", -1)  # shortage rule, unchanged


def test_import_from_json_still_accepts_ordinary_values(db, tmp_path):
    payload = tmp_path / "stock.json"
    payload.write_text(json.dumps([
        {"product_name": "Acid", "balance_this_month": 42, "balance_last_month": 10,
         "this_month_unit": "KG"},
        {"product_name": "New", "balance_this_month": 0, "balance_last_month": 0}]),
        encoding="utf-8")
    import_from_json(str(payload))
    rows = dict(db.execute(
        "SELECT name, current_qty FROM chemicals ORDER BY name").fetchall())
    assert rows == {"Acid": 42, "New": 0}


def test_add_unit_conversion_still_accepts_finite_factors(db):
    assert add_unit_conversion(db, "kg", "g", 1000.0) is True
    assert db.execute("SELECT factor FROM unit_conversions "
                      "WHERE from_unit = 'KG'").fetchone()[0] == 1000.0


def test_recipe_writers_still_accept_zero_and_finite(db):
    cid = _company_with_product(db, name="Happy Co")
    add_chemical(db, "Water", 500, "KG")

    assert add_recipe(db, "R", 0, 0, company_id=cid, product_name="Soap") is True
    assert add_recipe_item(db, cid, "R", "Water", 0.0) is True
    assert update_recipe_item(db, cid, "R", "Water", 50.0) is True
    assert update_recipe(db, cid, "R", total_quantity=0, water_percentage=0) is True
    assert db.execute("SELECT percentage, required_qty_per_unit "
                      "FROM recipe_items").fetchone() == (50.0, 0.5)
    assert db.execute("SELECT total_quantity FROM recipes "
                      "WHERE name = 'R'").fetchone()[0] == 0


def test_no_non_finite_value_survives_any_guarded_write(db):
    """End-to-end: after driving every guarded writer with ``inf``, not one REAL
    column in the tables these services touch may hold a non-finite value."""
    sid = _sale_at_payment_due(db)
    create_invoice(db, sid, "INV-SWEEP")
    add_chemical(db, "Acid", 10, "KG")
    inf = float("inf")
    item_id = db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id LIMIT 1",
        (sid,)).fetchone()[0]

    for call in (
        lambda: record_sale_payment(db, sid, "2026-10-30", inf),
        lambda: add_sale_item(db, sid, "X", inf, inf, "KG"),
        lambda: update_sale_item(db, item_id, quantity=inf),
        lambda: add_sale(db, {"pi_number": "PI-S"},
                         [{"product_name": "X", "quantity": inf}]),
        lambda: update_stock(db, "Acid", inf),
        lambda: set_reorder_level(db, "Acid", inf),
        lambda: add_unit_conversion(db, "kg", "g", inf),
        lambda: compare_stock_upload(db, [{"name": "Acid", "balance_last_month": inf,
                                           "balance_this_month": 1}]),
    ):
        with pytest.raises(ValueError):
            call()

    for sql in (
        "SELECT count(*) FROM sales WHERE payment_amount::text IN "
        "('Infinity', '-Infinity', 'NaN')",
        "SELECT count(*) FROM sale_items WHERE quantity::text IN "
        "('Infinity', '-Infinity', 'NaN') OR unit_price::text IN "
        "('Infinity', '-Infinity', 'NaN')",
        "SELECT count(*) FROM sale_payments WHERE payment_amount::text IN "
        "('Infinity', '-Infinity', 'NaN')",
        "SELECT count(*) FROM chemicals WHERE current_qty::text IN "
        "('Infinity', '-Infinity', 'NaN') OR balance_last_month::text IN "
        "('Infinity', '-Infinity', 'NaN') OR reorder_level::text IN "
        "('Infinity', '-Infinity', 'NaN')",
        "SELECT count(*) FROM unit_conversions WHERE factor::text IN "
        "('Infinity', '-Infinity', 'NaN')",
        "SELECT count(*) FROM invoices WHERE amount::text IN "
        "('Infinity', '-Infinity', 'NaN')",
    ):
        assert db.execute(sql).fetchone()[0] == 0, sql