"""Packaging register: counted containers, on their own ledger.

Bags, jars and drums are counted in whole pieces and are not chemicals, so they
do not live in ``chemicals``: every chemical reader treats a row there as a
formula ingredient or a reconciliation line
(``uploads.compare_stock_upload``, ``recipes._chemical`` /
``recipes._aggregate_requirements``, the dashboard feed). A separate table keeps
this feature from having to change any of them.

Two properties differ from the chemical model on purpose:

* There is no stored quantity column. The count is ``SUM(delta)`` over
  ``packaging_movements``, so the ledger is the single source of truth and cannot
  drift away from its own history.
* A physical count arrives as an absolute number and is turned into a delta
  *here*, inside the transaction that writes it. ``stock.update_stock`` takes a
  delta the browser computed from a value it read earlier, so two people editing
  one chemical lose one of the two edits.
"""

import logging
from typing import Any, Dict, List, Optional

import psycopg

from raas_tracker._numeric import finite_values
from raas_tracker.audit import get_audit_actor, log_audit_action
from raas_tracker.notifications import notify_packaging_short

logger = logging.getLogger("raas")

MOVEMENT_KINDS = ("RECEIVED", "ISSUED", "RETURNED", "DAMAGED", "SCRAPPED", "COUNT")

# Free-text purpose on the movement row, bounded like every other audit channel.
MAX_REASON_LEN = 120
MAX_LABEL_LEN = 120

# Ceiling for one history page. `flask_app` re-exports this as the API's stated
# limit; clamping lives here so a caller that bypasses HTTP cannot widen it.
MAX_HISTORY_LIMIT = 500


def _pieces(value: Any, field: str) -> int:
    """Coerce a piece count to an exact int, refusing a fractional or non-finite one.

    Containers are counted, not weighed: ``REAL`` (float4) arithmetic is what
    makes chemical quantities imprecise, so a movement of 2.5 drums is an error
    rather than a rounding.
    """
    finite_values(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a whole number of pieces")
    if number != int(number):
        raise ValueError(f"{field} must be a whole number of pieces")
    return int(number)


def _pieces_float(value: Any) -> float:
    """Validate a capacity: finite and non-negative. Counts stay ints elsewhere."""
    finite_values(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("capacity_kg must be a number")
    if number < 0:
        raise ValueError("capacity_kg must be 0 or greater")
    return number


def _norm_label(label: str) -> str:
    return " ".join(str(label or "").split())[:MAX_LABEL_LEN]


def _resolve_item(conn: psycopg.Connection, *, item_id: Optional[int] = None,
                  label: Optional[str] = None,
                  for_update: bool = False) -> Optional[Dict[str, Any]]:
    """Find one packaging type by id or label, case-insensitively.

    The lookup uses ``lower(label)`` because that is the expression the unique
    index is built on. ``stock.update_stock`` reads ``WHERE name = %s`` against
    ``idx_chemicals_name_lower`` and silently reports "not found" for a name whose
    case differs from the row.
    """
    if item_id is None and not label:
        return None
    if item_id is not None:
        row = conn.execute(
            "SELECT id, label, capacity_kg, unit, reorder_level FROM packaging_items "
            "WHERE id = %s", (item_id,)).fetchone()
    else:
        row = conn.execute(
            "SELECT id, label, capacity_kg, unit, reorder_level FROM packaging_items "
            "WHERE lower(label) = lower(%s)", (_norm_label(label),)).fetchone()
    if not row:
        return None
    item = {"id": row[0], "label": row[1], "capacity_kg": float(row[2]),
            "unit": row[3], "reorder_level": row[4]}
    if for_update:
        # Serialise movements of one type: the SUM below and the INSERT that
        # follows must describe the same count, or two concurrent clerks write
        # two rows that both claim to be "before".
        conn.execute("SELECT 1 FROM packaging_items WHERE id = %s FOR UPDATE",
                     (item["id"],))
    return item


def _sum(conn: psycopg.Connection, item_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(delta), 0) FROM packaging_movements WHERE item_id = %s",
        (item_id,)).fetchone()
    return int(row[0])


def packaging_count(conn: psycopg.Connection, *, item_id: Optional[int] = None,
                    label: Optional[str] = None) -> Optional[int]:
    """Pieces on hand for one type, read from the ledger. None if unknown."""
    item = _resolve_item(conn, item_id=item_id, label=label)
    if not item:
        return None
    return _sum(conn, item["id"])


def list_packaging_items(conn: psycopg.Connection) -> List[Dict[str, Any]]:
    """Every packaging type with its ledger-derived count. One grouped query."""
    rows = conn.execute(
        """SELECT p.id, p.label, p.capacity_kg, p.unit, p.reorder_level, p.created_at,
                  COALESCE(SUM(m.delta), 0)::integer AS qty,
                  MAX(m.created_at) AS last_moved_at
           FROM packaging_items p
           LEFT JOIN packaging_movements m ON m.item_id = p.id
           GROUP BY p.id, p.label, p.capacity_kg, p.unit, p.reorder_level, p.created_at
           ORDER BY lower(p.label)"""
    ).fetchall()
    return [{"id": r[0], "label": r[1], "capacity_kg": float(r[2]), "unit": r[3],
             "reorder_level": r[4], "created_at": r[5], "qty": int(r[6]),
             "last_moved_at": r[7]} for r in rows]


def create_packaging_item(conn: psycopg.Connection, label: str,
                          capacity_kg: Any = 0,
                          unit: str = "PCS") -> Optional[Dict[str, Any]]:
    """Register a new container type. None when the label already exists.

    A type born at zero raises no alert: the seed creates five of them, and a
    fresh database would otherwise open with five critical notifications about
    packaging nobody has counted yet.
    """
    label = _norm_label(label)
    if not label:
        raise ValueError("label is required")
    capacity = _pieces_float(capacity_kg)
    unit_name = str(unit or "PCS").strip().upper()[:20] or "PCS"
    # No conflict target: `ON CONFLICT (label) DO NOTHING` would not cover the
    # lower(label) uniqueness, and a case-variant duplicate must not raise.
    row = conn.execute(
        "INSERT INTO packaging_items (label, capacity_kg, unit) "
        "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING id, created_at",
        (label, capacity, unit_name)).fetchone()
    if not row:
        # Nothing was written, so there is nothing to undo — and no rollback,
        # which would discard an enclosing caller's work too.
        logger.warning("Packaging type '%s' already exists", label)
        return None
    log_audit_action(conn, "ADD_PACKAGING_ITEM", "packaging", row[0],
                     new_value=f"{label} ({capacity:g} kg)", atomic=False)
    conn.commit()
    logger.info("Added packaging type: %s", label)
    return {"id": row[0], "label": label, "capacity_kg": capacity,
            "unit": unit_name, "reorder_level": 0, "qty": 0,
            "created_at": row[1]}


def record_packaging_movement(conn: psycopg.Connection, *,
                              item_id: Optional[int] = None,
                              label: Optional[str] = None,
                              kind: str, delta: Any = None,
                              counted_qty: Any = None,
                              reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Write one ledger row and return it. None when the type is unknown.

    Give either ``delta`` (an in/out movement) or ``counted_qty`` (what a person
    physically counted, which this resolves to a delta server-side), never both.

    Raises ValueError for an unknown kind, a fractional or negative count, and any
    movement that would take the count below zero.

    Alerts fire only here and in :func:`set_packaging_reorder_level`, after the
    commit: ``notifications.notify`` commits, so it can never run inside the
    caller's transaction — and a buy alert for a movement that was rolled back
    would be a lie.
    """
    kind_name = str(kind or "").strip().upper()
    if kind_name not in MOVEMENT_KINDS:
        raise ValueError(f"kind must be one of {', '.join(MOVEMENT_KINDS)}")
    if delta is not None and counted_qty is not None:
        raise ValueError("provide either delta or counted_qty, not both")
    if delta is None and counted_qty is None:
        raise ValueError("delta or counted_qty is required")
    if kind_name == "COUNT" and counted_qty is None:
        raise ValueError("a COUNT must state counted_qty")
    move = 0
    counted: Optional[int] = None
    if delta is not None:
        move = _pieces(delta, "delta")
    else:
        counted = _pieces(counted_qty, "counted_qty")
        if counted < 0:
            raise ValueError("counted_qty must be 0 or greater")

    item = _resolve_item(conn, item_id=item_id, label=label, for_update=True)
    if not item:
        conn.rollback()
        logger.warning("Packaging type %r not found", label or item_id)
        return None

    before = _sum(conn, item["id"])
    after = counted if counted is not None else before + move
    applied_delta = after - before
    if after < 0:
        conn.rollback()
        raise ValueError(
            f"{item['label']} has {before} pieces; issuing {abs(applied_delta)} "
            "would take it below zero")

    reason_text = " ".join(str(reason or "").split())[:MAX_REASON_LEN] or None
    actor = (get_audit_actor() or "system")
    row = conn.execute(
        """INSERT INTO packaging_movements
           (item_id, kind, delta, before_qty, after_qty, reason, actor)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           RETURNING id, created_at""",
        (item["id"], kind_name, applied_delta, before, after, reason_text, actor)
    ).fetchone()
    # atomic=False: the ledger row and its audit row are one unit, so a failed
    # audit INSERT cannot leave a counted movement with no trail.
    log_audit_action(conn, "PACKAGING_MOVEMENT", "packaging", item["id"],
                     old_value=str(before), new_value=str(after), atomic=False)
    conn.commit()

    notify_packaging_short(conn, item["id"], item["label"], after,
                           item["reorder_level"])
    logger.info("Packaging '%s': %s %+d -> %d (%s)", item["label"], kind_name,
                applied_delta, after, reason_text or "-")
    return {"id": row[0], "item_id": item["id"], "label": item["label"],
            "kind": kind_name, "delta": applied_delta, "before_qty": before,
            "after_qty": after, "reason": reason_text, "actor": actor,
            "created_at": row[1]}


def set_packaging_reorder_level(conn: psycopg.Connection, label: str,
                                level: Any) -> bool:
    """Set the per-type buy threshold. False when the type is unknown.

    0 disables the low-stock state; only an exact zero count then raises.
    """
    level_int = _pieces(level, "reorder_level")
    if level_int < 0:
        raise ValueError("reorder_level must be 0 or greater")
    item = _resolve_item(conn, label=label)
    if not item:
        return False
    old_level = item["reorder_level"]
    conn.execute("UPDATE packaging_items SET reorder_level = %s WHERE id = %s",
                 (level_int, item["id"]))
    log_audit_action(conn, "SET_PACKAGING_REORDER_LEVEL", "packaging", item["id"],
                     old_value=str(old_level), new_value=str(level_int), atomic=False)
    conn.commit()
    notify_packaging_short(conn, item["id"], item["label"],
                           _sum(conn, item["id"]), level_int)
    return True


def _day_bound(value: Optional[str], end_of_day: bool) -> Optional[str]:
    """Extend a bare YYYY-MM-DD to a full timestamp so text bounds compare right.

    ``created_at`` holds 'YYYY-MM-DD HH24:MI:SS'. A date-only `until` bound
    compared directly would drop that whole day ('2026-11-30 08:00' sorts above
    '2026-11-30'), and wrapping the column in ``substring()`` — which is what
    ``stock.get_stock_movements`` does — makes the predicate unusable for the
    (item_id, created_at) index. Bounding the *literal* keeps both.
    """
    text = (value or "").strip()
    if not text:
        return None
    if len(text) == 10:
        return f"{text} 23:59:59" if end_of_day else f"{text} 00:00:00"
    return text


def get_packaging_movements(conn: psycopg.Connection, *,
                            item_id: Optional[int] = None,
                            label: Optional[str] = None,
                            since: Optional[str] = None,
                            until: Optional[str] = None,
                            limit: int = 200) -> List[Dict[str, Any]]:
    """Ledger rows, newest first. A direct read of real columns — no parsing.

    Contrast with ``stock.get_stock_movements``, which reconstructs chemical
    movements by regex over ``audit_logs.new_value`` text: its history depth is
    whatever audit retention allows, and a change to that text format silently
    drops rows from the feed.
    """
    clause, params = "", []
    if item_id is not None:
        clause, params = "WHERE m.item_id = %s", [item_id]
    elif label:
        clause, params = "WHERE lower(p.label) = lower(%s)", [_norm_label(label)]
    bounds = []
    lower = _day_bound(since, end_of_day=False)
    upper = _day_bound(until, end_of_day=True)
    if lower:
        bounds.append("m.created_at >= %s")
        params.append(lower)
    if upper:
        bounds.append("m.created_at <= %s")
        params.append(upper)
    if bounds:
        clause += (" AND " if clause else "WHERE ") + " AND ".join(bounds)
    size = max(1, min(int(limit or 200), MAX_HISTORY_LIMIT))
    rows = conn.execute(
        f"""SELECT m.id, m.item_id, p.label, m.kind, m.delta, m.before_qty,
                   m.after_qty, m.reason, m.actor, m.created_at
            FROM packaging_movements m
            JOIN packaging_items p ON p.id = m.item_id
            {clause}
            ORDER BY m.id DESC
            LIMIT %s""",
        (*params, size)).fetchall()
    return [{"id": r[0], "item_id": r[1], "label": r[2], "kind": r[3],
             "delta": r[4], "before_qty": r[5], "after_qty": r[6],
             "reason": r[7], "actor": r[8], "created_at": r[9]} for r in rows]
