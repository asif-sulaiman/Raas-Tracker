"""Company master data (customers) + legacy sales linkage (P0)."""
import psycopg
from typing import Any, Dict, List, Optional

from .db import logger
from .audit import log_audit_action

_FIELDS = ("name", "code", "country", "address", "contact_person",
           "swift", "lc_bank")

# Derived from _FIELDS, never a hand-copied list: P1-4 audit coverage depends
# on this tuple matching _FIELDS, so a newly added column must not be able to
# skip auditing by being forgotten here.
_PATCH_FIELDS = tuple(f for f in _FIELDS if f != "name")


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
    # One transaction for the row and its audit row (see delete_company).
    log_audit_action(conn, "COMPANY_CREATE", "company", row[0], new_value=name,
                     atomic=False)
    conn.commit()
    return row[0]


def update_company(conn: psycopg.Connection, cid: int,
                   fields: Dict[str, Any]) -> Optional[bool]:
    """Update allowlisted fields. None = missing, False = duplicate name."""
    current = get_company(conn, cid)
    if not current:
        return None
    updates = {}
    for key in _PATCH_FIELDS:
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
    # Audit every genuine change, not just a rename (P1-4). Previously the
    # row was gated on a name change, so moving swift/lc_bank/address/
    # contact_person/code/country left no trace at all.
    #
    # Non-name values are deliberately NOT recorded: those columns hold bank
    # and contact data, and keeping them out of audit_logs is a property worth
    # preserving. The field NAMES are the evidence. A name keeps its
    # before/after because a company name is an identifier, not PII.
    changed = sorted(k for k, v in updates.items() if current.get(k) != v)
    if changed:
        name_moved = "name" in changed
        extras = [k for k in changed if k != "name"]
        # Field list FIRST: `new_value` is capped at 2000 chars and a company
        # name has no length limit, so with the name appended last, truncation
        # could eat the list of what changed — the actual evidence.
        new_value = ("changed=" + ",".join(extras)) if extras else None
        if name_moved:
            new_value = (f"{new_value} ({updates['name']})" if new_value
                         else updates["name"])
        # atomic=False, and the single commit moved below: the UPDATE and its
        # audit row are one unit, so a failing audit INSERT cannot leave a
        # changed company with no record of the change.
        log_audit_action(conn, "COMPANY_UPDATE", "company", cid,
                         old_value=current["name"] if name_moved else None,
                         new_value=new_value, atomic=False)
    if updates:
        conn.commit()
    return True


def delete_company(conn: psycopg.Connection, cid: int):
    """Delete a company. True = deleted, False = missing, 'linked' = referenced."""
    try:
        cursor = conn.execute("DELETE FROM companies WHERE id = %s", (cid,))
    except psycopg.errors.ForeignKeyViolation:
        conn.rollback()
        return "linked"
    if cursor.rowcount:
        # atomic=False: the DELETE and its audit row are ONE unit. Committing
        # first (as this used to) meant a failing audit INSERT left a deleted
        # company with no trace of the deletion.
        log_audit_action(conn, "COMPANY_DELETE", "company", cid, atomic=False)
        conn.commit()
        return True
    return False


def backfill_company_links(conn: psycopg.Connection) -> int:
    """Link legacy free-text sales.client_name rows to the companies master.

    Idempotent: case variants share one company (first-seen spelling wins;
    rename later via the Companies page). Returns sales rows newly linked.

    Transaction control belongs to the caller. This used to commit, which
    split _create_tables' migration in two durable halves: anything issued
    before the call was committed even if the rest of the migration then
    failed, leaving a half-applied schema that was retried forever. Standalone
    callers must commit themselves.
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
    if linked:
        logger.info("Backfilled %s sales rows to companies.", linked)
    return linked
