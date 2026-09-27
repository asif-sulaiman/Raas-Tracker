"""Sales pipeline, items, payments, and summaries."""

import psycopg
import json
import os
import re
from datetime import date, datetime
from typing import Optional, List, Dict, Any, Union

from .audit import log_audit_action
from .notifications import notify_sale_stage, clear_dedupe, clear_maturity_dedupe


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
            """INSERT INTO sale_items (sale_id, product_name, quantity, unit_price, unit)
               VALUES (%s, %s, %s, %s, %s)""",
            (sale_id, item["product_name"], item.get("quantity", 0), item.get("unit_price", 0), unit)
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
               s.created_at, s.updated_at, s.company_id, s.maturity_date, s.comments,
               COALESCE(c.name, s.client_name),
                COALESCE(ROUND(SUM(si.quantity * si.unit_price)::numeric, 2)::float8, 0) AS total_value,
                COUNT(si.id) AS item_count,
                COALESCE((SELECT ROUND(SUM(sp.payment_amount)::numeric, 2)::float8 FROM sale_payments sp
                          WHERE sp.sale_id = s.id), 0) AS total_paid
        FROM sales s
        LEFT JOIN sale_items si ON si.sale_id = s.id
        LEFT JOIN companies c ON c.id = s.company_id
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
    query += " GROUP BY s.id, c.id ORDER BY s.created_at DESC, s.id DESC"
    return [
        {"id": r[0], "stage": r[1], "pi_number": r[2], "pi_date": r[3],
         "client_name": r[4], "pi_file_path": r[5], "lc_number": r[6],
         "lc_date": r[7], "shipment_date": r[8], "payment_date": r[9],
         "payment_amount": r[10], "created_at": r[11], "updated_at": r[12],
         "company_id": r[13], "maturity_date": r[14], "comments": r[15],
         "company_name": r[16],
         "total_value": r[17], "item_count": r[18], "total_paid": r[19],
         "balance": r[17] - r[19]}
        for r in conn.execute(query, params).fetchall()
    ]


def get_sale_by_id(conn: psycopg.Connection, sale_id: int) -> Optional[Dict[str, Any]]:
    """Return a single sale with its items and stage history."""
    row = conn.execute(
        "SELECT s.id, s.stage, s.pi_number, s.pi_date, s.client_name, "
        "s.pi_file_path, s.lc_number, s.lc_date, s.shipment_date, "
        "s.payment_date, s.payment_amount, s.created_at, s.updated_at, "
        "s.company_id, s.maturity_date, s.comments, c.name "
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
    }
    sale["items"] = [
        {"id": r[0], "sale_id": r[1], "product_name": r[2],
         "quantity": r[3], "unit_price": r[4], "unit": r[5] or "KG"}
        for r in conn.execute(
            "SELECT id, sale_id, product_name, quantity, unit_price, unit "
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
    sale["invoice_total"] = sum(
        (i["quantity"] or 0) * (i["unit_price"] or 0) for i in sale["items"]
    )
    sale["total_paid"] = sum(p["payment_amount"] or 0 for p in sale["payments"])
    sale["balance"] = sale["invoice_total"] - sale["total_paid"]
    sale["shipments"] = list_shipments(conn, sale_id)
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
    conn.commit()
    log_audit_action(conn, "SHIPMENT_RECORD", "sale", sale_id,
                     new_value=f"{ship_date.strip()}")
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
    cursor = conn.execute(
        "DELETE FROM shipments WHERE id = %s AND sale_id = %s",
        (shipment_id, sale_id))
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
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (sale_id, payment_date, payment_amount, notes)
    )
    payment_id = cursor.fetchone()[0]
    _sync_sale_payment_totals(conn, sale_id)
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


def update_sale_payment_record(conn: psycopg.Connection, payment_id: int,
                               payment_date: Optional[str] = None,
                               payment_amount: Optional[float] = None,
                               notes: Optional[str] = None) -> bool:
    """Edit a payment record. Reverts a completed sale to payment_due
    if the new total no longer covers the invoice."""
    row = conn.execute("SELECT sale_id, payment_amount FROM sale_payments WHERE id = %s",
                       (payment_id,)).fetchone()
    if not row:
        return False
    sale_id, old_amount = row[0], row[1]
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


def delete_sale_payment_record(conn: psycopg.Connection, payment_id: int) -> bool:
    """Delete a payment record and re-sync totals."""
    row = conn.execute("SELECT sale_id, payment_amount FROM sale_payments WHERE id = %s",
                       (payment_id,)).fetchone()
    if not row:
        return False
    sale_id = row[0]
    conn.execute("DELETE FROM sale_payments WHERE id = %s", (payment_id,))
    _sync_sale_payment_totals(conn, sale_id)
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
    """Add a line item to an existing sale. Returns the new item ID."""
    unit = (unit or "KG").strip().upper() or "KG"
    cursor = conn.execute(
        """INSERT INTO sale_items (sale_id, product_name, quantity, unit_price, unit)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (sale_id, product_name, quantity, unit_price, unit)
    )
    item_id = cursor.fetchone()[0]
    conn.commit()
    log_audit_action(conn, "SALE_ITEM_ADD", "sale_item", item_id,
                     new_value=json.dumps({"sale_id": sale_id, "product_name": product_name,
                                           "quantity": quantity, "unit_price": unit_price, "unit": unit},
                                          default=str))
    return item_id


def update_sale_item(conn: psycopg.Connection, item_id: int,
                     product_name: Optional[str] = None,
                     quantity: Optional[float] = None,
                     unit_price: Optional[float] = None,
                     unit: Optional[str] = None) -> bool:
    """Update fields of a sale line item."""
    # Fetch old item for audit
    old_row = conn.execute(
        "SELECT product_name, quantity, unit_price, unit FROM sale_items WHERE id = %s",
        (item_id,)).fetchone()
    if not old_row:
        return False
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
    conn.execute(f"UPDATE sale_items SET {', '.join(fields)} WHERE id = %s", vals)
    conn.commit()
    
    # Audit log
    new_item = {"product_name": product_name, "quantity": quantity,
                "unit_price": unit_price, "unit": unit}
    log_audit_action(conn, "SALE_ITEM_UPDATE", "sale_item", item_id,
                     old_value=json.dumps(old_item, default=str),
                     new_value=json.dumps(new_item, default=str))
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
        row = conn.execute(
            "SELECT id, company_id, client_name FROM sales WHERE id = %s",
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
            if qty < 0 or price < 0:
                raise ValueError("Quantity and price cannot be negative")
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
               company_id = %s, comments = %s, updated_at = %s WHERE id = %s""",
            (pi_number, header.get("pi_date"), client_name, company_id,
             header.get("comments"), _now_str(), sale_id)
        )
        for it in seen:
            if it["id"] is None:
                conn.execute(
                    "INSERT INTO sale_items (sale_id, product_name, quantity, unit_price, unit) VALUES (%s, %s, %s, %s, %s)",
                    (sale_id, it["product_name"], it["quantity"], it["unit_price"], it["unit"])
                )
            else:
                conn.execute(
                    "UPDATE sale_items SET product_name = %s, quantity = %s, unit_price = %s, unit = %s WHERE id = %s",
                    (it["product_name"], it["quantity"], it["unit_price"], it["unit"], it["id"])
                )
        for rid in removed_ids:
            conn.execute("DELETE FROM sale_items WHERE id = %s", (rid,))
        conn.commit()
        
        # Audit log
        new_snapshot = {
            "header": header,
            "items": items,
            "removed_ids": removed_ids,
        }
        log_audit_action(conn, "SALE_UPDATE", "sale", sale_id,
                         old_value=json.dumps(old_snapshot, default=str),
                         new_value=json.dumps(new_snapshot, default=str))
        
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


def get_commercial_report(conn: psycopg.Connection) -> List[Dict[str, Any]]:
    """Live commercial report — one output row per sale_item, USD only.

    Item-level columns (product, qty, price, total_price, dates on the
    shipment) vary per row. Sale-level payment columns — received_amount,
    due_amount, receive_date, maturity_date, payment_status,
    payment_comment — are computed per SALE and repeated identically on
    every row of that sale. Sale total and sale paid are aggregated in
    scalar subqueries so item x shipment x payment joins can never
    double count.
    """
    query = """
        SELECT
            s.id AS sale_id,
            COALESCE(c.name, s.client_name) AS customer_name,
            s.pi_number, s.pi_date,
            s.lc_number, s.lc_date,
            si.product_name, si.unit, si.quantity, si.unit_price,
            ROUND((si.quantity * si.unit_price)::numeric, 2)::float8 AS total_price,
            (SELECT MAX(sh.invoice_date)
               FROM shipments sh WHERE sh.sale_id = s.id) AS invoice_date,
            (SELECT MAX(sh.ship_date)
               FROM shipments sh WHERE sh.sale_id = s.id) AS latest_ship_date,
            s.shipment_date AS actual_ship_date,
            s.maturity_date,
            (SELECT MAX(sp.payment_date)
               FROM sale_payments sp WHERE sp.sale_id = s.id) AS receive_date,
            (SELECT COALESCE(ROUND(SUM(sp.payment_amount)::numeric, 2), 0)::float8
               FROM sale_payments sp WHERE sp.sale_id = s.id) AS received_amount,
            (SELECT COALESCE(ROUND(SUM(si2.quantity * si2.unit_price)::numeric, 2), 0)::float8
               FROM sale_items si2 WHERE si2.sale_id = s.id) AS sale_total,
            s.comments
        FROM sales s
        LEFT JOIN sale_items si ON si.sale_id = s.id
        LEFT JOIN companies c ON c.id = s.company_id
        ORDER BY s.created_at DESC
    """
    rows = conn.execute(query).fetchall()

    report = []
    for r in rows:
        (sale_id, customer_name, pi_number, pi_date, lc_number, lc_date,
         product_name, unit, quantity, unit_price, total_price,
         invoice_date, latest_ship_date, actual_ship_date,
         maturity_date, receive_date, received_amount, sale_total,
         comments) = r

        received = round(float(received_amount or 0), 2)
        # due = ROUND(sale_total, 2) - sale_paid, clamped at 0.
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
            "actual_ship_date": actual_ship_date,
            "maturity_date": maturity_date,
            "receive_date": receive_date,
            "received_amount": received,
            "due_amount": due_amount,
            "payment_status": pay_status,
            "payment_comment": pay_comment,
        })

    return report
