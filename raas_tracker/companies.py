"""Company master data (customers) + legacy sales linkage (P0)."""
import psycopg
from typing import Any, Dict, List, Optional

from .db import logger
from .audit import log_audit_action

_FIELDS = ("name", "code", "country", "address", "contact_person",
           "swift", "lc_bank")


def _row_to_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": row[0], "name": row[1], "code": row[2], "country": row[3],
        "address": row[4], "contact_person": row[5], "swift": row[6],
        "lc_bank": row[7], "created_at": row[8],
    }


def list_companies(conn: psycopg.Connection) -> List[Dict[str, Any]]:
    """All companies, alphabetical."""
    rows = conn.execute(
        "SELECT id, name, code, country, address, contact_person, swift, "
        "lc_bank, created_at FROM companies ORDER BY lower(name)").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_company(conn: psycopg.Connection, cid: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT id, name, code, country, address, contact_person, swift, "
        "lc_bank, created_at FROM companies WHERE id = %s", (cid,)).fetchone()
    return _row_to_dict(row) if row else None


def create_company(conn: psycopg.Connection, *, name: str, code: str = None,
                   country: str = None, address: str = None,
                   contact_person: str = None, swift: str = None,
                   lc_bank: str = None) -> Optional[int]:
    """Insert a company. Returns id, or None on (case-insensitive) duplicate."""
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    existing = conn.execute(
        "SELECT id FROM companies WHERE lower(name) = lower(%s)",
        (name,)).fetchone()
    if existing:
        logger.warning("Company '%s' already exists.", name)
        return None
    row = conn.execute(
        "INSERT INTO companies (name, code, country, address, contact_person, "
        "swift, lc_bank) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (name, code or None, country or None, address or None,
         contact_person or None, swift or None, lc_bank or None)).fetchone()
    conn.commit()
    log_audit_action(conn, "COMPANY_CREATE", "company", row[0], new_value=name)
    return row[0]


def update_company(conn: psycopg.Connection, cid: int,
                   fields: Dict[str, Any]) -> Optional[bool]:
    """Update allowlisted fields. None = missing, False = duplicate name."""
    current = get_company(conn, cid)
    if not current:
        return None
    updates = {}
    for key in ("code", "country", "address", "contact_person", "swift",
                "lc_bank"):
        if key in fields:
            updates[key] = fields[key] or None
    if "name" in fields:
        name = (fields["name"] or "").strip()
        if not name:
            raise ValueError("name is required")
        dup = conn.execute(
            "SELECT id FROM companies WHERE lower(name) = lower(%s) AND id <> %s",
            (name, cid)).fetchone()
        if dup:
            return False
        updates["name"] = name
    if updates:
        conn.execute(
            "UPDATE companies SET {} WHERE id = %s".format(
                ", ".join(f"{k} = %s" for k in updates)),
            (*updates.values(), cid))
        conn.commit()
    if "name" in updates and updates["name"] != current["name"]:
        log_audit_action(conn, "COMPANY_UPDATE", "company", cid,
                         old_value=current["name"], new_value=updates["name"])
    return True


def delete_company(conn: psycopg.Connection, cid: int):
    """Delete a company. True = deleted, False = missing, 'linked' = referenced."""
    try:
        cursor = conn.execute("DELETE FROM companies WHERE id = %s", (cid,))
        conn.commit()
    except psycopg.errors.ForeignKeyViolation:
        conn.rollback()
        return "linked"
    if cursor.rowcount:
        log_audit_action(conn, "COMPANY_DELETE", "company", cid)
        return True
    return False


def backfill_company_links(conn: psycopg.Connection) -> int:
    """Link legacy free-text sales.client_name rows to the companies master.

    Idempotent: case variants share one company (first-seen spelling wins;
    rename later via the Companies page). Returns sales rows newly linked.
    """
    names = conn.execute(
        "SELECT DISTINCT trim(client_name) FROM sales "
        "WHERE client_name IS NOT NULL AND trim(client_name) <> ''").fetchall()
    for (name,) in names:
        existing = conn.execute(
            "SELECT id FROM companies WHERE lower(name) = lower(%s)",
            (name,)).fetchone()
        if not existing:
            conn.execute("INSERT INTO companies (name) VALUES (%s)", (name,))
    cursor = conn.execute(
        """UPDATE sales SET company_id =
               (SELECT id FROM companies
                WHERE lower(companies.name) = lower(trim(sales.client_name)))
           WHERE company_id IS NULL
             AND client_name IS NOT NULL AND trim(client_name) <> ''""")
    linked = cursor.rowcount or 0
    conn.commit()
    if linked:
        logger.info("Backfilled %s sales rows to companies.", linked)
    return linked
