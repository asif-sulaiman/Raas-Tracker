"""In-app notifications: creation (with dedupe), role-filtered listing, read state."""

import psycopg
from typing import Any, Dict, List, Optional

SEVERITIES = ("info", "warning", "critical")
ROLE_SCOPES = ("all", "admin")


def notify(conn: psycopg.Connection, *, type: str, title: str, body: str = "",
           severity: str = "info", role_scope: str = "all",
           entity_type: Optional[str] = None, entity_id: Optional[int] = None,
           dedupe_key: Optional[str] = None) -> Optional[int]:
    """Insert a notification. Returns id, or None if deduped (key already exists).

    Dedupe prevents alert spam for an ongoing condition (same chemical still
    below reorder level, same sale still awaiting payment, ...). Callers clear
    the key when the condition resolves so a future breach notifies again.
    """
    if severity not in SEVERITIES:
        severity = "info"
    if role_scope not in ROLE_SCOPES:
        role_scope = "all"
    if dedupe_key:
        existing = conn.execute(
            "SELECT id FROM notifications WHERE dedupe_key = %s", (dedupe_key,)
        ).fetchone()
        if existing:
            return None
    cursor = conn.execute(
        """INSERT INTO notifications
           (type, title, body, severity, role_scope, entity_type, entity_id, dedupe_key)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (type, title, body, severity, role_scope, entity_type, entity_id, dedupe_key),
    )
    conn.commit()
    return cursor.fetchone()[0]


def clear_dedupe(conn: psycopg.Connection, dedupe_key: str) -> None:
    """Remove a dedupe row so the same condition can notify again later."""
    conn.execute("DELETE FROM notifications WHERE dedupe_key = %s", (dedupe_key,))
    conn.commit()


def list_notifications_for(conn: psycopg.Connection, user_id: int, user_role: str,
                           limit: int = 50) -> List[Dict[str, Any]]:
    """Recent notifications visible to this user, newest first, with read flags."""
    rows = conn.execute(
        """SELECT n.id, n.type, n.title, n.body, n.severity, n.role_scope,
                  n.entity_type, n.entity_id, n.created_at,
                  CASE WHEN r.notification_id IS NULL THEN 0 ELSE 1 END AS is_read
           FROM notifications n
            LEFT JOIN notification_reads r
              ON r.notification_id = n.id AND r.user_id = %s
            WHERE n.role_scope = 'all' OR %s = 'admin'
            ORDER BY n.id DESC
            LIMIT %s""",
        (user_id, user_role, limit),
    )
    return [_row_to_dict(row) for row in rows]


def unread_count(conn: psycopg.Connection, user_id: int, user_role: str) -> int:
    """Number of unseen notifications visible to this user."""
    row = conn.execute(
        """SELECT COUNT(*)
           FROM notifications n
            LEFT JOIN notification_reads r
              ON r.notification_id = n.id AND r.user_id = %s
            WHERE r.notification_id IS NULL
              AND (n.role_scope = 'all' OR %s = 'admin')""",
        (user_id, user_role),
    ).fetchone()
    return row[0]


def mark_read(conn: psycopg.Connection, user_id: int, notification_ids: List[int]) -> int:
    """Mark specific notifications read. Returns newly marked count."""
    marked = 0
    for nid in notification_ids:
        cursor = conn.execute(
            """INSERT INTO notification_reads (user_id, notification_id)
               VALUES (%s, %s) ON CONFLICT DO NOTHING""",
            (user_id, nid),
        )
        marked += cursor.rowcount
    conn.commit()
    return marked


def mark_read_all_for(conn: psycopg.Connection, user_id: int, user_role: str) -> int:
    """Mark every currently visible notification read for this user."""
    cursor = conn.execute(
        """INSERT INTO notification_reads (user_id, notification_id)
           SELECT %s, id FROM notifications
           WHERE role_scope = 'all' OR %s = 'admin' ON CONFLICT DO NOTHING""",
        (user_id, user_role),
    )
    conn.commit()
    return cursor.rowcount


def notify_reorder_status(conn: psycopg.Connection, chem_id: int, name: str,
                          qty: float, reorder_level: float) -> None:
    """Sync stock notifications with current qty vs reorder threshold.

    Breached now -> insert once (deduped); recovered -> clear the key so the
    next breach notifies again. qty == 0 is always critical, even with no
    reorder level configured; low-stock needs reorder_level > 0.
    """
    zero_key = f"stock:0:{chem_id}"
    low_key = f"stock:low:{chem_id}"
    if qty <= 0:
        notify(conn, type="stock_out", title=f"Out of stock: {name}",
               body=f"{name} has 0 remaining. Reorder immediately.",
               severity="critical", entity_type="chemical", entity_id=chem_id,
               dedupe_key=zero_key)
        clear_dedupe(conn, low_key)
    elif reorder_level > 0 and qty <= reorder_level:
        notify(conn, type="stock_low", title=f"Low stock: {name}",
               body=f"{name} is at {qty:g} (reorder level {reorder_level:g}).",
               severity="warning", entity_type="chemical", entity_id=chem_id,
               dedupe_key=low_key)
        clear_dedupe(conn, zero_key)
    else:
        clear_dedupe(conn, zero_key)
        clear_dedupe(conn, low_key)


def notify_sale_stage(conn: psycopg.Connection, sale_id: int, client_name: str,
                      from_stage: str, to_stage: str) -> None:
    """Notify on payment_due entry (warning) and completion (info).

    Leaving payment_due (payment recorded) clears the warning so a later
    revert-to-due can notify again.
    """
    due_key = f"sale:{sale_id}:payment_due"
    if from_stage == "payment_due" and to_stage != "payment_due":
        clear_dedupe(conn, due_key)
    if to_stage == "payment_due":
        notify(conn, type="sale_payment_due", title=f"Payment due: {client_name}",
               body=f"Sale #{sale_id} ({client_name}) entered payment_due.",
               severity="warning", entity_type="sale", entity_id=sale_id,
               dedupe_key=due_key)
    elif to_stage == "completed":
        notify(conn, type="sale_completed", title=f"Sale completed: {client_name}",
               body=f"Sale #{sale_id} ({client_name}) is fully paid and closed.",
               severity="info", entity_type="sale", entity_id=sale_id,
               dedupe_key=f"sale:{sale_id}:completed")


def _row_to_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": row[0], "type": row[1], "title": row[2], "body": row[3],
        "severity": row[4], "role_scope": row[5], "entity_type": row[6],
        "entity_id": row[7], "created_at": row[8], "is_read": bool(row[9]),
    }
