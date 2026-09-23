"""Sales pipeline, items, payments, and summaries."""

import psycopg
import json
import os
import re
from datetime import date, datetime
from typing import Optional, List, Dict, Any, Union

from .audit import log_audit_action
from .notifications import notify_sale_stage


def _now_str() -> str:
    """Current UTC time as 'YYYY-MM-DD HH:MM:SS' for TEXT datetime columns."""
    from datetime import datetime as _dt
    return _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S")

SALE_STAGE_ORDER = ["pi_issued", "lc_received", "shipment_ongoing", "payment_due", "completed"]


def _next_stage(current: str) -> Optional[str]:
    """Return the next stage in the pipeline, or None if at end."""
    try:
        idx = SALE_STAGE_ORDER.index(current)
        return SALE_STAGE_ORDER[idx + 1] if idx + 1 < len(SALE_STAGE_ORDER) else None
    except ValueError:
        return None


def add_sale(conn: psycopg.Connection, sale_data: Dict[str, Any],
             items: List[Dict[str, Any]], initial_stage: str = "pi_issued") -> int:
    """Insert a new sale with line items. Returns the new sale ID."""
    cursor = conn.execute(
        """INSERT INTO sales (stage, pi_number, pi_date, client_name, pi_file_path)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (initial_stage, sale_data.get("pi_number"), sale_data.get("pi_date"),
         sale_data.get("client_name"), sale_data.get("pi_file_path"))
    )
    sale_id = cursor.fetchone()[0]
    for item in items:
        conn.execute(
            """INSERT INTO sale_items (sale_id, product_name, quantity, unit_price)
               VALUES (%s, %s, %s, %s)""",
            (sale_id, item["product_name"], item.get("quantity", 0), item.get("unit_price", 0))
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
                  search: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return all sales (optionally filtered by stage and/or search text).

    Search matches PI number, client name, LC number, and product names.
    """
    query = """
        SELECT s.id, s.stage, s.pi_number, s.pi_date, s.client_name, s.pi_file_path,
               s.lc_number, s.lc_date, s.shipment_date, s.payment_date, s.payment_amount,
               s.created_at, s.updated_at,
                COALESCE(ROUND(SUM(si.quantity * si.unit_price)::numeric, 2)::float8, 0) AS total_value,
                COUNT(si.id) AS item_count,
                COALESCE((SELECT ROUND(SUM(sp.payment_amount)::numeric, 2)::float8 FROM sale_payments sp
                          WHERE sp.sale_id = s.id), 0) AS total_paid
        FROM sales s
        LEFT JOIN sale_items si ON si.sale_id = s.id
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
        query += " WHERE " + " AND ".join(clauses)
    query += " GROUP BY s.id ORDER BY s.created_at DESC, s.id DESC"
    return [
        {"id": r[0], "stage": r[1], "pi_number": r[2], "pi_date": r[3],
         "client_name": r[4], "pi_file_path": r[5], "lc_number": r[6],
         "lc_date": r[7], "shipment_date": r[8], "payment_date": r[9],
         "payment_amount": r[10], "created_at": r[11], "updated_at": r[12],
         "total_value": r[13], "item_count": r[14], "total_paid": r[15],
         "balance": r[13] - r[15]}
        for r in conn.execute(query, params).fetchall()
    ]


def get_sale_by_id(conn: psycopg.Connection, sale_id: int) -> Optional[Dict[str, Any]]:
    """Return a single sale with its items and stage history."""
    row = conn.execute("SELECT * FROM sales WHERE id = %s", (sale_id,)).fetchone()
    if not row:
        return None
    sale = {
        "id": row[0], "stage": row[1], "pi_number": row[2], "pi_date": row[3],
        "client_name": row[4], "pi_file_path": row[5], "lc_number": row[6],
        "lc_date": row[7], "shipment_date": row[8], "payment_date": row[9],
        "payment_amount": row[10], "created_at": row[11], "updated_at": row[12],
    }
    sale["items"] = [
        {"id": r[0], "sale_id": r[1], "product_name": r[2],
         "quantity": r[3], "unit_price": r[4]}
        for r in conn.execute(
            "SELECT * FROM sale_items WHERE sale_id = %s ORDER BY id", (sale_id,)
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
    sale["invoice_total"] = sum(
        (i["quantity"] or 0) * (i["unit_price"] or 0) for i in sale["items"]
    )
    sale["total_paid"] = sum(p["payment_amount"] or 0 for p in sale["payments"])
    sale["balance"] = sale["invoice_total"] - sale["total_paid"]
    return sale


def move_sale_to_stage(conn: psycopg.Connection, sale_id: int, new_stage: str,
                       notes: Optional[str] = None) -> bool:
    """Move a sale to the next (or specified) stage. Logs the transition."""
    row = conn.execute("SELECT stage, client_name FROM sales WHERE id = %s", (sale_id,)).fetchone()
    if not row:
        return False
    current, client_name = row[0], row[1] or f"#{sale_id}"
    if new_stage not in SALE_STAGE_ORDER:
        return False
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
                 notes: Optional[str] = None) -> Optional[str]:
    """Advance a sale to the next pipeline stage. Returns the new stage or None."""
    row = conn.execute("SELECT stage FROM sales WHERE id = %s", (sale_id,)).fetchone()
    if not row:
        return None
    nxt = _next_stage(row[0])
    if nxt and move_sale_to_stage(conn, sale_id, nxt, notes):
        return nxt
    return None


def update_sale_lc(conn: psycopg.Connection, sale_id: int, lc_number: str,
                   lc_date: str, shipment_date: str) -> bool:
    """Enter LC details for a sale."""
    conn.execute(
        """UPDATE sales
           SET lc_number = %s, lc_date = %s, shipment_date = %s, updated_at = %s
           WHERE id = %s""",
        (lc_number, lc_date, shipment_date, _now_str(), sale_id)
    )
    log_audit_action(conn, "SALE_LC", "sale", sale_id, new_value=lc_number)
    conn.commit()
    return True


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
                        payment_amount: float, notes: Optional[str] = None) -> Dict[str, Any]:
    """Append a (possibly partial) payment. Auto-completes from payment_due
    when total paid reaches the invoice total. Returns totals + stage."""
    if payment_amount is None or payment_amount <= 0:
        raise ValueError("payment_amount must be positive")
    cursor = conn.execute(
        """INSERT INTO sale_payments (sale_id, payment_date, payment_amount, notes)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (sale_id, payment_date, payment_amount, notes)
    )
    payment_id = cursor.fetchone()[0]
    _sync_sale_payment_totals(conn, sale_id)
    log_audit_action(conn, "SALE_PAYMENT", "sale", sale_id,
                     new_value=str(payment_amount))
    total_paid = get_sale_total_paid(conn, sale_id)
    invoice_total = get_sale_invoice_total(conn, sale_id)
    row = conn.execute("SELECT stage FROM sales WHERE id = %s", (sale_id,)).fetchone()
    stage = row[0] if row else None
    if _complete_if_paid(conn, sale_id, stage, total_paid, invoice_total):
        stage = "completed"
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


def update_sale_payment_record(conn: psycopg.Connection, payment_id: int,
                               payment_date: Optional[str] = None,
                               payment_amount: Optional[float] = None,
                               notes: Optional[str] = None) -> bool:
    """Edit a payment record. Reverts a completed sale to payment_due
    if the new total no longer covers the invoice."""
    row = conn.execute("SELECT sale_id FROM sale_payments WHERE id = %s",
                       (payment_id,)).fetchone()
    if not row:
        return False
    sale_id = row[0]
    fields, vals = [], []
    if payment_date is not None:
        fields.append("payment_date = %s")
        vals.append(payment_date)
    if payment_amount is not None:
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
    log_audit_action(conn, "SALE_PAYMENT_EDIT", "sale", sale_id,
                     new_value=str(payment_id))
    row = conn.execute("SELECT stage FROM sales WHERE id = %s", (sale_id,)).fetchone()
    stage = row[0] if row else None
    _complete_if_paid(conn, sale_id, stage,
                      get_sale_total_paid(conn, sale_id),
                      get_sale_invoice_total(conn, sale_id))
    _revert_if_unpaid(conn, sale_id)
    conn.commit()
    return True


def delete_sale_payment_record(conn: psycopg.Connection, payment_id: int) -> bool:
    """Delete a payment record and re-sync totals."""
    row = conn.execute("SELECT sale_id, payment_amount FROM sale_payments WHERE id = %s",
                       (payment_id,)).fetchone()
    if not row:
        return False
    sale_id = row[0]
    conn.execute("DELETE FROM sale_payments WHERE id = %s", (payment_id,))
    _sync_sale_payment_totals(conn, sale_id)
    log_audit_action(conn, "SALE_PAYMENT_DELETE", "sale", sale_id,
                     old_value=str(row[1]))
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
                  quantity: float, unit_price: float) -> int:
    """Add a line item to an existing sale. Returns the new item ID."""
    cursor = conn.execute(
        """INSERT INTO sale_items (sale_id, product_name, quantity, unit_price)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (sale_id, product_name, quantity, unit_price)
    )
    conn.commit()
    return cursor.fetchone()[0]


def update_sale_item(conn: psycopg.Connection, item_id: int,
                     product_name: Optional[str] = None,
                     quantity: Optional[float] = None,
                     unit_price: Optional[float] = None) -> bool:
    """Update fields of a sale line item."""
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
    if not fields:
        return False
    vals.append(item_id)
    conn.execute(f"UPDATE sale_items SET {', '.join(fields)} WHERE id = %s", vals)
    conn.commit()
    return True


def delete_sale_item(conn: psycopg.Connection, item_id: int) -> bool:
    """Delete a single line item from a sale."""
    conn.execute("DELETE FROM sale_items WHERE id = %s", (item_id,))
    conn.commit()
    return True


def update_sale_full(conn: psycopg.Connection, sale_id: int, header: Dict[str, Any],
                     items: List[Dict[str, Any]], removed_ids: List[int]) -> Optional[Dict[str, Any]]:
    """Atomically replace a sale's header + items in ONE transaction.

    Validates everything before writing anything, then commits once. Any
    failure rolls back, so the sale is never left partial. psycopg
    transactions are implicit (replacing BEGIN IMMEDIATE). Raises ValueError
    on business-rule violations. Returns the updated sale dict, or None when
    the sale does not exist.
    """
    removed_ids = [int(r) for r in (removed_ids or [])]
    try:
        row = conn.execute("SELECT id FROM sales WHERE id = %s", (sale_id,)).fetchone()
        if not row:
            conn.rollback()
            return None
        existing_ids = {r[0] for r in conn.execute(
            "SELECT id FROM sale_items WHERE sale_id = %s", (sale_id,)).fetchall()}

        pi_number = (header.get("pi_number") or "").strip()
        if not pi_number:
            raise ValueError("pi_number is required")
        if not items:
            raise ValueError("A sale must keep at least one product item")

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
            if qty < 0 or price < 0:
                raise ValueError("Quantity and price cannot be negative")
            item_id = it.get("id")
            if item_id is not None:
                item_id = int(item_id)
                if item_id not in existing_ids:
                    raise ValueError(f"items[{pos}].id {item_id} does not belong to sale {sale_id}")
                if item_id in removed_ids:
                    raise ValueError(f"item {item_id} is both updated and removed")
            seen.append({"id": item_id, "product_name": name, "quantity": qty, "unit_price": price})

        for rid in removed_ids:
            if rid not in existing_ids:
                raise ValueError(f"removed id {rid} does not belong to sale {sale_id}")

        conn.execute(
            """UPDATE sales SET pi_number = %s, pi_date = %s, client_name = %s,
               updated_at = %s WHERE id = %s""",
            (pi_number, header.get("pi_date"), header.get("client_name"), _now_str(), sale_id)
        )
        for it in seen:
            if it["id"] is None:
                conn.execute(
                    "INSERT INTO sale_items (sale_id, product_name, quantity, unit_price) VALUES (%s, %s, %s, %s)",
                    (sale_id, it["product_name"], it["quantity"], it["unit_price"])
                )
            else:
                conn.execute(
                    "UPDATE sale_items SET product_name = %s, quantity = %s, unit_price = %s WHERE id = %s",
                    (it["product_name"], it["quantity"], it["unit_price"], it["id"])
                )
        for rid in removed_ids:
            conn.execute("DELETE FROM sale_items WHERE id = %s", (rid,))
        conn.commit()
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
    conn.execute("DELETE FROM sales WHERE id = %s", (sale_id,))
    log_audit_action(conn, "SALE_DELETE", "sale", sale_id,
                     old_value=row[0] if row else None)
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


# ==================== USER AUTH FUNCTIONS ====================
