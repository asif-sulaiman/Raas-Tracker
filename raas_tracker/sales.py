"""Sales pipeline, items, payments, and summaries."""

import math
import psycopg
import json
import os
import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, List, Dict, Any, Union

from .audit import log_audit_action
from .notifications import notify_sale_stage, clear_dedupe, clear_maturity_dedupe
from ._numeric import finite_values as _finite
from ._exceptions import (
    InvoiceAlreadyAdvanced,
    InvoiceNotFound,
    InvoiceNotProduced,
    LifecycleConflict,
    SaleItemNotFound,
    ServiceError,
    ToleranceExceeded,
)


# Phase 3-C: 5% over-invoice tolerance (global constant, multiplier)
OVER_INVOICE_TOLERANCE = Decimal("1.05")


def _now_str() -> str:
    """Current UTC time as 'YYYY-MM-DD HH:MM:SS' for TEXT datetime columns.

    This is the *Python* spelling and it is correct: strftime uses %H for a
    24-hour hour and %M for minutes. It must produce byte-identical output to
    the SQL floor `to_char(..., 'YYYY-MM-DD HH24:MI:SS')`, which is what
    compares against these values (P1-18). Never use the SQL pattern codes
    here, and never use these Python codes inside a to_char() call.
    """
    from datetime import datetime as _dt, timezone
    return _dt.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

SALE_STAGE_ORDER = ["pi_issued", "lc_received", "shipment_ongoing", "payment_due", "completed"]


class InvoicedLineConflict(LifecycleConflict):
    """A PI line that invoice_items already point at cannot be re-based.

    Removing it, or changing its quantity/price, would desynchronise the
    commercial report: its grain is the PI line while its money comes from
    invoice_items, and the next invoice is sized from the PI total.

    A `LifecycleConflict`, so ``_exceptions.status_for`` reports the 409 it
    always meant. It used to be a bare ``ValueError`` that three routes each
    caught to hardcode a 409 — the docstring claimed "never 400" and nothing
    but those three copies enforced it.
    """


def _positive_values(pairs: List[tuple]) -> None:
    """Raise ValueError unless every (field, value) pair is strictly positive.

    Phase 2-B, the **sign** rule on the sale-line money columns. Deliberate
    reversal of the first pass, which kept ``0`` and negatives legal here on the
    reasoning that a zero-priced quotation line ("price TBD") is legitimate.
    That reasoning was put to the user with its cost stated explicitly, and the
    strict rule was chosen over it. See ``tests/test_sale_item_sign_rule.py``.

    Applied to ``quantity`` AND ``unit_price`` on all four writers of
    ``sale_items.quantity`` / ``sale_items.unit_price`` — ``add_sale_item``,
    ``update_sale_item``, ``add_sale`` and ``update_sale_full``. They write the
    same two columns, so a rule on any subset of them is a rule that can be
    bypassed by using one of the others; ``add_sale`` is the only sale-create
    path over HTTP.

    A helper rather than four inline ``if``s so the four writers cannot drift
    apart, and so the field name in the message points at the column. ``_finite``
    runs FIRST at every call site: ``nan <= 0`` is False, so a sign check on its
    own would wave NaN through.
    """
    for field, value in pairs:
        if value is None:
            # "not supplied", not "not a number". `update_sale_item` is a
            # PATCH: the route passes every keyword through, so a body carrying
            # only `unit_price` arrives here with `quantity=None`. Validating it
            # would reject a perfectly good single-field edit.
            continue
        try:
            ok = float(value) > 0
        except (TypeError, ValueError):
            raise ValueError(f"{field} must be a number")
        if not ok:
            raise ValueError(f"{field} must be greater than 0")


def _next_stage(current: str) -> Optional[str]:
    """Return the next stage in the pipeline, or None if at end."""
    try:
        idx = SALE_STAGE_ORDER.index(current)
        return SALE_STAGE_ORDER[idx + 1] if idx + 1 < len(SALE_STAGE_ORDER) else None
    except ValueError:
        return None


def add_sale(conn: psycopg.Connection, sale_data: Dict[str, Any],
             items: List[Dict[str, Any]], initial_stage: str = "pi_issued") -> int:
    """Insert a new sale with line items. Returns the new sale ID.

    Phase 2-B: every item's quantity/unit_price must be finite and > 0.
    Validated before the INSERT INTO sales, not inside the item loop, so a
    rejected line cannot leave an uncommitted header behind on a failed call.
    """
    for item in items or []:
        qty, price = item.get("quantity", 0), item.get("unit_price", 0)
        _finite(qty, price)
        _positive_values([("quantity", qty), ("unit_price", price)])
    cursor = conn.execute(
        """INSERT INTO sales (stage, pi_number, pi_date, client_name, pi_file_path, company_id, maturity_date, comments)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (initial_stage, sale_data.get("pi_number"), sale_data.get("pi_date"),
         sale_data.get("client_name"), sale_data.get("pi_file_path"),
         sale_data.get("company_id"), sale_data.get("maturity_date"), sale_data.get("comments"))
    )
    sale_id = cursor.fetchone()[0]
    for item in items:
        unit = (item.get("unit") or "KG").strip().upper() or "KG"
        conn.execute(
            """INSERT INTO sale_items (sale_id, product_name, quantity, unit_price, unit, item_no)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (sale_id, item["product_name"], item.get("quantity", 0), item.get("unit_price", 0),
             unit, item.get("item_no"))
        )
    conn.execute(
        """INSERT INTO sales_stage_history (sale_id, from_stage, to_stage, notes)
           VALUES (%s, NULL, %s, 'Created')""",
        (sale_id, initial_stage)
    )
    log_audit_action(conn, "SALE_CREATE", "sale", sale_id,
                     new_value=sale_data.get("pi_number"))
    conn.commit()
    return sale_id


def get_all_sales(conn: psycopg.Connection, stage: Optional[str] = None,
                  search: Optional[str] = None, page: int = 1,
                  page_size: int = 50) -> Dict[str, Any]:
    """Return paginated sales (optionally filtered by stage and/or search text).

    Search matches PI number, client name, LC number, and product names.
    Returns dict with 'sales' list and 'total' count.
    """
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 200:
        page_size = 50
    offset = (page - 1) * page_size

    base_query = """
        WITH sale_payments_agg AS (
            SELECT sale_id,
                   COALESCE(ROUND(SUM(payment_amount)::numeric, 2)::float8, 0) AS total_paid
            FROM sale_payments
            GROUP BY sale_id
        )
        SELECT s.id, s.stage, s.pi_number, s.pi_date, s.client_name, s.pi_file_path,
               s.lc_number, s.lc_date, s.shipment_date, s.payment_date, s.payment_amount,
               s.created_at, s.updated_at, s.company_id, s.maturity_date, s.comments,
               COALESCE(c.name, s.client_name),
                COALESCE(ROUND(SUM(si.quantity * si.unit_price)::numeric, 2)::float8, 0) AS total_value,
                COUNT(si.id) AS item_count,
               COALESCE(spa.total_paid, 0) AS total_paid,
               s.shipment_status, s.lc_id,
               COUNT(*) OVER() AS total_count
        FROM sales s
        LEFT JOIN sale_items si ON si.sale_id = s.id
        LEFT JOIN companies c ON c.id = s.company_id
        LEFT JOIN sale_payments_agg spa ON spa.sale_id = s.id
    """
    params: list = []
    clauses = []
    if stage:
        clauses.append("s.stage = %s")
        params.append(stage)
    if search:
        like = f"%{search}%"
        clauses.append("""(s.pi_number ILIKE %s OR s.client_name ILIKE %s OR s.lc_number ILIKE %s
            OR EXISTS (SELECT 1 FROM sale_items si2 WHERE si2.sale_id = s.id
                       AND si2.product_name ILIKE %s))""")
        params.extend([like, like, like, like])
    if clauses:
        base_query += " WHERE " + " AND ".join(clauses)
    base_query += " GROUP BY s.id, c.id, spa.total_paid, s.lc_id ORDER BY s.created_at DESC, s.id DESC"
    base_query += " LIMIT %s OFFSET %s"
    params.extend([page_size, offset])

    rows = conn.execute(base_query, params).fetchall()
    total = rows[0][22] if rows else 0  # total_count is the last column
    sales = [
        {"id": r[0], "stage": r[1], "pi_number": r[2], "pi_date": r[3],
         "client_name": r[4], "pi_file_path": r[5], "lc_number": r[6],
         "lc_date": r[7], "shipment_date": r[8], "payment_date": r[9],
         "payment_amount": r[10], "created_at": r[11], "updated_at": r[12],
         "company_id": r[13], "maturity_date": r[14], "comments": r[15],
         "company_name": r[16],
         "total_value": r[17], "item_count": r[18], "total_paid": r[19],
         "shipment_status": r[20], "lc_id": r[21],
         "balance": r[17] - r[19]}
        for r in rows
    ]
    return {"sales": sales, "total": total}


def get_sale_by_id(conn: psycopg.Connection, sale_id: int) -> Optional[Dict[str, Any]]:
    """Return a single sale with its items and stage history."""
    row = conn.execute(
        "SELECT s.id, s.stage, s.pi_number, s.pi_date, s.client_name, "
        "s.pi_file_path, s.lc_number, s.lc_date, s.shipment_date, "
        "s.payment_date, s.payment_amount, s.created_at, s.updated_at, "
        "s.company_id, s.maturity_date, s.comments, c.name, s.shipment_status, "
        "s.lc_id "
        "FROM sales s LEFT JOIN companies c ON c.id = s.company_id "
        "WHERE s.id = %s", (sale_id,)).fetchone()
    if not row:
        return None
    sale = {
        "id": row[0], "stage": row[1], "pi_number": row[2], "pi_date": row[3],
        "client_name": row[4], "pi_file_path": row[5], "lc_number": row[6],
        "lc_date": row[7], "shipment_date": row[8], "payment_date": row[9],
        "payment_amount": row[10], "created_at": row[11], "updated_at": row[12],
        "company_id": row[13], "maturity_date": row[14], "comments": row[15],
        "company_name": row[16] or row[4],
        "shipment_status": row[17], "lc_id": row[18],
    }
    sale["items"] = [
        {"id": r[0], "sale_id": r[1], "product_name": r[2],
         "quantity": r[3], "unit_price": r[4], "unit": r[5] or "KG",
         "item_no": r[6]}
        for r in conn.execute(
            "SELECT id, sale_id, product_name, quantity, unit_price, unit, item_no "
            "FROM sale_items WHERE sale_id = %s ORDER BY id", (sale_id,)
        ).fetchall()
    ]
    sale["history"] = [
        {"id": r[0], "sale_id": r[1], "from_stage": r[2], "to_stage": r[3],
         "changed_at": r[4], "notes": r[5]}
        for r in conn.execute(
            "SELECT * FROM sales_stage_history WHERE sale_id = %s ORDER BY changed_at",
            (sale_id,)
        ).fetchall()
    ]
    sale["payments"] = [
        {"id": r[0], "sale_id": r[1], "payment_date": r[2],
         "payment_amount": r[3], "notes": r[4], "created_at": r[5]}
        for r in conn.execute(
            "SELECT * FROM sale_payments WHERE sale_id = %s ORDER BY payment_date, id",
            (sale_id,)
        ).fetchall()
    ]
    sale["invoices"] = list_invoices(conn, sale_id)
    # Same SQL-rounded helpers the payment paths use, so the displayed balance
    # can never drift from what record_sale_payment / update_sale_payment_record
    # report (Python float multiplication used to be unrounded here).
    sale["invoice_total"] = get_sale_invoice_total(conn, sale_id)
    sale["total_paid"] = get_sale_total_paid(conn, sale_id)
    sale["balance"] = sale["invoice_total"] - sale["total_paid"]
    sale["shipments"] = list_shipments(conn, sale_id)
    return sale


def move_sale_to_stage(conn: psycopg.Connection, sale_id: int, new_stage: str,
                       notes: Optional[str] = None, via_lc: bool = False) -> bool:
    """Move a sale to the next (or specified) stage. Logs the transition.

    ``via_lc`` marks LC-driven propagation (``move_lc_stage``): a directly
    invoked move (``via_lc=False``) on a sale linked via ``sales.lc_id``
    into ``shipment_ongoing`` is refused (returns False) — linked PIs only
    travel via the LC endpoints. ``payment_due -> completed`` (incl.
    ``_complete_if_paid``) stays allowed.

    Phase 3-A barriers:
    - lc_received -> shipment_ongoing: requires at least one non-void invoice
    - shipment_ongoing -> payment_due: requires every invoiced product has
      a company recipe AND an invoice-linked production run
    """
    row = conn.execute("SELECT stage, client_name, lc_id, company_id FROM sales WHERE id = %s", (sale_id,)).fetchone()
    if not row:
        return False
    current, client_name, lc_id, company_id = row[0], row[1] or f"#{sale_id}", row[2], row[3]
    if new_stage not in SALE_STAGE_ORDER:
        return False
    if new_stage == "shipment_ongoing" and not via_lc and lc_id is not None:
        return False

    # Phase 3-A: Barrier 1 - lc_received -> shipment_ongoing requires at least one non-void invoice
    if new_stage == "shipment_ongoing" and current == "lc_received":
        ready = invoice_readiness(conn, [sale_id])
        if not ready["invoices_ok"]:
            return False

    # Phase 3-A: Barrier 2 - shipment_ongoing -> payment_due requires recipe + production for every invoiced product
    if new_stage == "payment_due" and current == "shipment_ongoing":
        if company_id is None:
            return False
        ready = production_readiness(conn, company_id, [sale_id])
        if not ready["produced_ok"]:
            return False

    if new_stage == "shipment_ongoing":
        # Entering production/shipment: mark the sale as running unless it
        # already reached a later shipment_status (never downgrade).
        conn.execute(
            """UPDATE sales
               SET stage = %s,
                   shipment_status = COALESCE(shipment_status, 'production_running'),
                   updated_at = %s
               WHERE id = %s""",
            (new_stage, _now_str(), sale_id)
        )
    else:
        conn.execute(
            "UPDATE sales SET stage = %s, updated_at = %s WHERE id = %s",
            (new_stage, _now_str(), sale_id)
        )
    conn.execute(
        """INSERT INTO sales_stage_history (sale_id, from_stage, to_stage, notes)
           VALUES (%s, %s, %s, %s)""",
        (sale_id, current, new_stage, notes)
    )
    log_audit_action(conn, "SALE_MOVE", "sale", sale_id,
                     old_value=current, new_value=new_stage)
    conn.commit()
    notify_sale_stage(conn, sale_id, client_name, current, new_stage)
    return True


def advance_sale(conn: psycopg.Connection, sale_id: int,
                 notes: Optional[str] = None, via_lc: bool = False) -> Optional[str]:
    """Advance a sale to the next pipeline stage. Returns the new stage or None."""
    row = conn.execute("SELECT stage FROM sales WHERE id = %s", (sale_id,)).fetchone()
    if not row:
        return None
    nxt = _next_stage(row[0])
    if nxt and move_sale_to_stage(conn, sale_id, nxt, notes, via_lc=via_lc):
        return nxt
    return None


def update_sale_lc(conn: psycopg.Connection, sale_id: int, lc_number: str,
                   lc_date: str, shipment_date: str) -> bool:
    """Enter LC details for a sale. Returns False when the sale doesn't exist."""
    cur = conn.execute(
        """UPDATE sales
           SET lc_number = %s, lc_date = %s, shipment_date = %s, updated_at = %s
           WHERE id = %s""",
        (lc_number, lc_date, shipment_date, _now_str(), sale_id)
    )
    if cur.rowcount == 0:
        return False
    log_audit_action(conn, "SALE_LC", "sale", sale_id, new_value=lc_number)
    conn.commit()
    return True


def add_shipment(conn: psycopg.Connection, sale_id: int, ship_date: str,
                 invoice_number: str = None, invoice_date: str = None,
                 notes: str = None) -> Optional[int]:
    """Record an actual (possibly partial) shipment. Returns id, None if no sale."""
    if not (ship_date or "").strip():
        raise ValueError("ship_date is required")
    sale = conn.execute("SELECT id FROM sales WHERE id = %s", (sale_id,)).fetchone()
    if not sale:
        return None
    row = conn.execute(
        """INSERT INTO shipments (sale_id, ship_date, invoice_number, invoice_date, notes)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (sale_id, ship_date.strip(), (invoice_number or "").strip() or None,
         (invoice_date or "").strip() or None, (notes or "").strip() or None)
    ).fetchone()
    # atomic=False, commit below: the INSERT and its audit row are one unit.
    log_audit_action(conn, "SHIPMENT_RECORD", "sale", sale_id,
                     new_value=f"{ship_date.strip()}", atomic=False)
    conn.commit()
    return row[0]


def list_shipments(conn: psycopg.Connection, sale_id: int) -> List[Dict[str, Any]]:
    """Shipments for a sale, oldest first."""
    return [
        {"id": r[0], "sale_id": r[1], "ship_date": r[2],
         "invoice_number": r[3], "invoice_date": r[4], "notes": r[5],
         "created_at": r[6]}
        for r in conn.execute(
            "SELECT id, sale_id, ship_date, invoice_number, invoice_date, "
            "notes, created_at FROM shipments WHERE sale_id = %s "
            "ORDER BY ship_date, id", (sale_id,)
        ).fetchall()
    ]


def delete_shipment(conn: psycopg.Connection, sale_id: int,
                    shipment_id: int) -> bool:
    """Delete one shipment row. Returns False when not found."""
    row = conn.execute(
        "SELECT ship_date, invoice_number FROM shipments "
        "WHERE id = %s AND sale_id = %s", (shipment_id, sale_id)).fetchone()
    cursor = conn.execute(
        "DELETE FROM shipments WHERE id = %s AND sale_id = %s",
        (shipment_id, sale_id))
    if cursor.rowcount > 0:
        # P1-5: captured before the DELETE, because afterwards the row that
        # described this shipment no longer exists.
        # atomic=False, commit below: the DELETE and its audit row are one
        # unit. A committed delete with no audit row is the worst outcome —
        # the row is gone and nothing records that it ever existed.
        log_audit_action(conn, "SHIPMENT_DELETE", "shipment", shipment_id,
                         old_value=(f"ship_date={row[0]} "
                                    f"invoice_number={row[1]}" if row else None),
                         atomic=False)
        conn.commit()
    return cursor.rowcount > 0


def get_register_products(conn: psycopg.Connection,
                          company_id: int) -> List[Dict[str, Any]]:
    """Distinct PI products for a company (newest PI ref as hint)."""
    company = conn.execute("SELECT id FROM companies WHERE id = %s",
                           (company_id,)).fetchone()
    if not company:
        raise ValueError("unknown company")
    return [
        {"product_name": r[0], "pi_number": r[1], "sale_id": r[2]}
        for r in conn.execute(
            """SELECT DISTINCT ON (lower(si.product_name)) si.product_name,
                      s.pi_number, s.id
               FROM sale_items si JOIN sales s ON s.id = si.sale_id
               WHERE s.company_id = %s
               ORDER BY lower(si.product_name), s.id DESC""",
            (company_id,)).fetchall()
    ]


def get_sale_invoice_total(conn: psycopg.Connection, sale_id: int) -> float:
    """Sum of quantity * unit_price over all line items of a sale."""
    row = conn.execute(
        "SELECT COALESCE(ROUND(SUM(quantity * unit_price)::numeric, 2)::float8, 0) FROM sale_items WHERE sale_id = %s",
        (sale_id,)
    ).fetchone()
    return row[0] if row else 0


def get_sale_total_paid(conn: psycopg.Connection, sale_id: int) -> float:
    """Sum of all recorded payments for a sale."""
    row = conn.execute(
        "SELECT COALESCE(ROUND(SUM(payment_amount)::numeric, 2)::float8, 0) FROM sale_payments WHERE sale_id = %s",
        (sale_id,)
    ).fetchone()
    return row[0] if row else 0


def _sync_sale_payment_totals(conn: psycopg.Connection, sale_id: int) -> None:
    """Keep legacy sales.payment_amount/date in sync with payment records."""
    row = conn.execute(
        """SELECT COALESCE(ROUND(SUM(payment_amount)::numeric, 2)::float8, 0), MAX(payment_date)
           FROM sale_payments WHERE sale_id = %s""",
        (sale_id,)
    ).fetchone()
    conn.execute(
        """UPDATE sales SET payment_amount = %s, payment_date = %s,
           updated_at = %s WHERE id = %s""",
        (row[0], row[1], _now_str(), sale_id)
    )


def record_sale_payment(conn: psycopg.Connection, sale_id: int, payment_date: str,
                        payment_amount: float, notes: Optional[str] = None,
                        maturity_date: Optional[str] = None) -> Dict[str, Any]:
    """Append a (possibly partial) payment. Auto-completes from payment_due
    when total paid reaches the invoice total. Returns totals + stage.
    
    If maturity_date is provided and non-empty, updates the sale's maturity_date
    when it is NULL or different from the provided value.
    Clears maturity notification dedupe keys when the sale becomes fully paid.
    """
    # Phase 2-B. Must precede the maturity_date UPDATE below: a rejection that
    # lands after it would leave the header mutated on a call that failed.
    _finite(payment_amount)
    if payment_amount is None or payment_amount <= 0:
        raise ValueError("payment_amount must be positive")
    
    # Update maturity_date on sale header if provided and changed
    if maturity_date and str(maturity_date).strip():
        maturity_date = str(maturity_date).strip()
        row = conn.execute("SELECT maturity_date FROM sales WHERE id = %s", (sale_id,)).fetchone()
        current_maturity = row[0] if row else None
        if current_maturity != maturity_date:
            conn.execute("UPDATE sales SET maturity_date = %s, updated_at = %s WHERE id = %s",
                         (maturity_date, _now_str(), sale_id))
    
    cursor = conn.execute(
        """INSERT INTO sale_payments (sale_id, payment_date, payment_amount, notes)
           VALUES (%s, %s, %s, %s) RETURNING id, invoice_id""",
        (sale_id, payment_date, payment_amount, notes)
    )
    payment_id, payment_invoice_id = cursor.fetchone()
    _sync_sale_payment_totals(conn, sale_id)
    _resync_invoice_payment_status(conn, payment_invoice_id)
    total_paid = get_sale_total_paid(conn, sale_id)
    invoice_total = get_sale_invoice_total(conn, sale_id)
    old_balance = invoice_total - (total_paid - payment_amount)
    new_balance = invoice_total - total_paid
    log_audit_action(conn, "SALE_PAYMENT", "sale", sale_id,
                     old_value=f"balance={old_balance:g}",
                     new_value=f"paid={payment_amount:g} balance={new_balance:g} "
                               f"invoice_total={invoice_total:g}")
    row = conn.execute("SELECT stage FROM sales WHERE id = %s", (sale_id,)).fetchone()
    stage = row[0] if row else None
    was_completed = _complete_if_paid(conn, sale_id, stage, total_paid, invoice_total)
    if was_completed:
        stage = "completed"
    
    # Clear maturity notification dedupe keys when sale becomes fully paid
    # (regardless of stage transition)
    if total_paid >= invoice_total and invoice_total > 0:
        clear_maturity_dedupe(conn, sale_id)
    
    conn.commit()
    return {"payment_id": payment_id, "total_paid": total_paid,
            "balance": invoice_total - total_paid, "stage": stage}


def _complete_if_paid(conn: psycopg.Connection, sale_id: int, stage: Optional[str],
                      total_paid: float, invoice_total: float) -> bool:
    """Move a payment_due sale to completed once payments cover the invoice."""
    if stage == "payment_due" and total_paid >= invoice_total:
        move_sale_to_stage(conn, sale_id, "completed",
                           f"Paid in full (${total_paid})")
        return True
    return False


def update_sale_payment(conn: psycopg.Connection, sale_id: int, payment_date: str,
                        payment_amount: float) -> bool:
    """Legacy single-payment API — now appends a payment record."""
    record_sale_payment(conn, sale_id, payment_date, payment_amount)
    return True


def update_sale_payment_record(conn: psycopg.Connection, sale_id: int, payment_id: int,
                               payment_date: Optional[str] = None,
                               payment_amount: Optional[float] = None,
                               notes: Optional[str] = None) -> bool:
    """Edit a payment record. Reverts a completed sale to payment_due
    if the new total no longer covers the invoice, and re-derives the linked
    invoice's paid status.

    `sale_id` binds the payment to the sale named in the request URL: a
    payment may only be edited through the sale it belongs to, closing an
    IDOR where any payment_id could be addressed through any sale's route.
    """
    row = conn.execute("SELECT sale_id, payment_amount, invoice_id FROM sale_payments WHERE id = %s",
                       (payment_id,)).fetchone()
    if not row:
        return False
    db_sale_id, old_amount, invoice_id = row[0], row[1], row[2]
    if db_sale_id != sale_id:
        return False
    fields, vals = [], []
    if payment_date is not None:
        fields.append("payment_date = %s")
        vals.append(payment_date)
    if payment_amount is not None:
        # Phase 2-B, same column as record_sale_payment. Placed inside the
        # `is not None` branch: only a supplied amount can be non-finite.
        _finite(payment_amount)
        if payment_amount <= 0:
            raise ValueError("payment_amount must be positive")
        fields.append("payment_amount = %s")
        vals.append(payment_amount)
    if notes is not None:
        fields.append("notes = %s")
        vals.append(notes)
    if fields:
        vals.append(payment_id)
        conn.execute(f"UPDATE sale_payments SET {', '.join(fields)} WHERE id = %s", vals)
    _sync_sale_payment_totals(conn, sale_id)
    _resync_invoice_payment_status(conn, invoice_id)
    total_paid = get_sale_total_paid(conn, sale_id)
    invoice_total = get_sale_invoice_total(conn, sale_id)
    new_amount = payment_amount if payment_amount is not None else old_amount
    old_balance = invoice_total - (total_paid - new_amount + old_amount)
    new_balance = invoice_total - total_paid
    log_audit_action(conn, "SALE_PAYMENT_EDIT", "sale", sale_id,
                     old_value=f"amount={old_amount:g} balance={old_balance:g}",
                     new_value=f"amount={new_amount:g} balance={new_balance:g} "
                               f"invoice_total={invoice_total:g}")
    row = conn.execute("SELECT stage FROM sales WHERE id = %s", (sale_id,)).fetchone()
    stage = row[0] if row else None
    _complete_if_paid(conn, sale_id, stage, total_paid, invoice_total)
    _revert_if_unpaid(conn, sale_id)
    conn.commit()
    return True


def delete_sale_payment_record(conn: psycopg.Connection, sale_id: int, payment_id: int) -> bool:
    """Delete a payment record, re-sync totals, and re-derive the linked
    invoice's paid status (the invoice may no longer be covered).

    `sale_id` binds the payment to the sale named in the request URL: a
    payment may only be deleted through the sale it belongs to, closing an
    IDOR where any payment_id could be removed through any sale's route.
    """
    row = conn.execute("SELECT sale_id, payment_amount, invoice_id FROM sale_payments WHERE id = %s",
                       (payment_id,)).fetchone()
    if not row:
        return False
    db_sale_id = row[0]
    if db_sale_id != sale_id:
        return False
    sale_id = db_sale_id
    invoice_id = row[2]
    conn.execute("DELETE FROM sale_payments WHERE id = %s", (payment_id,))
    _sync_sale_payment_totals(conn, sale_id)
    _resync_invoice_payment_status(conn, invoice_id)
    total_paid = get_sale_total_paid(conn, sale_id)
    invoice_total = get_sale_invoice_total(conn, sale_id)
    old_balance = invoice_total - (total_paid + row[1])
    new_balance = invoice_total - total_paid
    log_audit_action(conn, "SALE_PAYMENT_DELETE", "sale", sale_id,
                     old_value=f"amount={row[1]:g} balance={old_balance:g}",
                     new_value=f"balance={new_balance:g} invoice_total={invoice_total:g}")
    _revert_if_unpaid(conn, sale_id)
    conn.commit()
    return True


def _revert_if_unpaid(conn: psycopg.Connection, sale_id: int) -> None:
    """Move a completed sale back to payment_due if payments no longer cover it."""
    row = conn.execute("SELECT stage FROM sales WHERE id = %s", (sale_id,)).fetchone()
    if row and row[0] == "completed":
        if get_sale_total_paid(conn, sale_id) < get_sale_invoice_total(conn, sale_id):
            move_sale_to_stage(conn, sale_id, "payment_due",
                               "Payment edited/deleted — balance outstanding")


def add_sale_item(conn: psycopg.Connection, sale_id: int, product_name: str,
                  quantity: float, unit_price: float, unit: str = "KG") -> int:
    """Add a line item to an existing sale. Returns the new item ID.

    Phase 2-B: quantity and unit_price must be finite and strictly > 0.
    Reversal of the first pass, which allowed zero here — see
    ``_positive_values`` and ``tests/test_sale_item_sign_rule.py``.
    """
    _finite(quantity, unit_price)
    _positive_values([("quantity", quantity), ("unit_price", unit_price)])
    unit = (unit or "KG").strip().upper() or "KG"
    cursor = conn.execute(
        """INSERT INTO sale_items (sale_id, product_name, quantity, unit_price, unit)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (sale_id, product_name, quantity, unit_price, unit)
    )
    item_id = cursor.fetchone()[0]
    # atomic=False, commit below: the INSERT and its audit row are one unit.
    log_audit_action(conn, "SALE_ITEM_ADD", "sale_item", item_id,
                     new_value=json.dumps({"sale_id": sale_id, "product_name": product_name,
                                           "quantity": quantity, "unit_price": unit_price, "unit": unit},
                                          default=str),
                     atomic=False)
    conn.commit()
    return item_id


def update_sale_item(conn: psycopg.Connection, sale_id: int, item_id: int,
                     product_name: Optional[str] = None,
                     quantity: Optional[float] = None,
                     unit_price: Optional[float] = None,
                     unit: Optional[str] = None) -> Optional[bool]:
    """Update fields of a sale line item.

    Refuses (409 via the route) to change quantity or unit_price on a line that
    already has invoice lines. The report's grain is the PI line while its money
    comes from invoice_items, and the next invoice's amount is derived from the
    PI total: moving quantity/price under existing invoice lines would size the
    next invoice off a total that no longer matches what was billed, and
    invoice_items.line_total would keep the old money. product_name and unit are
    descriptive only, so they stay editable.

    Phase 2-B: a supplied quantity/unit_price must be finite and strictly > 0.
    Two placement decisions, both load-bearing:

    * AFTER the invoiced-line conflict gate — that 409 is the more specific
      answer and the frontend already handles it, so a non-positive value must
      not change an invoiced line's error shape to a 400.
    * INSIDE the `is not None` branch — a patch that does not touch the money
      columns must not be validated against them, or a descriptive-only rename
      of an invoiced line would start failing.

    Phase 5 follow-up: `sale_id` is now required and bound at the SQL level,
    matching ``delete_sale_item``. A caller who knows one item's id could
    previously edit it through *any* sale's URL — the same object-reference
    hole Phase 5 closed on payments. Return contract is deliberately
    three-valued so the route can tell the two failure modes apart:

    * ``None`` — no such item under this sale (404).
    * ``False`` — item found, but the patch supplied nothing to write (200).
    * ``True`` — updated.
    """
    # Fetch old item for audit; the AND sale_id is the IDOR binding.
    old_row = conn.execute(
        "SELECT product_name, quantity, unit_price, unit FROM sale_items"
        " WHERE id = %s AND sale_id = %s",
        (item_id, sale_id)).fetchone()
    if not old_row:
        return None
    if quantity is not None or unit_price is not None:
        if conn.execute("SELECT COUNT(*) FROM invoice_items WHERE sale_item_id = %s",
                        (item_id,)).fetchone()[0]:
            raise InvoicedLineConflict(
                "cannot change quantity or price of a sale item that has invoice"
                " lines (adjust the invoice lines instead)")
        # Phase 2-B, after the 409 gate above and before any write.
        _finite(quantity, unit_price)
        _positive_values(
            [("quantity", quantity), ("unit_price", unit_price)])
    old_item = {"product_name": old_row[0], "quantity": old_row[1],
                "unit_price": old_row[2], "unit": old_row[3]}
    
    fields, vals = [], []
    if product_name is not None:
        fields.append("product_name = %s")
        vals.append(product_name)
    if quantity is not None:
        fields.append("quantity = %s")
        vals.append(quantity)
    if unit_price is not None:
        fields.append("unit_price = %s")
        vals.append(unit_price)
    if unit is not None:
        fields.append("unit = %s")
        vals.append((unit or "KG").strip().upper() or "KG")
    if not fields:
        return False
    vals.append(item_id)
    conn.execute(f"UPDATE sale_items SET {', '.join(fields)}"
                 " WHERE id = %s AND sale_id = %s", vals + [sale_id])

    # Audit log
    new_item = {"product_name": product_name, "quantity": quantity,
                "unit_price": unit_price, "unit": unit}
    # atomic=False, commit below: the UPDATE and its audit row are one unit.
    log_audit_action(conn, "SALE_ITEM_UPDATE", "sale_item", item_id,
                     old_value=json.dumps(old_item, default=str),
                     new_value=json.dumps(new_item, default=str), atomic=False)
    conn.commit()
    return True


def delete_sale_item(conn: psycopg.Connection, sale_id: int, item_id: int) -> bool:
    """Delete a single line item from a sale.

    Refuses (409 via the route) when the line has invoice_items. The commercial
    report's grain is the PI line while its money comes from invoice_items, and
    invoice_items.sale_item_id is ON DELETE SET NULL: deleting an invoiced line
    would drop the row out of the grain while its money stayed in the sale's
    total, so the rows would no longer sum to the sale's own receivable. Reduce
    the invoice line instead.

    `sale_id` scopes the delete: the line must belong to that sale, so it
    cannot be removed through another PI's URL. Returns False when it does
    not, which lets the route answer 404 instead of claiming success.
    """
    invoiced = conn.execute(
        "SELECT COUNT(*) FROM invoice_items WHERE sale_item_id = %s",
        (item_id,)).fetchone()[0]
    if invoiced:
        raise InvoicedLineConflict(
            "cannot delete a sale item that has invoice lines"
            " (adjust the invoice lines instead)")
    # P1-5 / D2 / D3: capture the line BEFORE the DELETE — including the PI it
    # came from and the production runs the cascade is about to destroy. Once
    # the row is gone this is the only surviving record, so the BOUNDED fields
    # lead: `product_name` and `pi_number` have no max_length and old_value is
    # capped at 2000 chars, so unbounded client text last would clip the
    # evidence — the same rule as COMPANY_UPDATE.
    row = conn.execute(
        "SELECT si.product_name, si.quantity, si.unit, si.unit_price, "
        "       s.pi_number, "
        "       (SELECT COUNT(*) FROM production_runs pr "
        "         WHERE pr.sale_item_id = si.id) "
        "FROM sale_items si JOIN sales s ON s.id = si.sale_id "
        "WHERE si.id = %s AND si.sale_id = %s", (item_id, sale_id)).fetchone()
    cursor = conn.execute("DELETE FROM sale_items WHERE id = %s AND sale_id = %s",
                          (item_id, sale_id))
    # Gate on rowcount: a delete that matched no row is not a delete and must
    # not leave an audit row claiming one happened.
    if cursor.rowcount > 0 and row:
        # atomic=False, commit below: the DELETE and its audit row are one
        # unit. The cascade here destroys production history, so a committed
        # delete whose audit failed loses the only record that it happened.
        log_audit_action(conn, "SALE_ITEM_DELETE", "sale_item", item_id,
                         old_value=f"runs={row[5]} qty={row[1]} {row[2]} "
                                   f"unit_price={row[3]} pi={row[4]} "
                                   f"product={row[0]}",
                         atomic=False)
        conn.commit()
    return cursor.rowcount > 0


def update_sale_full(conn: psycopg.Connection, sale_id: int, header: Dict[str, Any],
                     items: List[Dict[str, Any]], removed_ids: List[int]) -> Optional[Dict[str, Any]]:
    """Atomically replace a sale's header + items in ONE transaction.

    Validates everything before writing anything, then commits once. Any
    failure rolls back, so the sale is never left partial. psycopg
    transactions are implicit (replacing BEGIN IMMEDIATE). Raises ValueError
    on business-rule violations. Returns the updated sale dict, or None when
    the sale does not exist.

    Phase 2-B: item quantity/unit_price must be finite and strictly > 0. The whole
    item list is validated before the first write, so a poisoned line late in
    the batch cannot leave earlier lines applied.

    The sign rule here is a TIGHTENING of the pre-existing `qty < 0 or
    price < 0`, not a new rule: zero was previously accepted. That matters
    more here than anywhere else, because this function REPLACES the sale's
    entire line set — a sale that already held a zero-priced line becomes
    unsaveable through this route until the operator gives that line a positive
    price. Verified against production: 0 non-positive rows in `sale_items`, so
    nothing to migrate today.
    """
    removed_ids = [int(r) for r in (removed_ids or [])]
    try:
        row = conn.execute(
            "SELECT id, company_id, client_name, pi_date, comments, "
            "pi_file_path FROM sales WHERE id = %s",
            (sale_id,)).fetchone()
        if not row:
            conn.rollback()
            return None
        
        # Fetch old snapshot for audit
        old_sale = get_sale_by_id(conn, sale_id)
        old_snapshot = {
            "pi_number": old_sale.get("pi_number"),
            "lc_number": old_sale.get("lc_number"),
            "lc_date": old_sale.get("lc_date"),
            "shipment_date": old_sale.get("shipment_date"),
            "maturity_date": old_sale.get("maturity_date"),
            "comments": old_sale.get("comments"),
            "stage": old_sale.get("stage"),
            "items": [
                {"product_name": i["product_name"], "quantity": i["quantity"],
                 "unit_price": i["unit_price"], "unit": i["unit"]}
                for i in old_sale.get("items", [])
            ],
        }
        existing_ids = {r[0] for r in conn.execute(
            "SELECT id FROM sale_items WHERE sale_id = %s", (sale_id,)).fetchall()}

        pi_number = (header.get("pi_number") or "").strip()
        if not pi_number:
            raise ValueError("pi_number is required")
        # Absent optional fields keep their stored values (never NULL-wipe).
        pi_date = header["pi_date"] if "pi_date" in header else row[3]
        comments = header["comments"] if "comments" in header else row[4]
        # P1-15: this route accepted pi_file_path and silently dropped it, while
        # the flat patch route wrote it. Persist it here, keeping the stored
        # value when the caller omits the field.
        pi_file_path = header["pi_file_path"] if "pi_file_path" in header else row[5]
        if not items:
            raise ValueError("A sale must keep at least one product item")
        company_id = header.get("company_id")
        client_name = header.get("client_name")
        if company_id is not None:
            company = conn.execute(
                "SELECT name FROM companies WHERE id = %s",
                (company_id,)).fetchone()
            if not company:
                raise ValueError("unknown company")
            client_name = company[0]
        else:
            # Absent fields keep their stored values (never NULL-wipe links).
            company_id = row[1]
            if client_name is None:
                client_name = row[2]

        seen: List[Dict[str, Any]] = []
        for pos, it in enumerate(items):
            name = (it.get("product_name") or "").strip()
            if not name:
                raise ValueError(f"items[{pos}].product_name is required")
            try:
                qty = float(it.get("quantity", 0))
                price = float(it.get("unit_price", 0))
            except (TypeError, ValueError):
                raise ValueError(f"items[{pos}] quantity/unit_price must be numbers")
            # Phase 2-B: `qty < 0` is False for both inf and nan, so
            # finiteness is a separate check, not a reordering of this one.
            _finite(qty, price)
            # Was `if qty < 0 or price < 0: "Quantity and price cannot be
            # negative"`, which let 0 through. Now the same strict rule as
            # every other writer of these columns, with the field named.
            _positive_values([("quantity", qty), ("unit_price", price)])
            unit = (it.get("unit") or "KG").strip().upper() or "KG"
            item_id = it.get("id")
            if item_id is not None:
                item_id = int(item_id)
                if item_id not in existing_ids:
                    raise ValueError(f"items[{pos}].id {item_id} does not belong to sale {sale_id}")
                if item_id in removed_ids:
                    raise ValueError(f"item {item_id} is both updated and removed")
            seen.append({"id": item_id, "product_name": name, "quantity": qty, "unit_price": price, "unit": unit})

        for rid in removed_ids:
            if rid not in existing_ids:
                raise ValueError(f"removed id {rid} does not belong to sale {sale_id}")

        conn.execute(
            """UPDATE sales SET pi_number = %s, pi_date = %s, client_name = %s,
               company_id = %s, comments = %s, pi_file_path = %s,
               updated_at = %s WHERE id = %s""",
            (pi_number, pi_date, client_name, company_id,
             comments, pi_file_path, _now_str(), sale_id)
        )
        # Finding-5: LCs are scoped per company. A company change orphans
        # the link, so clear it in this SAME transaction (lane B re-attaches
        # explicitly). No-op when the LC still belongs to the new company.
        relink_sale_company(conn, sale_id, company_id)
        for it in seen:
            if it["id"] is None:
                conn.execute(
                    "INSERT INTO sale_items (sale_id, product_name, quantity, unit_price, unit) VALUES (%s, %s, %s, %s, %s)",
                    (sale_id, it["product_name"], it["quantity"], it["unit_price"], it["unit"])
                )
            else:
                # Same rule as delete_sale_item / update_sale_item: an invoiced
                # PI line cannot be re-based. The next invoice's amount is
                # derived from the PI total, so moving quantity/price here would
                # size it off a total the invoice set no longer matches while
                # invoice_items.line_total keeps the old money - leaving a
                # phantom receivable that can never be billed. Descriptive
                # fields (product_name/unit) and no-op writes stay allowed.
                stored = conn.execute(
                    "SELECT quantity, unit_price FROM sale_items WHERE id = %s",
                    (it["id"],)).fetchone()
                rebased = (
                    stored is not None
                    and (float(stored[0] or 0) != float(it["quantity"] or 0)
                         or float(stored[1] or 0) != float(it["unit_price"] or 0))
                )
                if rebased and conn.execute(
                        "SELECT COUNT(*) FROM invoice_items WHERE sale_item_id = %s",
                        (it["id"],)).fetchone()[0]:
                    raise InvoicedLineConflict(
                        f"cannot change quantity or price of sale item {it['id']}:"
                        " it has invoice lines (adjust the invoice lines instead)")
                conn.execute(
                    "UPDATE sale_items SET product_name = %s, quantity = %s, unit_price = %s, unit = %s WHERE id = %s",
                    (it["product_name"], it["quantity"], it["unit_price"], it["unit"], it["id"])
                )
        for rid in removed_ids:
            # Same rule as delete_sale_item: an invoiced PI line cannot be
            # removed or the report's rows stop summing to the sale's money.
            if conn.execute("SELECT COUNT(*) FROM invoice_items WHERE sale_item_id = %s",
                            (rid,)).fetchone()[0]:
                raise InvoicedLineConflict(
                    f"cannot remove sale item {rid}: it has invoice lines"
                    " (adjust the invoice lines instead)")
            conn.execute("DELETE FROM sale_items WHERE id = %s", (rid,))
        conn.commit()
        
        # Audit log. pi_file_path is a server-side path, so it must not enter
        # the audit trail — that would disclose the filesystem layout to anyone
        # who can read audit logs. Scrubbed from both sides so a path already
        # stored on the sale is not leaked by the "old" snapshot either.
        def _without_server_path(snapshot):
            cleaned = dict(snapshot)
            header_part = cleaned.get("header") or {}
            cleaned["header"] = {k: v for k, v in header_part.items()
                                 if k != "pi_file_path"}
            return cleaned

        new_snapshot = {
            "header": header,
            "items": items,
            "removed_ids": removed_ids,
        }
        log_audit_action(conn, "SALE_UPDATE", "sale", sale_id,
                         old_value=json.dumps(_without_server_path(old_snapshot), default=str),
                         new_value=json.dumps(_without_server_path(new_snapshot), default=str))
        
        return get_sale_by_id(conn, sale_id)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


def delete_sale(conn: psycopg.Connection, sale_id: int) -> bool:
    """Delete a sale and cascade-delete its items and history."""
    row = conn.execute("SELECT pi_number FROM sales WHERE id = %s", (sale_id,)).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM sales WHERE id = %s", (sale_id,))
    log_audit_action(conn, "SALE_DELETE", "sale", sale_id, old_value=row[0])
    conn.commit()
    return True


def get_sales_summary(conn: psycopg.Connection) -> Dict[str, Any]:
    """Aggregate counts per stage + total pipeline value."""
    summary: Dict[str, Any] = {stage: {"count": 0, "value": 0} for stage in SALE_STAGE_ORDER}
    rows = conn.execute("""
        SELECT s.stage,
               COUNT(DISTINCT s.id) AS cnt,
                COALESCE(ROUND(SUM(si.quantity * si.unit_price)::numeric, 2)::float8, 0) AS val
        FROM sales s
        LEFT JOIN sale_items si ON si.sale_id = s.id
        GROUP BY s.stage
    """).fetchall()
    for r in rows:
        summary[r[0]] = {"count": r[1], "value": r[2]}
    total_value = sum(v["value"] for v in summary.values())
    total_sales = sum(v["count"] for v in summary.values())
    summary["total_pipeline_value"] = total_value
    summary["total_sales"] = total_sales
    return summary


def _parse_date(value: Any) -> Optional[date]:
    """TEXT/`date` column → `date`, or None when unset/unparseable."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _payment_status(due_amount: float, maturity_date: Any,
                    received_amount: float) -> str:
    """Paid → Overdue → Partial → Due → Pending (contract precedence order)."""
    if due_amount <= 0:
        return "Paid"
    mat = _parse_date(maturity_date)
    if mat and mat < date.today():
        return "Overdue"
    if received_amount > 0:
        return "Partial"
    if mat:
        return "Due"
    return "Pending"


def get_commercial_report(conn: psycopg.Connection, filters: dict = None) -> "tuple[List[Dict[str, Any]], int]":
    """Live commercial report — one output row per sale_item, USD only.

    Returns (rows, total): `rows` is the requested page (page/page_size),
    `total` is how many rows match the filters across ALL pages.

    Item-level columns (product, qty, price, total_price, dates on the
    shipment) vary per row. Sale-level payment columns — received_amount,
    due_amount, receive_date, maturity_date, payment_status,
    payment_comment — are computed per SALE and repeated identically on
    every row of that sale. Sale total and sale paid are aggregated in
    scalar subqueries so item x shipment x payment joins can never
    double count.

    INVOICE-DRIVEN: per-row quantity/total come from invoice_items
    (SUM per sale_item). Only sales with >=1 invoice row are included
    (EXISTS invoices). Zero-invoiced lines show 0.

    filters: {
        "date_anchor": "pi_date" | "lc_date" | "shipment_date" | "receive_date" | "maturity_date",
        "date_from": "YYYY-MM-DD",
        "date_to": "YYYY-MM-DD",
        "customer_name": str,
        "product_name": str,
        "q": str,  # quick search: matches PI number OR customer OR product
        "company_id": int,
        "stage": "pi_issued" | "lc_received" | "shipment_ongoing" | "payment_due" | "completed",
        "payment_status": "Paid" | "Overdue" | "Partial" | "Due" | "Pending",
        "lc_id": int,  # exact match on sales.lc_id
        "page": 1,
        "page_size": 50,
    }
    """
    if filters is None:
        filters = {}

    # Map date_anchor to SQL column expression for CTE
    date_anchor_map = {
        "pi_date": "s.pi_date",
        "lc_date": "s.lc_date",
        "shipment_date": "s.shipment_date",
        "receive_date": "COALESCE((SELECT MAX(sp.payment_date) FROM sale_payments sp WHERE sp.sale_id = s.id), s.pi_date)",
        "maturity_date": "s.maturity_date",
    }
    date_anchor_expr = date_anchor_map.get(filters.get("date_anchor"), "s.pi_date")

    # Build WHERE clauses referencing CTE columns
    where_clauses = []
    params = {}

    # Date range filters using SUBSTR for TEXT date columns
    if filters.get("date_from"):
        where_clauses.append("SUBSTR(date_anchor_col, 1, 10) >= %(date_from)s")
        params["date_from"] = filters["date_from"]
    if filters.get("date_to"):
        where_clauses.append("SUBSTR(date_anchor_col, 1, 10) <= %(date_to)s")
        params["date_to"] = filters["date_to"]

    if filters.get("customer_name"):
        where_clauses.append("customer_name ILIKE %(customer_name)s")
        params["customer_name"] = f"%{filters['customer_name']}%"

    if filters.get("product_name"):
        where_clauses.append("""EXISTS (
            SELECT 1 FROM sale_items si3
            WHERE si3.sale_id = base.sale_id
            AND si3.product_name ILIKE %(product_name)s
        )""")
        params["product_name"] = f"%{filters['product_name']}%"

    # Quick search: ONE term matches PI number OR customer OR product
    # (OR semantics — never AND-ed across the three).
    if filters.get("q"):
        where_clauses.append("""(
            pi_number ILIKE %(q)s
            OR customer_name ILIKE %(q)s
            OR EXISTS (
                SELECT 1 FROM sale_items si4
                WHERE si4.sale_id = base.sale_id
                AND si4.product_name ILIKE %(q)s
            )
        )""")
        params["q"] = f"%{filters['q']}%"

    if filters.get("company_id") is not None:
        where_clauses.append("company_id = %(company_id)s")
        params["company_id"] = filters["company_id"]

    if filters.get("stage"):
        where_clauses.append("stage = %(stage)s")
        params["stage"] = filters["stage"]

    if filters.get("lc_id") is not None:
        where_clauses.append("lc_id = %(lc_id)s")
        params["lc_id"] = filters["lc_id"]

    if filters.get("payment_status"):
        where_clauses.append("""
            CASE
                WHEN sale_total - received_amount <= 0 THEN 'Paid'
                WHEN maturity_date::date < CURRENT_DATE AND sale_total - received_amount > 0 THEN 'Overdue'
                WHEN received_amount > 0 THEN 'Partial'
                WHEN maturity_date IS NOT NULL THEN 'Due'
                ELSE 'Pending'
            END = %(payment_status)s
        """)
        params["payment_status"] = filters["payment_status"]

    # Build WHERE clauses: start with the invoice EXISTS requirement, then add filter clauses
    all_where_clauses = ["EXISTS (SELECT 1 FROM invoices WHERE sale_id = base.sale_id)"]
    all_where_clauses.extend(where_clauses)
    where_sql = "WHERE " + " AND ".join(all_where_clauses)

    # Pagination. Clamped, not trusted: a negative or zero page reaches
    # LIMIT/OFFSET as a negative OFFSET, which Postgres rejects with
    # InvalidRowCountInResultOffsetClause -> a 500 on a mere query param. Only
    # the lower bound is enforced here; the export path deliberately asks for a
    # page_size above the interactive cap.
    page = max(int(filters.get("page", 1) or 1), 1)
    page_size = max(int(filters.get("page_size", 50) or 50), 1)
    offset = (page - 1) * page_size
    params["page_size"] = page_size
    params["offset"] = offset

    query = f"""
        WITH base AS (
            SELECT
                s.id AS sale_id,
                COALESCE(c.name, s.client_name) AS customer_name,
                s.pi_number, s.pi_date,
                s.lc_number, s.lc_date,
                s.shipment_date, s.maturity_date, s.stage, s.comments,
                s.company_id,
                s.lc_id,
                si.product_name, si.unit,
                COALESCE(ii.inv_qty, 0)::float8 AS quantity,
                si.unit_price,
                ROUND((COALESCE(ii.inv_qty, 0) * si.unit_price)::numeric, 2)::float8 AS total_price,
                (SELECT MAX(sh.invoice_date)
                   FROM shipments sh WHERE sh.sale_id = s.id) AS invoice_date,
                (SELECT MAX(sh.ship_date)
                   FROM shipments sh WHERE sh.sale_id = s.id) AS latest_ship_date,
                (SELECT MAX(sp.payment_date)
                   FROM sale_payments sp WHERE sp.sale_id = s.id) AS receive_date,
                (SELECT COALESCE(ROUND(SUM(sp.payment_amount)::numeric, 2), 0)::float8
                   FROM sale_payments sp WHERE sp.sale_id = s.id) AS received_amount,
                (SELECT COALESCE(ROUND(SUM(ii2.line_total)::numeric, 2), 0)::float8
                   FROM invoice_items ii2
                   JOIN invoices i2 ON i2.id = ii2.invoice_id
                   WHERE i2.sale_id = s.id) AS sale_total,
                {date_anchor_expr} AS date_anchor_col
            FROM sales s
            LEFT JOIN sale_items si ON si.sale_id = s.id
            LEFT JOIN companies c ON c.id = s.company_id
            LEFT JOIN LATERAL (
                SELECT SUM(ii.quantity) AS inv_qty
                FROM invoice_items ii
                JOIN invoices i ON i.id = ii.invoice_id
                WHERE i.sale_id = s.id
                AND ii.sale_item_id = si.id
            ) ii ON true
        )
        SELECT *, COUNT(*) OVER() AS _total_rows
        FROM base
        {where_sql}
        ORDER BY sale_id DESC
        LIMIT %(page_size)s OFFSET %(offset)s
    """

    rows = conn.execute(query, params).fetchall()

    # Total counts every row matching the filters, BEFORE LIMIT/OFFSET, so the
    # caller can page honestly instead of mistaking a full page for the report.
    total = rows[0][-1] if rows else 0

    report = []
    for r in rows:
        (sale_id, customer_name, pi_number, pi_date, lc_number, lc_date,
         shipment_date, maturity_date, stage, comments, company_id, lc_id,
         product_name, unit, quantity, unit_price, total_price,
         invoice_date, latest_ship_date, receive_date, received_amount,
         sale_total, date_anchor_col, _total_rows) = r

        received = round(float(received_amount or 0), 2)
        due_amount = round(max(round(float(sale_total or 0), 2) - received, 0.0), 2)
        pay_status = _payment_status(due_amount, maturity_date, received)
        pay_comment = (comments or "").strip()

        report.append({
            "sale_id": sale_id,
            "customer_name": customer_name,
            "pi_number": pi_number,
            "pi_date": pi_date,
            "lc_number": lc_number,
            "lc_date": lc_date,
            "product_name": product_name,
            "unit": unit,
            "quantity": quantity,
            "unit_price": unit_price,
            "total_price": total_price,
            "invoice_date": invoice_date,
            "latest_ship_date": latest_ship_date,
            "actual_ship_date": shipment_date,
            "maturity_date": maturity_date,
            "receive_date": receive_date,
            "received_amount": received,
            "due_amount": due_amount,
            "payment_status": pay_status,
            "payment_comment": pay_comment,
        })

    return report, total


def get_commercial_report_summary(conn: psycopg.Connection, filters: dict = None) -> List[Dict[str, Any]]:
    """
    filters: same as get_commercial_report plus "group_by": "month" | "week" | "year" | "none"
    Returns period-aggregated summary:
    [{
        "period_start": "2026-09-01",
        "period_end": "2026-09-30",
        "kpis": {"gross_sales": 120000, "received": 90000, "due": 30000, "overdue": 5000, "order_count": 12},
        "items": [...]  # detail rows for this period (when group_by != "none")
    }, ...]
    """
    if filters is None:
        filters = {}

    # Map date_anchor to SQL column expression for CTE
    date_anchor_map = {
        "pi_date": "s.pi_date",
        "lc_date": "s.lc_date",
        "shipment_date": "s.shipment_date",
        "receive_date": "COALESCE((SELECT MAX(sp.payment_date) FROM sale_payments sp WHERE sp.sale_id = s.id), s.pi_date)",
        "maturity_date": "s.maturity_date",
    }
    date_anchor_expr = date_anchor_map.get(filters.get("date_anchor"), "s.pi_date")

    group_by = filters.get("group_by", "month")
    if group_by not in ("month", "week", "year", "none"):
        group_by = "month"

    # Build WHERE clauses referencing CTE columns
    where_clauses = []
    params = {}

    if filters.get("date_from"):
        where_clauses.append("SUBSTR(date_anchor_col, 1, 10) >= %(date_from)s")
        params["date_from"] = filters["date_from"]
    if filters.get("date_to"):
        where_clauses.append("SUBSTR(date_anchor_col, 1, 10) <= %(date_to)s")
        params["date_to"] = filters["date_to"]

    if filters.get("customer_name"):
        where_clauses.append("customer_name ILIKE %(customer_name)s")
        params["customer_name"] = f"%{filters['customer_name']}%"

    if filters.get("product_name"):
        where_clauses.append("""EXISTS (
            SELECT 1 FROM sale_items si3
            WHERE si3.sale_id = {alias}.sale_id
            AND si3.product_name ILIKE %(product_name)s
        )""")
        params["product_name"] = f"%{filters['product_name']}%"

    # Quick search: ONE term matches PI number OR customer OR product
    # (OR semantics — never AND-ed across the three).
    if filters.get("q"):
        where_clauses.append("""(
            pi_number ILIKE %(q)s
            OR customer_name ILIKE %(q)s
            OR EXISTS (
                SELECT 1 FROM sale_items si4
                WHERE si4.sale_id = {alias}.sale_id
                AND si4.product_name ILIKE %(q)s
            )
        )""")
        params["q"] = f"%{filters['q']}%"

    if filters.get("company_id") is not None:
        where_clauses.append("company_id = %(company_id)s")
        params["company_id"] = filters["company_id"]

    if filters.get("stage"):
        where_clauses.append("stage = %(stage)s")
        params["stage"] = filters["stage"]

    if filters.get("lc_id") is not None:
        where_clauses.append("lc_id = %(lc_id)s")
        params["lc_id"] = filters["lc_id"]

    if filters.get("payment_status"):
        where_clauses.append("""
            CASE
                WHEN sale_total - received_amount <= 0 THEN 'Paid'
                WHEN maturity_date::date < CURRENT_DATE AND sale_total - received_amount > 0 THEN 'Overdue'
                WHEN received_amount > 0 THEN 'Partial'
                WHEN maturity_date IS NOT NULL THEN 'Due'
                ELSE 'Pending'
            END = %(payment_status)s
        """)
        params["payment_status"] = filters["payment_status"]

    # The product filter is the only clause that names the base relation, so it
    # is rendered once per query: the summary collapses `base` to one row per
    # sale inside a CTE aliased `b`, while the detail query selects FROM base.
    # Only the static alias is substituted — every user value stays a %(name)s
    # placeholder. `prefix` lets the per-period items query chain the clauses
    # with AND after its own WHERE instead of emitting a second WHERE.
    def _where_sql(alias, prefix="WHERE "):
        rendered = (c.replace("{alias}", alias) for c in where_clauses)
        return prefix + " AND ".join(rendered) if where_clauses else ""

    # Include the invoice EXISTS requirement in the sale-level WHERE
    sale_where_sql = _where_sql("b", prefix="WHERE ")
    if sale_where_sql:
        sale_where_sql += " AND EXISTS (SELECT 1 FROM invoices WHERE sale_id = b.sale_id)"
    else:
        sale_where_sql = "WHERE EXISTS (SELECT 1 FROM invoices WHERE sale_id = b.sale_id)"
    period_where_sql = _where_sql("base", prefix="AND ")

    # Build the period truncation expression using the CTE column
    if group_by == "month":
        period_trunc = "DATE_TRUNC('month', date_anchor_col::timestamp)::DATE"
        period_end = "(DATE_TRUNC('month', date_anchor_col::timestamp) + INTERVAL '1 month' - INTERVAL '1 day')::DATE"
    elif group_by == "week":
        period_trunc = "DATE_TRUNC('week', date_anchor_col::timestamp)::DATE"
        period_end = "(DATE_TRUNC('week', date_anchor_col::timestamp) + INTERVAL '1 week' - INTERVAL '1 day')::DATE"
    elif group_by == "year":
        period_trunc = "DATE_TRUNC('year', date_anchor_col::timestamp)::DATE"
        period_end = "(DATE_TRUNC('year', date_anchor_col::timestamp) + INTERVAL '1 year' - INTERVAL '1 day')::DATE"
    else:
        # "none" - single period covering all
        period_trunc = "DATE '1900-01-01'"
        period_end = "DATE '2100-12-31'"

    # Summary query with period grouping.
    #
    # `base` carries ONE ROW PER LINE ITEM, so the sale-level scalars
    # (received_amount / sale_total / maturity_date) repeat on every row of the
    # same sale: a bare SUM(received_amount) multiplies the money by the item
    # count (a 3-item sale of 3000 paid 1000 reported 3000 received).
    # sale_rows collapses `base` to ONE ROW PER SALE (MAX keeps the single
    # copy of each sale-level scalar, SUM(total_price) keeps the per-item gross
    # ), so the outer per-period aggregates count the money exactly once per
    # sale while gross_sales stays a per-item sum. Money follows the file
    # convention COALESCE(ROUND(SUM(...)::numeric, 2)::float8, 0) — no float
    # drift — and due is clamped with GREATEST(..., 0) so an overpaid sale
    # reports 0 due, matching get_commercial_report's Python clamp.
    #
    # INVOICE-DRIVEN: per-row quantity/total come from invoice_items
    # (SUM per sale_item). Only sales with >=1 invoice row are included
    # (EXISTS invoices). Zero-invoiced lines show 0.
    summary_query = f"""
        WITH base AS (
            SELECT
                s.id AS sale_id,
                COALESCE(c.name, s.client_name) AS customer_name,
                s.pi_number, s.pi_date,
                s.lc_number, s.lc_date,
                s.shipment_date, s.maturity_date, s.stage, s.comments,
                s.company_id,
                s.lc_id,
                si.product_name, si.unit, si.unit_price,
                COALESCE(ii.inv_qty, 0)::float8 AS quantity,
                ROUND((COALESCE(ii.inv_qty, 0) * si.unit_price)::numeric, 2)::float8 AS total_price,
                (SELECT MAX(sh.invoice_date)
                   FROM shipments sh WHERE sh.sale_id = s.id) AS invoice_date,
                (SELECT MAX(sh.ship_date)
                   FROM shipments sh WHERE sh.sale_id = s.id) AS latest_ship_date,
                (SELECT MAX(sp.payment_date)
                   FROM sale_payments sp WHERE sp.sale_id = s.id) AS receive_date,
                (SELECT COALESCE(ROUND(SUM(sp.payment_amount)::numeric, 2), 0)::float8
                   FROM sale_payments sp WHERE sp.sale_id = s.id) AS received_amount,
                (SELECT COALESCE(ROUND(SUM(ii2.line_total)::numeric, 2), 0)::float8
                   FROM invoice_items ii2
                   JOIN invoices i2 ON i2.id = ii2.invoice_id
                   WHERE i2.sale_id = s.id) AS sale_total,
                {date_anchor_expr} AS date_anchor_col
            FROM sales s
            LEFT JOIN sale_items si ON si.sale_id = s.id
            LEFT JOIN companies c ON c.id = s.company_id
            LEFT JOIN LATERAL (
                SELECT SUM(ii.quantity) AS inv_qty
                FROM invoice_items ii
                JOIN invoices i ON i.id = ii.invoice_id
                WHERE i.sale_id = s.id
                AND ii.sale_item_id = si.id
            ) ii ON true
        ),
        sale_rows AS (
            SELECT
                b.sale_id,
                MAX(b.date_anchor_col) AS date_anchor_col,
                MAX(b.customer_name) AS customer_name,
                MAX(b.company_id) AS company_id,
                MAX(b.stage) AS stage,
                MAX(b.maturity_date) AS maturity_date,
                MAX(b.received_amount) AS received_amount,
                MAX(b.sale_total) AS sale_total,
                SUM(b.total_price) AS gross_sales
            FROM base b
            {sale_where_sql}
            GROUP BY b.sale_id
        )
        SELECT
            {period_trunc} AS period_start,
            {period_end} AS period_end,
            COUNT(DISTINCT sale_id) AS order_count,
            COALESCE(ROUND(SUM(gross_sales)::numeric, 2)::float8, 0) AS gross_sales,
            COALESCE(ROUND(SUM(received_amount)::numeric, 2)::float8, 0) AS total_received,
            COALESCE(ROUND(SUM(GREATEST(sale_total - received_amount, 0))::numeric, 2)::float8, 0) AS total_due,
            COALESCE(ROUND(SUM(CASE WHEN maturity_date::date < CURRENT_DATE
                                     AND sale_total - received_amount > 0
                                  THEN sale_total - received_amount ELSE 0 END)::numeric, 2)::float8, 0) AS total_overdue
        FROM sale_rows
        GROUP BY period_start, period_end
        ORDER BY period_start DESC
    """

    summary_rows = conn.execute(summary_query, params).fetchall()

    periods = []
    for sr in summary_rows:
        period_start, period_end, order_count, gross_sales, total_received, total_due, total_overdue = sr
        periods.append({
            "period_start": str(period_start),
            "period_end": str(period_end),
            "kpis": {
                "gross_sales": float(gross_sales or 0),
                "received": float(total_received or 0),
                "due": float(total_due or 0),
                "overdue": float(total_overdue or 0),
                "order_count": int(order_count or 0),
            },
            "items": [],  # Will be populated below if group_by != "none"
        })

    # If group_by != "none", also fetch detail items for each period for expandable rows
    if group_by != "none":
        # Query detail items for each period
        for period in periods:
            period_start = period["period_start"]
            period_end = period["period_end"]
            
            # Build detail query for this period
            detail_params = params.copy()
            detail_params["period_start"] = period_start
            detail_params["period_end"] = period_end
            
            detail_query = f"""
                WITH base AS (
                    SELECT
                        s.id AS sale_id,
                        COALESCE(c.name, s.client_name) AS customer_name,
                        s.pi_number, s.pi_date,
                        s.lc_number, s.lc_date,
                        s.shipment_date, s.maturity_date, s.stage, s.comments,
                        s.company_id,
                        s.lc_id,
                        si.product_name, si.unit, si.unit_price,
                        COALESCE(ii.inv_qty, 0)::float8 AS quantity,
                        ROUND((COALESCE(ii.inv_qty, 0) * si.unit_price)::numeric, 2)::float8 AS total_price,
                        (SELECT MAX(sh.invoice_date)
                           FROM shipments sh WHERE sh.sale_id = s.id) AS invoice_date,
                        (SELECT MAX(sh.ship_date)
                           FROM shipments sh WHERE sh.sale_id = s.id) AS latest_ship_date,
                        (SELECT MAX(sp.payment_date)
                           FROM sale_payments sp WHERE sp.sale_id = s.id) AS receive_date,
                        (SELECT COALESCE(ROUND(SUM(sp.payment_amount)::numeric, 2), 0)::float8
                           FROM sale_payments sp WHERE sp.sale_id = s.id) AS received_amount,
                        (SELECT COALESCE(ROUND(SUM(ii2.line_total)::numeric, 2), 0)::float8
                           FROM invoice_items ii2
                           JOIN invoices i2 ON i2.id = ii2.invoice_id
                           WHERE i2.sale_id = s.id) AS sale_total,
                        {date_anchor_expr} AS date_anchor_col
                    FROM sales s
                    LEFT JOIN sale_items si ON si.sale_id = s.id
                    LEFT JOIN companies c ON c.id = s.company_id
                    LEFT JOIN LATERAL (
                        SELECT SUM(ii.quantity) AS inv_qty
                        FROM invoice_items ii
                        JOIN invoices i ON i.id = ii.invoice_id
                        WHERE i.sale_id = s.id
                        AND ii.sale_item_id = si.id
                    ) ii ON true
                )
                SELECT
                    sale_id, customer_name, pi_number, pi_date, lc_number, lc_date,
                    product_name, unit, quantity, unit_price, total_price,
                    invoice_date, latest_ship_date, shipment_date AS actual_ship_date, maturity_date,
                    receive_date, received_amount, 
                    GREATEST(sale_total - received_amount, 0) AS due_amount,
                    CASE 
                        WHEN sale_total - received_amount <= 0 THEN 'Paid'
                        WHEN maturity_date::date < CURRENT_DATE AND sale_total - received_amount > 0 THEN 'Overdue'
                        WHEN received_amount > 0 THEN 'Partial'
                        WHEN maturity_date IS NOT NULL THEN 'Due'
                        ELSE 'Pending'
                    END AS payment_status,
                    comments AS payment_comment
                FROM base
                WHERE EXISTS (SELECT 1 FROM invoices WHERE sale_id = base.sale_id)
                AND {period_trunc} >= %(period_start)s
                AND {period_trunc} <= %(period_end)s
                {period_where_sql}
                ORDER BY sale_id DESC
            """
            
            detail_rows = conn.execute(detail_query, detail_params).fetchall()
            
            items = []
            for r in detail_rows:
                (sale_id, customer_name, pi_number, pi_date, lc_number, lc_date,
                 product_name, unit, quantity, unit_price, total_price,
                 invoice_date, latest_ship_date, actual_ship_date, maturity_date,
                 receive_date, received_amount, due_amount, payment_status, payment_comment) = r
                
                items.append({
                    "sale_id": sale_id,
                    "customer_name": customer_name,
                    "pi_number": pi_number,
                    "pi_date": pi_date,
                    "lc_number": lc_number,
                    "lc_date": lc_date,
                    "product_name": product_name,
                    "unit": unit,
                    "quantity": float(quantity or 0),
                    "unit_price": float(unit_price or 0),
                    "total_price": float(total_price or 0),
                    "invoice_date": invoice_date,
                    "latest_ship_date": latest_ship_date,
                    "actual_ship_date": actual_ship_date,
                    "maturity_date": maturity_date,
                    "receive_date": receive_date,
                    "received_amount": float(received_amount or 0),
                    "due_amount": float(due_amount or 0),
                    "payment_status": payment_status,
                    "payment_comment": payment_comment,
                })
            
            period["items"] = items
    
    return periods


def _to_decimal(value) -> Decimal:
    """Exact Decimal for a DB numeric/REAL value (no float drift)."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


_CENT = Decimal("0.01")


def _money(value) -> Decimal:
    """Round a Decimal to 2dp half-up, the same rule the SQL money uses."""
    return _to_decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP)


def _invoice_money_already_on_sale(conn: psycopg.Connection,
                                   sale_id: int) -> Decimal:
    """Money already committed against a sale, exactly (no float drift).

    Summed from the invoice LINES, because the commercial report sums lines.
    Legacy line-less invoices are the one exception: they carry money that the
    report cannot see, so their header is added here too, otherwise the derived
    remainder would re-bill it.
    """
    row = conn.execute(
        """SELECT COALESCE((SELECT SUM(ii.line_total)
                              FROM invoice_items ii
                              JOIN invoices i ON i.id = ii.invoice_id
                             WHERE i.sale_id = %s), 0)
                + COALESCE((SELECT SUM(i.amount)
                              FROM invoices i
                             WHERE i.sale_id = %s
                               AND i.amount IS NOT NULL
                               AND NOT EXISTS (SELECT 1 FROM invoice_items x
                                                 WHERE x.invoice_id = i.id)), 0)""",
        (sale_id, sale_id)).fetchone()
    raw = row[0] or 0
    if isinstance(raw, Decimal) and not raw.is_finite():
        raise ValueError(
            "sale has non-finite invoiced lines (check invoice quantities)")
    return _money(raw)


def remaining_invoice_money(conn: psycopg.Connection, sale_id: int) -> Decimal:
    """PI total minus everything already invoiced, floored at zero.

    A non-finite line total (NaN/Inf in a REAL quantity or unit_price)
    poisons the SQL SUM and would otherwise raise ``InvalidOperation`` deep
    in the money math, surfacing as a generic 500. It is bad input data, so
    it raises ``ValueError`` (mapped to 400 by the routes) naming the cause.
    """
    raw_total = get_sale_invoice_total(conn, sale_id) or 0
    if isinstance(raw_total, float) and not math.isfinite(raw_total):
        raise ValueError(
            "sale has a non-finite line total "
            "(check quantities and unit prices)")
    total = _money(raw_total)
    already = _invoice_money_already_on_sale(conn, sale_id)
    return max(total - already, Decimal("0"))


def pi_proposal_vs_invoiced(conn: psycopg.Connection, sale_id: int) -> Decimal:
    """PI proposal total minus actual invoiced total (can be negative).

    Used for variance reporting: shows how much invoiced amounts deviate
    from the PI proposal. Positive = invoiced less than PI; negative = invoiced more.
    """
    raw_total = get_sale_invoice_total(conn, sale_id) or 0
    if isinstance(raw_total, float) and not math.isfinite(raw_total):
        raise ValueError(
            "sale has a non-finite line total "
            "(check quantities and unit prices)")
    total = _money(raw_total)
    already = _invoice_money_already_on_sale(conn, sale_id)
    return _money(total - already)


def validate_invoice_lines(conn: psycopg.Connection, sale_id: int,
                           lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validate and enrich invoice lines with server-enforced prices.

    Args:
        conn: Database connection
        sale_id: The sale ID these lines belong to
        lines: List of {"sale_item_id": int, "quantity": float}

    Returns:
        List of validated lines with added fields:
        {"sale_item_id", "product_name", "unit", "quantity", "unit_price", "line_total"}

    Raises:
        ValueError: If line references invalid sale_item, qty <= 0, or non-finite qty.
    """
    if not lines:
        raise ValueError("at least one invoice line required")

    validated = []
    for line in lines:
        sale_item_id = line.get("sale_item_id")
        quantity = line.get("quantity")

        if sale_item_id is None:
            raise ValueError("each line must have a sale_item_id")
        try:
            qty = float(quantity)
        except (TypeError, ValueError):
            raise ValueError("quantity must be a number")
        if not math.isfinite(qty) or qty <= 0:
            raise ValueError("quantity must be finite and > 0")

        # Verify sale_item belongs to this sale and get authoritative price
        si = conn.execute(
            "SELECT id, product_name, unit, unit_price FROM sale_items "
            "WHERE id = %s AND sale_id = %s FOR UPDATE",
            (sale_item_id, sale_id)).fetchone()
        if not si:
            raise ValueError(f"sale item {sale_item_id} not found in this sale")

        unit_price = float(si[3] or 0)
        line_total = round(qty * unit_price, 2)

        validated.append({
            "sale_item_id": sale_item_id,
            "product_name": si[1],
            "unit": si[2] or "KG",
            "quantity": qty,
            "unit_price": unit_price,
            "line_total": line_total,
        })
    return validated


def check_invoice_tolerance(conn: psycopg.Connection, sale_id: int,
                            lines: List[Dict[str, Any]]) -> None:
    """Enforce 5% over-invoice ceiling per PI line.

    Args:
        conn: DB connection
        sale_id: Sale ID
        lines: List of {"sale_item_id": int, "quantity": float}
               These are the NEW lines being added.

    Raises:
        ValueError: If any PI line's total invoiced qty (existing + new) > PI_qty * 1.05
    """
    from collections import defaultdict

    # Aggregate new quantities by sale_item_id
    qty_by_item = defaultdict(Decimal)
    for line in lines:
        qty_by_item[line["sale_item_id"]] += Decimal(str(line["quantity"]))

    # Fetch PI line quantities AND existing invoiced quantities
    placeholders = ",".join(["%s"] * len(qty_by_item))
    rows = conn.execute(
        f"""SELECT si.id, si.quantity,
                  COALESCE(SUM(ii.quantity)::numeric, 0) AS already_invoiced
           FROM sale_items si
           LEFT JOIN invoice_items ii ON ii.sale_item_id = si.id
           LEFT JOIN invoices i ON i.id = ii.invoice_id
           WHERE si.id IN ({placeholders})
           GROUP BY si.id, si.quantity""",
        tuple(qty_by_item.keys())
    ).fetchall()

    for item_id, pi_qty, already_invoiced in rows:
        invoiced = qty_by_item[item_id] + Decimal(str(already_invoiced or 0))
        pi_qty_dec = Decimal(str(pi_qty))
        ceiling = (pi_qty_dec * OVER_INVOICE_TOLERANCE).quantize(Decimal("0.000001"))
        if invoiced > ceiling:
            raise ToleranceExceeded(
                f"PI line {item_id}: invoiced {invoiced:g} "
                f"exceeds {ceiling:g} (PI qty {pi_qty_dec:g} + 5%)"
            )


def _assert_invoice_mutable(conn: psycopg.Connection, invoice_id: int) -> None:
    """Raise InvoiceNotFound if the invoice is gone, ValueError if frozen."""
    row = conn.execute(
        "SELECT status FROM invoices WHERE id = %s FOR UPDATE", (invoice_id,)
    ).fetchone()
    if not row:
        raise InvoiceNotFound("invoice not found")
    status = row[0]
    if status in ("booked", "paid", "shipped", "void"):
        raise ValueError(f"cannot modify a {status} invoice")


def create_invoice(conn: psycopg.Connection, sale_id: int, invoice_number: str,
                   lines: List[Dict[str, Any]],
                   invoice_date: str) -> Dict[str, Any]:
    """Create a new invoice for a sale with explicit lines and a stated date.

    This is the only create mode. ``lines`` is required and non-empty
    (``[{"sale_item_id": int, "quantity": float}, ...]``):
    - Quantities may exceed PI quantities (shipment advantage; the 5% ceiling
      in ``check_invoice_tolerance`` still applies).
    - Unit price is always inherited from the PI line (server-enforced).
    - ``invoice_date`` is required and stored as given (stripped).

    The legacy auto-seed and the header-only test seam are gone: an invoice can
    no longer exist without the lines the invoice-driven report sums.

    Invoice amount is always Σ line totals (single source of truth).
    Every invoice is born ``planned``: ``paid`` asserts money was received, so
    only a payment record may set it (``_sync_invoice_paid_status``). Create-time
    arithmetic must never derive it.
    """
    if not invoice_number or not invoice_number.strip():
        raise ValueError("invoice_number is required")
    invoice_number = invoice_number.strip()
    if not invoice_date or not str(invoice_date).strip():
        raise ServiceError("invoice_date is required")
    invoice_date = str(invoice_date).strip()
    if not lines:
        raise ServiceError("lines are required")
    # Duplicate check first so a repeated number still reports "already exists".
    dup = conn.execute(
        "SELECT id FROM invoices WHERE sale_id = %s AND invoice_number = %s",
        (sale_id, invoice_number)
    ).fetchone()
    if dup:
        raise ValueError(f"Invoice number '{invoice_number}' already exists for this sale")
    # Serialise invoice creation per sale BEFORE validating the lines. Without
    # this lock two concurrent creates could both pass the tolerance check
    # against the same pre-existing total and each write the full ceiling.
    if not conn.execute("SELECT id FROM sales WHERE id = %s FOR UPDATE",
                        (sale_id,)).fetchone():
        raise ValueError("sale not found")

    # Non-finite guard: the VALUE is intentionally unused now that no remainder
    # is billed, but the call must stay. A NaN/Inf REAL in a PI line (or a
    # legacy line_total) would otherwise poison Decimal arithmetic inside
    # validate/tolerance and surface as a generic 500 instead of this named 400.
    remaining_invoice_money(conn, sale_id)

    validated_lines = validate_invoice_lines(conn, sale_id, lines)
    # Phase 3-C: Enforce 5% over-invoice tolerance (aggregate with existing lines)
    check_invoice_tolerance(conn, sale_id, validated_lines)
    amount = _money(sum(l["line_total"] for l in validated_lines))
    # INV-REVISION: a create NEVER births 'paid'. 'paid' asserts money was
    # received, so only a payment record can set it (_sync_invoice_paid_status).
    # Deriving status from create-time arithmetic made a zero-amount invoice
    # a terminal, unfulfilled row reported as settled.
    status = "planned"

    try:
        cursor = conn.execute(
            """INSERT INTO invoices (sale_id, invoice_number, status, amount, created_at, invoice_date)
               VALUES (%s, %s, %s, ROUND(%s::numeric, 2), %s, %s) RETURNING id""",
            (sale_id, invoice_number, status, amount, _now_str(), invoice_date)
        )
        invoice_id = cursor.fetchone()[0]
    except psycopg.IntegrityError:
        raise ValueError(f"Invoice number '{invoice_number}' already exists for this sale")

    # Phase 3-B: Freeze invoice lines once booked/paid/shipped/void
    # (New invoices are 'planned' so this passes, but provides defense in depth)
    _assert_invoice_mutable(conn, invoice_id)

    # Insert validated lines
    for vl in validated_lines:
        row = conn.execute(
            """INSERT INTO invoice_items
                   (invoice_id, sale_item_id, product_name, unit,
                    quantity, unit_price, line_total, source_type)
               VALUES (%s, %s, %s, %s, %s, %s,
                       ROUND(%s::numeric * %s::numeric, 2), 'pi_line')
               RETURNING id, line_total""",
            (invoice_id, vl["sale_item_id"], vl["product_name"], vl["unit"],
             vl["quantity"], vl["unit_price"], vl["quantity"], vl["unit_price"])
        ).fetchone()
        line_id, line_total = row
        log_audit_action(conn, "INVOICE_ITEM_ADD", "invoice_item", line_id,
                         new_value=(f"explicit line for sale_item {vl['sale_item_id']} "
                                    f"qty={vl['quantity']} total={_money(line_total or 0)}"),
                         atomic=False)

    # Restate header from lines (single source of truth)
    amount = _money(conn.execute(
        """UPDATE invoices SET amount =
               (SELECT COALESCE(SUM(line_total), 0) FROM invoice_items
                 WHERE invoice_id = %s)
           WHERE id = %s RETURNING amount""",
        (invoice_id, invoice_id)).fetchone()[0])

    # Entering production: only set production_running when no status yet
    # (COALESCE so a sale already at production_done/ship_booked never downgrades)
    conn.execute(
        "UPDATE sales SET shipment_status = COALESCE(shipment_status, 'production_running'), updated_at = %s WHERE id = %s",
        (_now_str(), sale_id)
    )

    # atomic=False, commit below: the header, its lines, the shipment
    # status flip and the audit row are one transaction. This commit is also
    # what releases the `sales ... FOR UPDATE` lock taken above, so it must stay
    # AFTER the audit write — committing first would release the lock early and
    # leave a durable invoice nobody recorded.
    log_audit_action(conn, "INVOICE_CREATE", "invoice", invoice_id,
                     new_value=f"{invoice_number} amount={amount} status={status}",
                     atomic=False)
    conn.commit()
    return {"invoice_id": invoice_id, "invoice_number": invoice_number,
            "status": status, "amount": float(amount),
            "invoice_date": invoice_date}


def create_invoices_batch(conn: psycopg.Connection, sale_id: int,
                          invoices: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Create N invoices for one sale in ONE transaction.

    Every failure rolls the whole batch back (mirrors /api/sales/batch). Each
    invoice's ``lines`` explicitly allocate a quantity per remaining PI line,
    so a true partial shipment (some products, some quantities) is expressible
    without inventing money: the header is restated in SQL as
    ``SUM(invoice_items.line_total)`` exactly as ``create_invoice`` does, so
    ``invoices.amount`` is always the real line sum and never a typed figure.

    Guards: every invoice needs ≥1 line and its own ``invoice_date``; each line
    quantity finite and > 0; each ``sale_item_id`` belongs to this sale; Σ per
    sale item across the batch never exceeds what is still uninvoiced; no
    duplicate invoice number within the batch or against the sale. The caller
    owns no separate commit — this function commits once, atomically.
    """
    if not invoices:
        raise ValueError("at least one invoice required")
    if not conn.execute("SELECT id FROM sales WHERE id = %s FOR UPDATE",
                        (sale_id,)).fetchone():
        raise ValueError("sale not found")
    # Lock every PI line up front so concurrent batches cannot double-bill.
    conn.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id FOR UPDATE",
        (sale_id,)).fetchall()
    sale_item_ids = {r[0] for r in conn.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id", (sale_id,)).fetchall()}

    # Validate the whole batch BEFORE any write.
    batch_qty: Dict[int, Decimal] = {}
    for inv in invoices:
        num = (inv.get("invoice_number") or "").strip()
        if not num:
            raise ValueError("invoice_number is required")
        # INVOICE-CONTRACT: each entry states its own date (a batch may span
        # days); a missing one refuses the whole batch before any write.
        if not str(inv.get("invoice_date") or "").strip():
            raise ServiceError(f"Invoice '{num}' requires an invoice_date")
        lines = inv.get("lines") or []
        if not lines:
            raise ValueError(f"Invoice '{num}' must have at least one line")
        for ln in lines:
            sid_i = ln.get("sale_item_id")
            if sid_i not in sale_item_ids:
                raise ValueError(
                    f"sale item {sid_i} does not belong to this sale")
            try:
                qty = _to_decimal(ln.get("quantity"))
            except (TypeError, ValueError):
                raise ValueError("quantity must be a number")
            if not qty.is_finite() or qty <= 0:
                raise ValueError("quantity must be finite and > 0")
            batch_qty[sid_i] = batch_qty.get(sid_i, Decimal("0")) + qty
    # Phase 3-C: The 5% tolerance check (check_invoice_tolerance) replaces the old
    # "remaining" check. It accounts for existing invoiced quantities and allows
    # up to PI_qty * 1.05 total, instead of strictly PI_qty.
    seen_nums: set = set()
    for inv in invoices:
        num = inv["invoice_number"].strip()
        if num in seen_nums:
            raise ValueError(f"Invoice number '{num}' appears multiple times")
        seen_nums.add(num)
        if conn.execute(
            "SELECT id FROM invoices WHERE sale_id = %s AND invoice_number = %s",
            (sale_id, num)).fetchone():
            raise ValueError(
                f"Invoice number '{num}' already exists for this sale")

    # Phase 3-C: Enforce 5% over-invoice tolerance across the entire batch
    # Aggregate all lines from all invoices in the batch
    all_lines = []
    for inv in invoices:
        for ln in inv["lines"]:
            all_lines.append({"sale_item_id": ln["sale_item_id"], "quantity": float(ln["quantity"])})
    check_invoice_tolerance(conn, sale_id, all_lines)

    results = []
    for inv in invoices:
        num = inv["invoice_number"].strip()
        inv_date = str(inv.get("invoice_date") or "").strip()
        try:
            cur = conn.execute(
                """INSERT INTO invoices (sale_id, invoice_number, status, amount, created_at, invoice_date)
                   VALUES (%s, %s, 'planned', 0, %s, %s) RETURNING id""",
                (sale_id, num, _now_str(), inv_date))
            invoice_id = cur.fetchone()[0]
        except psycopg.IntegrityError:
            raise ValueError(
                f"Invoice number '{num}' already exists for this sale")
        amount = Decimal("0")
        # Phase 3-B: Freeze invoice lines once booked/paid/shipped/void
        # (New invoices are 'planned' so this passes, but provides defense in depth)
        _assert_invoice_mutable(conn, invoice_id)
        for ln in inv["lines"]:
            sid_i = ln["sale_item_id"]
            qty = _to_decimal(ln["quantity"])
            si = conn.execute(
                "SELECT product_name, unit, unit_price FROM sale_items WHERE id = %s",
                (sid_i,)).fetchone()
            if not si:
                raise ValueError(f"sale item {sid_i} not found")
            product_name, unit, unit_price = si
            row = conn.execute(
                """INSERT INTO invoice_items
                       (invoice_id, sale_item_id, product_name, unit,
                        quantity, unit_price, line_total)
                   VALUES (%s, %s, %s, %s, %s, %s,
                           ROUND(%s::numeric * %s::numeric, 2))
                   RETURNING id, line_total""",
                (invoice_id, sid_i, product_name, unit or "KG", qty, unit_price,
                 qty, unit_price)).fetchone()
            line_id, line_total = row
            amount += _money(line_total or 0)
            log_audit_action(conn, "INVOICE_ITEM_ADD", "invoice_item", line_id,
                             new_value=(f"invoice={invoice_id} item={sid_i}"
                                        f" qty={qty}"), atomic=False)
        conn.execute(
            "UPDATE invoices SET amount = (SELECT COALESCE(SUM(line_total), 0)"
            " FROM invoice_items WHERE invoice_id = %s) WHERE id = %s",
            (invoice_id, invoice_id))
        log_audit_action(conn, "INVOICE_CREATE", "invoice", invoice_id,
                         new_value=f"{num} amount={amount}", atomic=False)
        results.append({"invoice_id": invoice_id, "invoice_number": num,
                        "status": "planned", "amount": float(amount),
                        "invoice_date": inv_date})

    # Entering production: only set production_running when no status yet
    # (never downgrade an LC's later shipment_status).
    conn.execute(
        "UPDATE sales SET shipment_status = COALESCE(shipment_status, 'production_running'), updated_at = %s WHERE id = %s",
        (_now_str(), sale_id))
    conn.commit()
    return {"invoices": results}


# Forward-only invoice lifecycle. Every transition refuses no-op repeats and
# backward moves (route maps these to 409); pay-on-paid is the only exception
# and flows through mark_invoice_paid, never here.
_INVOICE_STATUS_ORDER = ["planned", "produced", "booked", "shipped", "paid"]


def update_invoice_status(conn: psycopg.Connection, invoice_id: int, status: str,
                          approx_ship_date: Optional[str] = None,
                          actual_ship_date: Optional[str] = None) -> bool:
    """Update invoice status and optional dates.

    Forward-only: ``status`` must be strictly later in the lifecycle than the
    current status. Repeats and backward moves from any state (including the
    terminal 'shipped'/'paid' states) raise ``ValueError("invoice already
    <current>")``. Returns False only when the invoice does not exist.
    """
    valid_statuses = ["planned", "produced", "booked", "shipped", "paid"]
    if status not in valid_statuses:
        raise ValueError(f"Invalid status: {status}. Must be one of {valid_statuses}")

    row = conn.execute(
        "SELECT status FROM invoices WHERE id = %s FOR UPDATE", (invoice_id,)
    ).fetchone()
    if not row:
        return False
    current = row[0]
    if _INVOICE_STATUS_ORDER.index(status) <= _INVOICE_STATUS_ORDER.index(current):
        raise InvoiceAlreadyAdvanced(f"invoice already {current}")

    fields = ["status = %s"]
    params = [status]
    if approx_ship_date is not None:
        fields.append("approx_ship_date = %s")
        params.append(approx_ship_date)
    if actual_ship_date is not None:
        fields.append("actual_ship_date = %s")
        params.append(actual_ship_date)
    
    params.append(invoice_id)
    cursor = conn.execute(
        f"UPDATE invoices SET {', '.join(fields)} WHERE id = %s",
        params
    )
    if cursor.rowcount == 0:
        return False
    conn.commit()
    return True


def book_invoice(conn: psycopg.Connection, invoice_id: int, approx_ship_date: str) -> bool:
    """Book an invoice with approximate ship date.

    Production is mandatory first: booking commits to a real shipment, so an
    invoice whose products have not all been produced from the recipe page is
    refused. Fail-closed — the check re-reads production content rather than
    trusting ``invoices.status``, which a partial run already flips to
    'produced'.
    """
    if not approx_ship_date or not approx_ship_date.strip():
        raise ValueError("approx_ship_date is required for booking")
    approx = approx_ship_date.strip()

    inv = conn.execute(
        "SELECT i.sale_id, s.company_id, i.status FROM invoices i "
        "JOIN sales s ON s.id = i.sale_id WHERE i.id = %s FOR UPDATE",
        (invoice_id,)).fetchone()
    if not inv:
        return False
    _, company_id, current = inv

    # Re-check content at both pre-booking states ('planned' and 'produced').
    # Checking only 'planned' would be the hole: record_production flips status
    # to 'produced' on the FIRST linked run, so a multi-product invoice would
    # sail through with later products never built.
    if _INVOICE_STATUS_ORDER.index(current) <= _INVOICE_STATUS_ORDER.index("produced"):
        ready = invoice_production_readiness(conn, company_id, invoice_id)
        if not ready["ok"]:
            raise InvoiceNotProduced(_production_blocked_message(
                ready["missing_recipes"] or ready["missing_production"]))

    if not update_invoice_status(conn, invoice_id, "booked", approx_ship_date=approx):
        return False
    log_audit_action(conn, "INVOICE_BOOK", "invoice", invoice_id,
                     new_value=f"Booked on {approx}")
    return True


def ship_invoice(conn: psycopg.Connection, invoice_id: int, actual_ship_date: str,
                 invoice_number: str, notes: Optional[str] = None,
                 created_by: Optional[int] = None) -> Dict[str, Any]:
    """Mark an invoice as shipped - creates shipment record and updates invoice.

    Forward-only: the invoice row is locked (``FOR UPDATE``) and its status
    checked BEFORE any shipment row is written, so a refused transition
    (terminal 'shipped'/'paid') never leaves a phantom shipment behind.
    """
    if not actual_ship_date or not actual_ship_date.strip():
        raise ValueError("actual_ship_date is required for shipping")
    actual_ship_date = actual_ship_date.strip()

    # Lock first; refuse terminal states before any mutation.
    invoice = conn.execute(
        "SELECT id, sale_id, invoice_number, status FROM invoices WHERE id = %s FOR UPDATE",
        (invoice_id,)
    ).fetchone()
    if not invoice:
        raise InvoiceNotFound("Invoice not found")
    _, sale_id, db_number, current = invoice
    if current in ("shipped", "paid"):
        conn.rollback()
        raise InvoiceAlreadyAdvanced(f"invoice already {current}")

    # Shipment insert is inlined (not via add_shipment, which COMMITs) so the
    # lock is held until the guarded status flip below commits atomically.
    ship_row = conn.execute(
        """INSERT INTO shipments (sale_id, ship_date, invoice_number, invoice_date, notes)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (sale_id, actual_ship_date, (invoice_number or "").strip() or db_number,
         None, "Shipped via production run")
    ).fetchone()
    shipment_id = ship_row[0]

    # Guarded flip: a concurrent transition to a terminal state wins the race.
    cur = conn.execute(
        "UPDATE invoices SET status = 'shipped', actual_ship_date = %s, "
        "notes = COALESCE(notes, '') || %s WHERE id = %s AND status NOT IN ('shipped', 'paid')",
        (actual_ship_date, f"\nShipped on {actual_ship_date}: " + (notes or ""), invoice_id)
    )
    if cur.rowcount == 0:
        conn.rollback()
        lost = conn.execute(
            "SELECT status FROM invoices WHERE id = %s", (invoice_id,)
        ).fetchone()
        raise InvoiceAlreadyAdvanced(f"invoice already {(lost[0] if lost else 'shipped')}")

    # atomic=False on both, single commit below: the shipment row, the guarded
    # status flip and their two audit rows are one transaction. The commit
    # here is also what releases the invoice lock the guarded flip depends on,
    # so it must stay after the audit writes.
    log_audit_action(conn, "SHIPMENT_RECORD", "sale", sale_id,
                     new_value=f"{actual_ship_date}", atomic=False)
    log_audit_action(conn, "INVOICE_SHIP", "invoice", invoice_id,
                     new_value=f"Shipped on {actual_ship_date}", atomic=False)
    conn.commit()
    return {"invoice_id": invoice_id, "shipment_id": shipment_id, "status": "shipped"}


def _invoice_paid_amount(conn: psycopg.Connection, invoice_id: int) -> float:
    """Sum of payments recorded against one invoice."""
    row = conn.execute(
        """SELECT COALESCE(ROUND(SUM(payment_amount)::numeric, 2)::float8, 0)
           FROM sale_payments WHERE invoice_id = %s""",
        (invoice_id,)
    ).fetchone()
    return float(row[0]) if row else 0.0


def _sync_invoice_paid_status(conn: psycopg.Connection, invoice_id: int) -> bool:
    """Flip an invoice to 'paid' once payments cover its amount.

    Rules (never un-pays — the UPDATE only ever writes 'paid'):
      * amount IS NOT NULL -> paid when paid_amount >= amount
        (amount 0 is already 'paid' at creation);
      * amount IS NULL (legacy row, amount unknown) -> old behaviour:
        any payment carrying this invoice_id marks it 'paid'.
    Returns True when the status changed to 'paid'.
    """
    row = conn.execute(
        "SELECT amount, status FROM invoices WHERE id = %s", (invoice_id,)
    ).fetchone()
    if not row or row[1] == "paid":
        return False
    amount = row[0]
    paid = _invoice_paid_amount(conn, invoice_id)
    covered = (paid > 0) if amount is None else (paid >= float(amount))
    if not covered:
        return False
    cur = conn.execute(
        "UPDATE invoices SET status = 'paid' WHERE id = %s AND status <> 'paid'",
        (invoice_id,)
    )
    return cur.rowcount > 0


def _invoice_unpaid_status(conn: psycopg.Connection, invoice_id: int) -> str:
    """Furthest non-paid lifecycle state provable from the invoice row.

    ``paid`` does not record the state it replaced, so an invoice whose
    coverage disappeared is returned to the deepest state still evidenced by
    its own dates: shipped (actual_ship_date) -> booked (approx_ship_date)
    -> planned. 'produced' is not derivable from the invoice row alone.
    """
    row = conn.execute(
        "SELECT actual_ship_date, approx_ship_date FROM invoices WHERE id = %s",
        (invoice_id,)
    ).fetchone()
    if row:
        if row[0]:
            return "shipped"
        if row[1]:
            return "booked"
    return "planned"


def _resync_invoice_payment_status(conn: psycopg.Connection,
                                   invoice_id: Optional[int]) -> bool:
    """Re-derive an invoice's paid status from its payments, BOTH directions.

    Single entry point for every payment write path (insert / edit / delete):
    a payment that no longer covers the invoice must not leave it stuck at
    'paid' with paid_amount 0 — a permanent silent financial misstatement.

    * covered  -> ``_sync_invoice_paid_status`` (forward-only flip to 'paid').
    * not covered and status == 'paid' -> demote to ``_invoice_unpaid_status``
      (never lower than what the invoice's own dates prove).
    * otherwise -> no write (a partial payment leaves the status untouched).
    """
    if invoice_id is None:
        return False
    row = conn.execute(
        "SELECT amount, status FROM invoices WHERE id = %s", (invoice_id,)
    ).fetchone()
    if not row:
        return False
    amount, status = row[0], row[1]
    paid = _invoice_paid_amount(conn, invoice_id)
    # Same coverage rule as _sync_invoice_paid_status: legacy NULL amounts
    # (unknown) are paid by ANY payment; amount 0 is paid at creation.
    covered = (paid > 0) if amount is None else (paid >= float(amount))
    if covered:
        return _sync_invoice_paid_status(conn, invoice_id)
    if status != "paid":
        return False
    restored = _invoice_unpaid_status(conn, invoice_id)
    cur = conn.execute(
        "UPDATE invoices SET status = %s WHERE id = %s AND status = 'paid'",
        (restored, invoice_id)
    )
    if cur.rowcount > 0:
        stated = "NULL" if amount is None else f"{float(amount):g}"
        log_audit_action(conn, "INVOICE_PAYMENT_REVERSED", "invoice", invoice_id,
                         old_value="paid",
                         new_value=f"status={restored} paid_to_date={paid:g} "
                                   f"amount={stated}")
    return cur.rowcount > 0


def mark_invoice_paid(conn: psycopg.Connection, invoice_id: int,
                      payment_amount: float, payment_date: str,
                      notes: Optional[str] = None, created_by: Optional[int] = None) -> Dict[str, Any]:
    """Record a payment for an invoice (partial or full).

    The invoice row is locked (``FOR UPDATE``) before the payment INSERT so
    concurrent pays serialize inside one transaction; coverage is computed
    after the insert and the response ``status`` is the ACTUAL post-sync
    invoice status (partial payments keep e.g. 'planned', they do NOT report
    'paid'). Overpayments are accepted without error: the full payment row is
    recorded and the invoice flips to 'paid' (money received is truth; no
    amount cap here by design).
    """
    # Phase 2-B. Before the SELECT ... FOR UPDATE, so a rejection takes no lock.
    _finite(payment_amount)
    if payment_amount is None or payment_amount <= 0:
        raise ValueError("payment_amount must be positive")

    invoice = conn.execute(
        "SELECT sale_id FROM invoices WHERE id = %s FOR UPDATE", (invoice_id,)
    ).fetchone()
    if not invoice:
        raise InvoiceNotFound("Invoice not found")
    sale_id = invoice[0]

    cursor = conn.execute(
        """INSERT INTO sale_payments (sale_id, payment_date, payment_amount, notes, invoice_id)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (sale_id, payment_date, payment_amount, notes, invoice_id)
    )
    payment_id = cursor.fetchone()[0]
    _sync_sale_payment_totals(conn, sale_id)
    _resync_invoice_payment_status(conn, invoice_id)
    actual_status = conn.execute(
        "SELECT status FROM invoices WHERE id = %s", (invoice_id,)
    ).fetchone()[0]
    log_audit_action(conn, "INVOICE_PAYMENT", "invoice", invoice_id,
                     new_value=f"amount={payment_amount:g} date={payment_date}")
    conn.commit()
    return {"payment_id": payment_id, "status": actual_status}


def void_invoice(conn: psycopg.Connection, sale_id: int, invoice_id: int) -> bool:
    """Void (delete) an invoice belonging to a sale.

    Returns False when the invoice does not exist or belongs to a different
    sale (route maps to 404). Paid or shipped invoices CAN be voided — this
    is the remediation path for bad transitions. Payments detach via the
    existing ``ON DELETE SET NULL`` FK and stay counted in the sale-level
    totals (payments remain sale-level truth), so sale payment totals are
    intentionally left unchanged.
    """
    row = conn.execute(
        "SELECT sale_id FROM invoices WHERE id = %s", (invoice_id,)
    ).fetchone()
    if not row or row[0] != sale_id:
        return False
    conn.execute("DELETE FROM invoices WHERE id = %s", (invoice_id,))
    # atomic=False, commit below: the DELETE and its audit row are one unit.
    # A voided invoice's money is gone from the report, so an unaudited void is
    # a silent discrepancy between the ledger and the books.
    log_audit_action(conn, "INVOICE_VOID", "invoice", invoice_id,
                     new_value=f"voided from sale {sale_id}", atomic=False)
    conn.commit()
    return True


def _production_blocked_message(missing: List[str]) -> str:
    """Single source for the "not produced yet" wording.

    Shared by ``book_invoice``'s 409 body and the ``book_blocked_reason`` the
    list payload carries, so the button's hint and the server's refusal can
    never drift apart.
    """
    return ("invoice not produced yet — run production from the recipe page "
            f"for: {', '.join(missing)}")


# Statuses from which booking is still a legal next step. Anything past these
# is closed: not bookable, and no production check is worth running.
_BOOKABLE_STATUSES = ("planned", "produced")


def list_invoices(conn: psycopg.Connection, sale_id: int,
                  with_readiness: bool = False) -> List[Dict[str, Any]]:
    """List all invoices for a sale, with amount + paid-to-date per invoice.

    ``with_readiness`` adds ``can_book`` + ``book_blocked_reason`` so the
    invoice panel can disable its Book action from server truth instead of
    inferring it from ``status``.

    Off by default on purpose: ``get_sale_by_id`` (the whole PI detail modal)
    and ``get_sale_completion`` both embed this list but read none of these
    flags, and readiness costs extra queries per open invoice. Only
    ``GET /api/sales/<id>/invoices`` asks for them.

    ``can_book`` mirrors ``book_invoice`` exactly — open status AND every
    product produced — so the panel and the gate cannot disagree.
    ``book_blocked_reason`` is ``null`` when bookable, and for closed invoices
    too (a booked invoice is not "blocked", it is simply past booking);
    ``can_book`` carries that distinction on its own.
    """
    rows = [
        {"invoice_id": r[0], "invoice_number": r[1], "status": r[2],
         "approx_ship_date": r[3], "actual_ship_date": r[4], "notes": r[5],
         "invoice_date": r[6], "created_at": r[7],
         "amount": (float(r[8]) if r[8] is not None else None),
         "paid_amount": float(r[9])}
        for r in conn.execute(
            """SELECT id, invoice_number, status, approx_ship_date, actual_ship_date,
                      notes, invoice_date, created_at, amount,
                      (SELECT COALESCE(ROUND(SUM(sp.payment_amount)::numeric, 2)::float8, 0)
                       FROM sale_payments sp WHERE sp.invoice_id = invoices.id) AS paid_amount
               FROM invoices WHERE sale_id = %s ORDER BY id""",
            (sale_id,)
        ).fetchall()
    ]
    if not with_readiness:
        return rows

    company_row = conn.execute(
        "SELECT company_id FROM sales WHERE id = %s", (sale_id,)).fetchone()
    company_id = company_row[0] if company_row else None
    for row in rows:
        if row["status"] not in _BOOKABLE_STATUSES:
            row["can_book"] = False
            row["book_blocked_reason"] = None
            continue
        ready = invoice_production_readiness(conn, company_id, row["invoice_id"])
        row["can_book"] = bool(ready["ok"])
        row["book_blocked_reason"] = (
            None if ready["ok"] else _production_blocked_message(
                ready["missing_recipes"] or ready["missing_production"])
        )
    return rows


def get_sale_completion(conn: psycopg.Connection, sale_id: int) -> Dict[str, Any]:
    """Get sale completion status based on invoices.

    Adds ``total_amount`` (sum of non-NULL invoice amounts) and
    ``paid_amount`` (sum of per-invoice paid amounts) alongside the legacy
    status/paid/total/invoices keys.
    """
    invoices = list_invoices(conn, sale_id)
    if not invoices:
        return {"status": "none", "paid": 0, "total": 0, "invoices": []}
    
    paid = sum(1 for inv in invoices if inv["status"] == "paid")
    total = len(invoices)
    total_amount = round(sum(float(inv["amount"]) for inv in invoices
                             if inv["amount"] is not None), 2)
    paid_amount = round(sum(inv["paid_amount"] for inv in invoices), 2)
    
    if paid == total:
        status = "full"
    elif paid > 0:
        status = "partial"
    else:
        status = "none"
    
    return {"status": status, "paid": paid, "total": total,
            "invoices": invoices, "total_amount": total_amount,
            "paid_amount": paid_amount}


def relink_sale_company(conn: psycopg.Connection, sale_id: int,
                        new_company_id: int) -> bool:
    """Clear ``sales.lc_id`` when its LC belongs to a different company.

    Finding-5 invariant: LCs are scoped by ``(company_id, lc_number)``, so a
    sale that moves companies must not keep pointing at the old company's
    LC. Clears ``lc_id`` + the legacy ``lc_number``/``lc_date`` mirrors when
    the linked LC's company differs from ``new_company_id`` (or the LC row
    is gone). No-op when unlinked or still matching.

    Commit-free: the caller owns the transaction (``update_sale_full`` calls
    this before its commit; standalone callers must commit themselves).
    Returns False when the sale does not exist, True otherwise.
    """
    sale = conn.execute(
        "SELECT lc_id FROM sales WHERE id = %s", (sale_id,)).fetchone()
    if not sale:
        return False
    lc_id = sale[0]
    if lc_id is None:
        return True
    lc = conn.execute(
        "SELECT company_id FROM letters_of_credit WHERE id = %s",
        (lc_id,)).fetchone()
    if lc is None or lc[0] != new_company_id:
        conn.execute(
            "UPDATE sales SET lc_id = NULL, lc_number = NULL, lc_date = NULL"
            " WHERE id = %s",
            (sale_id,))
        log_audit_action(conn, "LC_DETACH", "lc", lc_id,
                         old_value=f"sale={sale_id}",
                         new_value="company changed", atomic=False)
    return True


def create_invoice_item(conn: psycopg.Connection, invoice_id: int,
                        sale_item_id: int, quantity: float) -> Dict[str, Any]:
    """Add one invoice line. The unit price is resolved server-side from the
    sale item — the client can never set it. ``quantity`` must be finite and
    > 0. Quantities may exceed the PI line quantity (shipment advantage).
    Audits ``INVOICE_ITEM_ADD``.
    """
    from decimal import Decimal as _Dec
    try:
        qty = float(quantity)
    except (TypeError, ValueError):
        raise ValueError("quantity must be a number")
    if not math.isfinite(qty) or qty <= 0:
        raise ValueError("quantity must be finite and > 0")
    try:
        inv = conn.execute(
            "SELECT id, sale_id FROM invoices WHERE id = %s FOR UPDATE",
            (invoice_id,)).fetchone()
        if not inv:
            conn.rollback()
            raise InvoiceNotFound("invoice not found")
        sale_id = inv[1]
        # Phase 3-B: Freeze invoice lines once booked/paid/shipped/void
        _assert_invoice_mutable(conn, invoice_id)
        si = conn.execute(
            "SELECT id, sale_id, product_name, unit, quantity, unit_price"
            " FROM sale_items WHERE id = %s FOR UPDATE",
            (sale_item_id,)).fetchone()
        if not si:
            conn.rollback()
            raise SaleItemNotFound("sale item not found")
        if si[1] != sale_id:
            conn.rollback()
            raise ValueError("sale item does not belong to this invoice's sale")
        # Phase 3-C: Enforce 5% over-invoice tolerance (aggregate with existing lines)
        check_invoice_tolerance(conn, sale_id, [{"sale_item_id": sale_item_id, "quantity": qty}])
        # Unit price inherited from PI line (server-enforced)
        unit_price = float(si[5] or 0)
        line_total = round(qty * unit_price, 2)
        try:
            row = conn.execute(
                """INSERT INTO invoice_items (invoice_id, sale_item_id,
                       product_name, unit, quantity, unit_price, line_total, source_type)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'pi_line') RETURNING id""",
                (invoice_id, sale_item_id, si[2], si[3] or "KG",
                 qty, unit_price, line_total),
            ).fetchone()
        except psycopg.errors.IntegrityError:
            try:
                conn.rollback()
            except Exception:
                pass
            raise InvoiceNotFound("invoice not found")
        line_id = row[0]
        # Contract: invoices.amount is a DERIVED cache of
        # SUM(invoice_items.line_total). Recomputed on every write in the
        # same txn (no line-delete endpoint exists yet, so recompute-on-write
        # covers all current writers).
        conn.execute(
            "UPDATE invoices SET amount = (SELECT COALESCE(SUM(line_total), 0)"
            " FROM invoice_items WHERE invoice_id = %s) WHERE id = %s",
            (invoice_id, invoice_id))
        log_audit_action(conn, "INVOICE_ITEM_ADD", "invoice_item", line_id,
                         new_value=f"invoice={invoice_id} item={sale_item_id}"
                                   f" qty={qty:g}",
                         atomic=False)
        conn.commit()
        return {"id": line_id, "invoice_id": invoice_id,
                "sale_item_id": sale_item_id, "product_name": si[2],
                "unit": si[3] or "KG", "quantity": qty,
                "unit_price": unit_price, "line_total": line_total}
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    return {"id": line_id, "invoice_id": invoice_id,
            "sale_item_id": sale_item_id, "product_name": si[2],
            "unit": si[3] or "KG", "quantity": qty,
            "unit_price": unit_price, "line_total": line_total}


def list_invoice_items(conn: psycopg.Connection,
                       invoice_id: int) -> List[Dict[str, Any]]:
    """Invoice lines for one invoice, oldest first."""
    return [
        {"id": r[0], "invoice_id": r[1], "sale_item_id": r[2],
         "product_name": r[3], "unit": r[4], "quantity": float(r[5]),
         "unit_price": float(r[6]),
         "line_total": float(r[7]) if r[7] is not None else 0.0}
        for r in conn.execute(
            "SELECT id, invoice_id, sale_item_id, product_name, unit,"
            " quantity, unit_price, line_total FROM invoice_items"
            " WHERE invoice_id = %s ORDER BY id",
            (invoice_id,)).fetchall()
    ]


def list_sale_invoice_lines(conn: psycopg.Connection,
                            sale_id: int) -> Dict[str, Any]:
    """PI lines of a sale with what has been invoiced against each.

    One row per ``sale_items`` line, aggregated across **every** invoice of the
    sale, exactly as ``check_invoice_tolerance`` counts them (no status filter),
    so the budget the UI shows and the refusal the writer raises cannot
    disagree.

    ``remaining`` is quantity only (``PI - invoiced``), floored at zero for
    display. A fully invoiced line is still returned: it does not disappear from
    the composer just because it has no budget left.

    ``ceiling`` is ``PI * OVER_INVOICE_TOLERANCE`` at 6 dp, computed with the
    same ``Decimal(str(...))`` expression as ``check_invoice_tolerance`` so a
    quantity the UI offers is exactly a quantity the writer accepts.

    ``has_line_less_invoices`` warns that a legacy invoice carries header money
    that no line backs. Its quantity contributes 0 here, so ``remaining`` would
    overstate the budget; callers must not treat it as authoritative in that
    case.
    """
    rows = conn.execute(
        """SELECT si.id, si.product_name, si.unit, si.quantity, si.unit_price,
                  COALESCE(SUM(ii.quantity)::numeric, 0) AS invoiced,
                  COALESCE(array_agg(DISTINCT ii.invoice_id)
                           FILTER (WHERE ii.invoice_id IS NOT NULL), '{}')
               FROM sale_items si
               LEFT JOIN invoice_items ii ON ii.sale_item_id = si.id
               LEFT JOIN invoices i ON i.id = ii.invoice_id
               WHERE si.sale_id = %s
               GROUP BY si.id, si.product_name, si.unit, si.quantity, si.unit_price
               ORDER BY si.id""",
        (sale_id,)
    ).fetchall()

    lines: List[Dict[str, Any]] = []
    for item_id, product_name, unit, pi_qty, unit_price, invoiced, invoice_ids in rows:
        pi = Decimal(str(pi_qty or 0))
        done = Decimal(str(invoiced or 0))
        ceiling = (pi * OVER_INVOICE_TOLERANCE).quantize(Decimal("0.000001"))
        lines.append({
            "sale_id": sale_id,
            "sale_item_id": item_id,
            "product_name": product_name,
            "unit": unit or "KG",
            "pi_quantity": float(pi),
            "invoiced_quantity": float(done),
            "remaining": float(max(pi - done, Decimal("0"))),
            "ceiling": float(ceiling),
            "unit_price": float(unit_price or 0),
            "invoice_ids": sorted(invoice_ids),
        })

    legacy = conn.execute(
        """SELECT EXISTS (
               SELECT 1 FROM invoices i
               WHERE i.sale_id = %s
                 AND NOT EXISTS (SELECT 1 FROM invoice_items ii
                                 WHERE ii.invoice_id = i.id))""",
        (sale_id,)).fetchone()[0]
    return {"lines": lines, "has_line_less_invoices": bool(legacy)}


def invoiced_total_for_sale(conn: psycopg.Connection, sale_id: int) -> float:
    """SUM of an entire sale's invoice lines (``line_total``), 0.0 when none."""
    row = conn.execute(
        """SELECT COALESCE(SUM(ii.line_total)::numeric, 0)::float8
           FROM invoice_items ii JOIN invoices i ON i.id = ii.invoice_id
           WHERE i.sale_id = %s""",
        (sale_id,)).fetchone()
    return float(row[0]) if row else 0.0


def invoice_readiness(conn: psycopg.Connection,
                      sale_ids: List[int]) -> Dict[str, Any]:
    """Gate core for ``lc_received -> shipment_ongoing``.

    Requires at least one invoice (status != 'void'). ``sale_count`` and ``invoice_count`` are kept
    identical to the previous `_lc_shipment_readiness` shape so the route and
    readiness endpoints can keep using ``invoices_ok``/``detail`` unchanged.
    """
    invoice_count = 0
    if sale_ids:
        invoice_count = conn.execute(
            "SELECT COUNT(*) FROM invoices WHERE sale_id = ANY(%s) AND status != 'void'",
            (sale_ids,)).fetchone()[0]
    return {
        "invoices_ok": invoice_count >= 1,
        "detail": {
            "sale_count": len(sale_ids),
            "invoice_count": invoice_count,
        },
    }


def production_readiness(conn: psycopg.Connection, company_id: int,
                         sale_ids: List[int]) -> Dict[str, Any]:
    """Gate core for ``shipment_ongoing -> payment_due``.

    Barrier 2: every distinct product covered by an invoice of this LC must have
    a recipe for the company, and a production run for that recipe linked to an
    invoice belonging to the same LC. Strict match — no legacy fallback.
    """
    products: List[str] = []
    if sale_ids:
        products = [
            r[0] for r in conn.execute(
                """SELECT DISTINCT ii.product_name
                     FROM invoice_items ii
                     JOIN invoices i ON i.id = ii.invoice_id
                    WHERE i.sale_id = ANY(%s)""",
                (sale_ids,)).fetchall()
            if r[0]
        ]
    missing_recipes: List[str] = []
    missing_production: List[str] = []
    for product in sorted(products):
        recipe = conn.execute(
            """SELECT id FROM recipes
                WHERE company_id = %s
                  AND lower(product_name) = lower(%s)""",
            (company_id, product)).fetchone()
        if not recipe:
            missing_recipes.append(product)
            # A product with no recipe cannot have been produced for that
            # recipe either, so produced_ok must also be False for it.
            missing_production.append(product)
            continue
        run_rows = conn.execute(
            """SELECT pr.id
                 FROM production_runs pr
                 JOIN production_run_links prl ON prl.run_id = pr.id
                 JOIN invoices i ON i.id = prl.invoice_id
                WHERE pr.recipe_id = %s
                  AND i.sale_id = ANY(%s)""",
            (recipe[0], sale_ids)).fetchall()
        if not run_rows:
            missing_production.append(product)
    return {
        "recipes_ok": not missing_recipes,
        "produced_ok": not missing_production,
        "detail": {
            "sale_count": len(sale_ids),
            "products": sorted(products),
            "missing_recipes": sorted(missing_recipes),
            "missing_production": sorted(missing_production),
        },
    }


def invoice_production_readiness(conn: psycopg.Connection, company_id: int,
                                 invoice_id: int) -> Dict[str, Any]:
    """Gate core for booking ONE invoice: has it actually been produced?

    Strict, fail-closed, and deliberately scoped to this invoice alone rather
    than to its sale. Invoices on one sale proceed independently — booking a
    ship for one must not wait on, or be unlocked by, a sibling's production.

    Requires, for every distinct product on THIS invoice's own lines: a recipe
    for the company, and a production run for that recipe linked to THIS
    invoice. ``invoices.status`` is not consulted — ``record_production`` marks
    an invoice 'produced' on the FIRST linked run, so on a multi-product invoice
    that flag reads satisfied while later products are still unbuilt. Booking
    commits to a real shipment, so it re-checks the content instead.

    Returns ``{"ok": bool, "missing_recipes": [...], "missing_production": [...]}``.
    """
    products = [
        r[0] for r in conn.execute(
            """SELECT DISTINCT ii.product_name
                 FROM invoice_items ii
                WHERE ii.invoice_id = %s AND ii.product_name IS NOT NULL""",
            (invoice_id,)).fetchall()
    ]
    missing_recipes: List[str] = []
    missing_production: List[str] = []
    for product in sorted(products):
        recipe = conn.execute(
            """SELECT id FROM recipes
                WHERE company_id = %s
                  AND lower(product_name) = lower(%s)""",
            (company_id, product)).fetchone()
        if not recipe:
            missing_recipes.append(product)
            # No recipe means it cannot have been produced for one either.
            missing_production.append(product)
            continue
        run = conn.execute(
            """SELECT pr.id
                 FROM production_runs pr
                 JOIN production_run_links prl ON prl.run_id = pr.id
                WHERE pr.recipe_id = %s
                  AND prl.invoice_id = %s
                LIMIT 1""",
            (recipe[0], invoice_id)).fetchone()
        if not run:
            missing_production.append(product)
    return {
        "ok": not missing_recipes and not missing_production,
        "products": sorted(products),
        "missing_recipes": sorted(missing_recipes),
        "missing_production": sorted(missing_production),
    }


def check_lc_shipment_ready(conn: psycopg.Connection,
                            lc_id: int) -> Dict[str, Any]:
    """Gate for ``lc_received -> shipment_ongoing``.

    Returns both barriers from one snapshot so the route can report the full
    picture in a single 409 payload, while the move primitive only enforces the
    barrier(s) actually crossed by the target stage.
    """
    lc = conn.execute(
        "SELECT id, company_id FROM letters_of_credit WHERE id = %s",
        (lc_id,)).fetchone()
    if not lc:
        raise LookupError(f"LC {lc_id} not found")
    company_id = lc[1]
    sale_rows = conn.execute(
        "SELECT id FROM sales WHERE lc_id = %s", (lc_id,)).fetchall()
    sale_ids = [r[0] for r in sale_rows]
    invoice = invoice_readiness(conn, sale_ids)
    production = production_readiness(conn, company_id, sale_ids)
    return {
        "recipes_ok": production["recipes_ok"],
        "invoices_ok": invoice["invoices_ok"],
        "produced_ok": production["produced_ok"],
        "detail": {
            "sale_count": invoice["detail"]["sale_count"],
            "invoice_count": invoice["detail"]["invoice_count"],
            "products": production["detail"]["products"],
            "missing_recipes": production["detail"]["missing_recipes"],
            "missing_production": production["detail"]["missing_production"],
        },
    }
