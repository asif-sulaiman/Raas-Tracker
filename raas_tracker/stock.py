"""Chemicals, units, and reorder levels."""

import psycopg
import json
import os
import re
import ipaddress
from datetime import date, datetime
from typing import Optional, List, Dict, Any, Union

from .db import JSON_PATH, get_connection, logger
from .audit import log_audit_action
from .notifications import notify_reorder_status

def import_from_json(json_path: str = JSON_PATH) -> Dict[str, int]:
    """Import chemical stock from parsed JSON data.
    
    Expected JSON structure from parse_stock.py:
    [
        {"item_no": "1", "product_name": "PRODUCT A", "balance_last_month": 0,
         "last_month_unit": "KG", "balance_this_month": 0, "this_month_unit": "KG"},
        ...
    ]
    
    Returns dict of {name: current_qty} for successfully imported chemicals.
    """
    if not os.path.exists(json_path):
        logger.warning("JSON file not found: %s", json_path)
        return {}
    
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    conn = get_connection()
    imported = 0
    skipped = 0
    
    for item in data:
        name = item.get("product_name", "").strip()
        qty = item.get("balance_this_month", 0)
        last_qty = item.get("balance_last_month", 0)
        unit = item.get("this_month_unit", "KG").strip().upper()
        
        if not name:
            skipped += 1
            continue
        
        try:
            existing = conn.execute(
                "SELECT id FROM chemicals WHERE UPPER(name) = %s", (name.upper(),)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE chemicals SET current_qty = %s, balance_last_month = %s, unit = %s, last_updated = %s WHERE UPPER(name) = %s",
                    (float(qty), float(last_qty), unit, date.isoformat(date.today()), name.upper())
                )
            else:
                conn.execute(
                    "INSERT INTO chemicals (name, current_qty, balance_last_month, unit, last_updated) VALUES (%s, %s, %s, %s, %s)",
                    (name, float(qty), float(last_qty), unit, date.isoformat(date.today()))
                )
            imported += 1
        except psycopg.IntegrityError:
            try:
                conn.rollback()
            except Exception:
                pass
            skipped += 1
    
    conn.commit()
    conn.close()
    
    logger.info("Import complete: %s chemicals imported, %s skipped", imported, skipped)
    return {item["product_name"]: item["balance_this_month"] for item in data[:5]}  # sample


def get_all_chemicals(conn: psycopg.Connection) -> List[Dict[str, Any]]:
    """Return all chemicals with current stock levels."""
    cursor = conn.execute(
        "SELECT id, name, current_qty, balance_last_month, unit, last_updated, "
        "COALESCE(reorder_level, 0) FROM chemicals ORDER BY name"
    )
    return [
        {"id": row[0], "name": row[1], "qty": row[2], "balance_last_month": row[3], "unit": row[4],
         "last_updated": row[5], "reorder_level": row[6] or 0}
        for row in cursor.fetchall()
    ]


# ==================== UNIT CONVERSION FUNCTIONS ====================


def get_unit_conversion(conn: psycopg.Connection, from_unit: str, to_unit: str) -> Optional[float]:
    """Get conversion factor between two units.
    
    Args:
        conn: Database connection
        from_unit: Source unit (e.g., 'KG', 'L', 'DRUM')
        to_unit: Target unit (e.g., 'G', 'ML', 'L')
    
    Returns:
        Conversion factor or None if conversion not possible
    """
    from_unit = from_unit.upper().strip()
    to_unit = to_unit.upper().strip()
    
    if from_unit == to_unit:
        return 1.0
    
    # Direct conversion
    cursor = conn.execute(
        "SELECT factor FROM unit_conversions WHERE from_unit = %s AND to_unit = %s",
        (from_unit, to_unit)
    )
    row = cursor.fetchone()
    if row:
        return row[0]

    # Try reverse conversion
    cursor = conn.execute(
        "SELECT factor FROM unit_conversions WHERE from_unit = %s AND to_unit = %s",
        (to_unit, from_unit)
    )
    row = cursor.fetchone()
    if row:
        return 1.0 / row[0]
    
    return None  # Conversion not possible


def convert_quantity(conn: psycopg.Connection, qty: float, from_unit: str, to_unit: str) -> Optional[float]:
    """Convert quantity from one unit to another.
    
    Args:
        conn: Database connection
        qty: Quantity to convert
        from_unit: Source unit
        to_unit: Target unit
    
    Returns:
        Converted quantity or None if conversion not possible
    """
    factor = get_unit_conversion(conn, from_unit, to_unit)
    if factor is None:
        return None
    return qty * factor


def get_all_unit_conversions(conn: psycopg.Connection) -> List[Dict[str, Any]]:
    """Return all unit conversions."""
    cursor = conn.execute("SELECT id, from_unit, to_unit, factor FROM unit_conversions ORDER BY from_unit")
    return [{"id": row[0], "from_unit": row[1], "to_unit": row[2], "factor": row[3]} for row in cursor.fetchall()]


def add_unit_conversion(conn: psycopg.Connection, from_unit: str, to_unit: str, factor: float) -> bool:
    """Add a new unit conversion.

    Audited: the factor governs every stock/quantity conversion in the system,
    and this route is reachable by any session *or* API key, so an unaudited
    change here silently alters every derived quantity in the product.
    """
    fu, tu = from_unit.upper().strip(), to_unit.upper().strip()
    try:
        prev = conn.execute(
            "SELECT factor FROM unit_conversions WHERE from_unit = %s AND to_unit = %s",
            (fu, tu)).fetchone()
        conn.execute(
            "INSERT INTO unit_conversions (from_unit, to_unit, factor) VALUES (%s, %s, %s) "
            "ON CONFLICT (from_unit, to_unit) DO UPDATE SET factor = EXCLUDED.factor",
            (fu, tu, factor)
        )
        log_audit_action(conn, "UNIT_CONVERSION_UPSERT", "unit_conversion", None,
                         old_value=str(prev[0]) if prev else None,
                         new_value=f"{fu}->{tu} factor={factor}",
                         atomic=False)
        conn.commit()
        return True
    except Exception as e:
        logger.exception("Error adding conversion")
        return False


def delete_unit_conversion(conn: psycopg.Connection, from_unit: str, to_unit: str) -> bool:
    """Delete a unit conversion."""
    try:
        conn.execute(
            "DELETE FROM unit_conversions WHERE from_unit = %s AND to_unit = %s",
            (from_unit.upper().strip(), to_unit.upper().strip())
        )
        conn.commit()
        return True
    except Exception as e:
        logger.exception("Error deleting conversion")
        return False


def sync_reorder_notifications(conn: psycopg.Connection,
                               chemical_names: List[str]) -> None:
    """Re-sync reorder notifications for chemicals changed with atomic=False.

    ``notify_reorder_status()`` commits, so it can never run inside a
    caller-owned transaction without silently committing that transaction.
    ``update_stock(atomic=False)`` therefore skips the inline call and the
    caller flushes the affected names here, AFTER it commits: a buy alert for
    a run that was rolled back would be a lie.
    """
    for name in dict.fromkeys(n for n in (chemical_names or []) if n):
        row = conn.execute(
            "SELECT id, name, current_qty, reorder_level FROM chemicals WHERE name = %s",
            (name,)
        ).fetchone()
        if row:
            notify_reorder_status(conn, row[0], row[1], row[2], row[3] or 0)


def update_stock(conn: psycopg.Connection, name: str, delta: float, unit: str = "KG",
                 reason: str = None, atomic: bool = True) -> bool:
    """Update chemical stock by delta (positive or negative).

    Args:
        conn: Database connection
        name: Chemical name
        delta: Quantity change (positive = add, negative = remove)
        unit: Unit of delta (must match chemical's unit or be 'KG')
        reason: Free-text purpose (e.g. 'Supplier delivery'), stored on the
            audit row's note channel (same convention as upload adjustments).
        atomic: When True (default) the UPDATE is committed immediately, as
            every caller has always expected. When False the write (and its
            audit row) is left in the caller's transaction: no commit AND no
            rollback is issued here. Multi-row callers (production runs) MUST
            pass False so a mid-loop failure cannot leave earlier ingredients
            durably deducted; they then flush reorder notifications after
            committing via sync_reorder_notifications().

    Returns:
        True if updated successfully, False if chemical not found

    Raises:
        ValueError: If the delta would result in negative stock ("insufficient stock")
    """
    # Get chemical info with FOR UPDATE to lock the row
    chemical = conn.execute(
        "SELECT id, name, current_qty, unit, reorder_level FROM chemicals WHERE name = %s FOR UPDATE",
        (name,)
    ).fetchone()
    
    if not chemical:
        logger.warning("Chemical '%s' not found in database", name)
        return False
    
    chem_id, chem_name, current_qty, chem_unit, reorder_level = chemical
    
    # Normalize unit - if user provides unit, validate it matches or is KG
    if unit.upper() != chem_unit.upper() and unit.upper() != "KG":
        logger.warning("Unit mismatch: chemical is '%s', provided '%s'", chem_unit, unit)
        # Try converting: if chemical is KG and user says KG, that's fine
        # If different units, we need conversion logic
        # For now, just use the chemical's unit and ignore provided unit
        unit = chem_unit
    
    new_qty = current_qty + delta
    if new_qty < 0:
        raise ValueError("insufficient stock")
    
    conn.execute(
        "UPDATE chemicals SET current_qty = %s, last_updated = %s WHERE id = %s",
        (new_qty, date.isoformat(date.today()), chem_id)
    )
    if atomic:
        conn.commit()
    # Collapse the reason onto one line: the audited format is
    # "<qty> (<reason>)", and an embedded newline both reads badly in the
    # history feed and used to break its parser outright.
    reason_text = " ".join((reason or "").split())[:120]
    log_audit_action(conn, "ADJUST_STOCK", "chemical", chem_id,
                     old_value=str(current_qty),
                     new_value=f"{new_qty} ({reason_text})" if reason_text else str(new_qty),
                     atomic=atomic)
    if atomic:
        notify_reorder_status(conn, chem_id, chem_name, new_qty, reorder_level)

    logger.info("Updated '%s': %s %s -> %s %s (change: %s %s)",
                chem_name, current_qty, chem_unit, new_qty, chem_unit, delta, chem_unit)
    return True


def set_reorder_level(conn: psycopg.Connection, name: str, level: float) -> bool:
    """Set the per-chemical reorder threshold. Returns False if not found.

    Raises ValueError on negative levels. A level of 0 disables the
    low-stock state (only exact-zero counts as out of stock).
    """
    if level is None or level < 0:
        raise ValueError("reorder_level must be 0 or greater")
    row = conn.execute(
        "SELECT id, name, current_qty, reorder_level FROM chemicals WHERE name = %s",
        (name,),
    ).fetchone()
    if not row:
        return False
    chem_id, chem_name, qty, old_level = row
    conn.execute("UPDATE chemicals SET reorder_level = %s WHERE id = %s", (level, chem_id))
    conn.commit()
    log_audit_action(conn, "SET_REORDER_LEVEL", "chemical", chem_id,
                     old_value=str(old_level), new_value=str(level))
    notify_reorder_status(conn, chem_id, chem_name, qty, level)
    return True


def add_chemical(conn: psycopg.Connection, name: str, qty: float, unit: str = "KG") -> bool:
    """Add a new chemical to the database."""
    existing = conn.execute(
        "SELECT id FROM chemicals WHERE UPPER(name) = %s",
        ((name or "").strip().upper(),)
    ).fetchone()
    if existing:
        logger.warning("Chemical '%s' already exists (case-insensitive). Use update_stock instead.", name)
        return False
    try:
        row = conn.execute(
            "INSERT INTO chemicals (name, current_qty, unit, last_updated) VALUES (%s, %s, %s, %s) RETURNING id",
            (name, qty, unit, date.isoformat(date.today()))
        ).fetchone()
        conn.commit()
        log_audit_action(conn, "ADD_CHEMICAL", "chemical", row[0],
                         new_value=str(qty))
        logger.info("Added chemical: %s = %s %s", name, qty, unit)
        return True
    except psycopg.IntegrityError:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("Chemical '%s' already exists. Use update_stock instead.", name)
        return False


_QTY_ACTIONS = ("ADJUST_STOCK", "ADD_CHEMICAL")


def _to_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_ip(note: str) -> bool:
    """True when the value is a bare IP literal rather than operator prose.

    Uses `ipaddress` instead of a dotted-quad regex so IPv6 is recognised too:
    an IPv6 client that adjusted stock without a reason would otherwise have
    its own address rendered as the movement's `purpose` in the history feed
    rather than falling back to "Manual adjustment".
    """
    candidate = (note or "").strip()
    if not candidate:
        return False
    if candidate == "localhost":
        return True
    try:
        ipaddress.ip_address(candidate)
        return True
    except ValueError:
        return False


# re.DOTALL matters: without it `.` does not match a newline, so a multi-line
# reason made the whole value unparseable and the movement's delta came back
# null — which silently removes the adjustment from the history chart.
_QTY_WITH_REASON_RE = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?)\s*(?:\((.*)\))?\s*$",
                                 re.DOTALL)


def _split_qty_reason(value) -> tuple:
    """Split an audit value into ``(quantity, reason)``.

    P1-6 moved the operator's reason out of the ``ip_address`` column and into
    ``new_value`` as ``"<qty> (<reason>)"``, so the quantity is no longer the
    whole string. Rows written before P1-6 still hold a bare number here, so
    anything that does not match falls back to a plain float parse.
    """
    if value is None:
        return None, None
    match = _QTY_WITH_REASON_RE.match(str(value))
    if not match:
        return _to_float(value), None
    return float(match.group(1)), (match.group(2) or "").strip() or None


def get_stock_movements(conn: psycopg.Connection, chemical_id: int = None,
                        actions=None, since: str = None, until: str = None,
                        limit: int = 200) -> List[Dict[str, Any]]:
    """Datewise chemical inventory movements for the stock history feed.

    Reads quantity-affecting audit rows (adjustments, upload applies, adds)
    with chemical names/units joined in. `since`/`until` are 'YYYY-MM-DD'
    bounds on the audit timestamp. Returns newest first.
    """
    actions = tuple(actions) if actions else _QTY_ACTIONS
    try:
        limit = max(1, min(int(limit or 200), 500))
    except (TypeError, ValueError):
        limit = 200
    query = (
        "SELECT a.id, a.action, a.entity_id, c.name, c.unit, "
        "a.old_value, a.new_value, a.user_id, a.ip_address, a.timestamp "
        "FROM audit_logs a LEFT JOIN chemicals c ON c.id = a.entity_id "
        "WHERE a.entity_type = 'chemical' AND a.action = ANY(%s)"
    )
    params: list = [list(actions)]
    if chemical_id is not None:
        query += " AND a.entity_id = %s"
        params.append(chemical_id)
    if since:
        query += " AND substring(a.timestamp, 1, 10) >= %s"
        params.append(since)
    if until:
        query += " AND substring(a.timestamp, 1, 10) <= %s"
        params.append(until)
    query += " ORDER BY a.timestamp DESC, a.id DESC LIMIT %s"
    params.append(limit)
    out = []
    for row in conn.execute(query, params).fetchall():
        old, _old_reason = _split_qty_reason(row[5])
        new, reason = _split_qty_reason(row[6])
        delta = (new - old) if old is not None and new is not None else new
        # Purpose: prefer the reason embedded in new_value (P1-6 onward).
        # Fall back to the ip_address note, which is where rows written before
        # P1-6 kept it — otherwise historical movements would lose their
        # purpose and silently read as "Manual adjustment".
        note = reason or (row[8] if row[8] and not _is_ip(row[8]) else None)
        if row[1] == "ADD_CHEMICAL":
            purpose, source = "New registration", "registration"
        elif note and re.search(r"upload", note, re.IGNORECASE):
            purpose, source = note, "upload"
        else:
            purpose, source = note or "Manual adjustment", "manual"
        out.append({
            "id": row[0], "action": row[1], "chemical_id": row[2],
            "chemical": row[3], "unit": row[4], "old": old, "new": new,
            "delta": delta, "actor": row[7], "purpose": purpose,
            "source": source, "timestamp": row[9],
        })
    return out


# ============================================================
# PHASE 2: RECIPE MANAGEMENT
# ============================================================
