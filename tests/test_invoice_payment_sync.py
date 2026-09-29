"""Payment writes must re-derive the linked invoice's paid status.

Every path that INSERT/UPDATE/DELETEs a sale_payments row re-derives the
affected invoice's status: insert (mark_invoice_paid / record_sale_payment),
edit (update_sale_payment_record) and delete (delete_sale_payment_record).
Deleting an invoice's payment used to leave invoices.status = 'paid' with
paid_amount 0 — a permanent silent financial misstatement.
"""
from raas_tracker.sales import (
    add_sale,
    create_invoice,
    delete_sale_payment_record,
    mark_invoice_paid,
    record_sale_payment,
    update_sale_payment_record,
)


# ---------- helpers ----------

def _sale(db, pi_number="PI-SYNC-1", total=100.0):
    return add_sale(db, {"pi_number": pi_number, "client_name": "Sync Co"},
                    [{"product_name": "W", "quantity": 1, "unit_price": total}])


def _status(db, invoice_id):
    return db.execute(
        "SELECT status FROM invoices WHERE id = %s", (invoice_id,)).fetchone()[0]


def _paid_amount(db, invoice_id):
    return round(float(db.execute(
        "SELECT COALESCE(ROUND(SUM(payment_amount)::numeric, 2)::float8, 0) "
        "FROM sale_payments WHERE invoice_id = %s", (invoice_id,)).fetchone()[0]), 2)


def _sale_paid(db, sale_id):
    return round(float(db.execute(
        "SELECT COALESCE(ROUND(SUM(payment_amount)::numeric, 2)::float8, 0) "
        "FROM sale_payments WHERE sale_id = %s", (sale_id,)).fetchone()[0]), 2)


def _sale_legacy_paid(db, sale_id):
    return round(float(db.execute(
        "SELECT payment_amount FROM sales WHERE id = %s", (sale_id,)).fetchone()[0] or 0), 2)


def _pay_row(db, sale_id, invoice_id, amount):
    return db.execute(
        "SELECT id FROM sale_payments WHERE sale_id = %s AND invoice_id = %s "
        "ORDER BY id DESC LIMIT 1", (sale_id, invoice_id)).fetchone()[0]


# ---------- delete path ----------

def test_deleting_full_payment_unpays_invoice(db):
    sid = _sale(db, "PI-SYNC-DEL")
    inv = create_invoice(db, sid, "INV-SYNC-DEL", amount=100)["invoice_id"]

    out = mark_invoice_paid(db, inv, 100, "2026-10-30")
    assert out["status"] == "paid"
    assert _status(db, inv) == "paid"
    assert _sale_legacy_paid(db, sid) == 100

    assert delete_sale_payment_record(db, out["payment_id"]) is True

    assert _status(db, inv) != "paid", "deleted payment must not leave status='paid'"
    assert _paid_amount(db, inv) == 0
    assert _sale_paid(db, sid) == 0
    assert _sale_legacy_paid(db, sid) == 0


def test_deleting_partial_payment_keeps_paid_invoice(db):
    """Two payments, one covering the invoice: deleting the other keeps 'paid'."""
    sid = _sale(db, "PI-SYNC-DEL2", total=200)
    inv = create_invoice(db, sid, "INV-SYNC-DEL2", amount=100)["invoice_id"]

    mark_invoice_paid(db, inv, 100, "2026-10-30")   # covers the invoice
    extra = record_sale_payment(db, sid, "2026-10-31", 25)["payment_id"]
    assert _status(db, inv) == "paid"

    assert delete_sale_payment_record(db, extra) is True
    assert _status(db, inv) == "paid", "still covered by the remaining payment"
    assert _paid_amount(db, inv) == 100


# ---------- update path ----------

def test_editing_payment_down_unpays_invoice(db):
    sid = _sale(db, "PI-SYNC-EDIT")
    inv = create_invoice(db, sid, "INV-SYNC-EDIT", amount=100)["invoice_id"]

    out = mark_invoice_paid(db, inv, 100, "2026-10-30")
    assert _status(db, inv) == "paid"

    assert update_sale_payment_record(db, out["payment_id"], payment_amount=40) is True

    assert _status(db, inv) != "paid", "shrinking the payment must flip status back"
    assert _paid_amount(db, inv) == 40
    assert _sale_legacy_paid(db, sid) == 40


def test_editing_payment_up_pays_invoice(db):
    sid = _sale(db, "PI-SYNC-EDIT2")
    inv = create_invoice(db, sid, "INV-SYNC-EDIT2", amount=100)["invoice_id"]

    out = mark_invoice_paid(db, inv, 40, "2026-10-30")
    assert out["status"] == "planned"
    assert _status(db, inv) == "planned"

    assert update_sale_payment_record(db, out["payment_id"], payment_amount=100) is True

    assert _status(db, inv) == "paid"
    assert _paid_amount(db, inv) == 100


# ---------- guards ----------

def test_sale_level_payment_leaves_invoices_alone(db):
    """A payment with no invoice_id must not touch any invoice status."""
    sid = _sale(db, "PI-SYNC-NONE", total=300)
    inv = create_invoice(db, sid, "INV-SYNC-NONE", amount=100)["invoice_id"]
    mark_invoice_paid(db, inv, 100, "2026-10-30")
    assert _status(db, inv) == "paid"

    sale_only = record_sale_payment(db, sid, "2026-10-31", 50)["payment_id"]
    assert _status(db, inv) == "paid"
    assert _paid_amount(db, inv) == 100

    assert update_sale_payment_record(db, sale_only, payment_amount=25) is True
    assert _status(db, inv) == "paid"

    assert delete_sale_payment_record(db, sale_only) is True
    assert _status(db, inv) == "paid"
    assert _paid_amount(db, inv) == 100
    assert _sale_paid(db, sid) == 100


def test_legacy_null_amount_invoice_unpays_when_payment_removed(db):
    """Legacy NULL-amount invoices: any payment pays, no payment un-pays."""
    sid = _sale(db, "PI-SYNC-LEG", total=200)
    inv = create_invoice(db, sid, "INV-SYNC-LEG", amount=100)["invoice_id"]
    db.execute("UPDATE invoices SET amount = NULL WHERE id = %s", (inv,))
    db.commit()

    out = mark_invoice_paid(db, inv, 5, "2026-10-30")
    assert out["status"] == "paid"
    assert _status(db, inv) == "paid"

    assert delete_sale_payment_record(db, out["payment_id"]) is True
    assert _status(db, inv) != "paid"
    assert _paid_amount(db, inv) == 0


def test_shipped_invoice_returns_to_shipped_when_unpaid(db):
    """Demotion targets the furthest provable state, not a hardcoded 'planned'."""
    sid = _sale(db, "PI-SYNC-SHIP", total=200)
    inv = create_invoice(db, sid, "INV-SYNC-SHIP", amount=100)["invoice_id"]
    db.execute(
        "UPDATE invoices SET status = 'shipped', actual_ship_date = %s WHERE id = %s",
        ("2026-10-20", inv))
    db.commit()

    out = mark_invoice_paid(db, inv, 100, "2026-10-30")
    assert _status(db, inv) == "paid"

    assert delete_sale_payment_record(db, out["payment_id"]) is True
    assert _status(db, inv) == "shipped"
