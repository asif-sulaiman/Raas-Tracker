"""One-off cutover: delete the demo recipes and their production runs.

The owner confirmed the recipes and runs currently in the database are demo
data, not a real formula either side of the business relies on. Schema v8
cannot be applied while rows exist that reference the old shape (a recipe was
one product; it now belongs to one invoice), so the cutover runs **before**
the new migration is deployed, against the live v7 database.

Why a separate module rather than a route:

* It runs once. An endpoint would stay reachable forever.
* It must connect with **raw psycopg** so that opening the connection does not
  trigger the v8 DDL it is preparing for.
* It is destructive, so the default is a dry run, applying requires a database
  backup that exists, and the caller owns the transaction.

The caller (``scripts/reset_demo_recipes.py``) takes the advisory lock, then
commits on success or rolls back on any failure; nothing here commits.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from .audit import log_audit_action
from .stock import update_stock

#: The only schema version this cutover understands. v8 changes the shape of
#: every table it deletes from, so running it later would be meaningless.
DEMO_SCHEMA_VERSION = 7

#: Distinct from the migration lock in db.py: taking the same key would make
#: a cutover and a migration wait on each other instead of ordering them.
DEMO_RESET_LOCK_KEY = 0x52414153 + 1

#: FK-safe deletion order. Tables added by v8 are absent on v7 and skipped.
_DELETE_ORDER = (
    "production_run_outputs",
    "production_run_links",
    "production_run_items",
    "production_runs",
    "recipe_items",
    "recipe_products",
    "recipes",
)

_SETTLED_INVOICE_STATUSES = ("booked", "shipped", "paid")


class DemoResetRefused(RuntimeError):
    """The reset was asked for in a state where it must not run."""


def _schema_version(conn: psycopg.Connection) -> Optional[int]:
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'raas_schema_version'"
        ).fetchone()
    except psycopg.Error:
        # A database that predates app_settings cannot be the v7 live one.
        return None
    if row is None:
        return None
    try:
        return int(str(row[0]).strip())
    except (TypeError, ValueError):
        return None


def _existing_tables(conn: psycopg.Connection, names) -> set:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = ANY(%s)",
        (list(names),),
    ).fetchall()
    return {row[0] for row in rows}


def _count(conn: psycopg.Connection, table: str) -> int:
    return conn.execute(
        sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table))
    ).fetchone()[0]


def _export(conn: psycopg.Connection, table: str) -> List[Dict[str, Any]]:
    """Every row about to be deleted, captured before the DELETE."""
    cur = conn.cursor(row_factory=dict_row)
    cur.execute(sql.SQL("SELECT * FROM {} ORDER BY id").format(sql.Identifier(table)))
    return list(cur.fetchall())


def _stock_delta(conn: psycopg.Connection) -> List[Dict[str, Any]]:
    """Per-chemical stock the demo runs deducted, ready to be added back.

    The join prefers the live chemical so a rename still resolves; the snapshot
    name is the fallback because historical ``production_run_items`` rows were
    written with a NULL ``chemical_id`` (``list_recipe_items_by_id`` never
    selected it). Rows that resolve to the same live chemical merge; a name
    with two different units stays split so each is restored in its own unit.
    """
    rows = conn.execute(
        """
        SELECT MIN(pri.chemical_id) AS chemical_id,
               COALESCE(c.name, pri.chemical_name) AS chemical_name,
               COALESCE(c.unit, pri.unit, 'KG') AS unit,
               SUM(pri.deducted_qty) AS quantity
          FROM production_run_items pri
          LEFT JOIN chemicals c ON c.id = pri.chemical_id
         GROUP BY COALESCE(c.name, pri.chemical_name),
                  COALESCE(c.unit, pri.unit, 'KG')
         ORDER BY 2, 3
        """
    ).fetchall()
    return [
        {"chemical_id": row[0], "chemical_name": row[1],
         "unit": row[2], "quantity": float(row[3] or 0)}
        for row in rows
    ]


def _derived_state_after_reset(conn: psycopg.Connection,
                               sale_ids: List[int]) -> List[Dict[str, Any]]:
    """Re-derive each affected sale's production badge from its invoices.

    Never touches the stage or the money: only the badge that said "production
    is running/done" is now false, because the runs it described are gone.
    ``ship_booked`` wins over ``production_running`` when a silenced invoice is
    still booked or shipped; a sale with no invoices left gets NULL.
    """
    updated = []
    for sale_id in sale_ids:
        statuses = [
            row[0] for row in conn.execute(
                "SELECT status FROM invoices WHERE sale_id = %s", (sale_id,)
            ).fetchall()
        ]
        if any(status in _SETTLED_INVOICE_STATUSES for status in statuses):
            badge = "ship_booked"
        elif statuses:
            badge = "production_running"
        else:
            badge = None
        conn.execute(
            "UPDATE sales SET shipment_status = %s, "
            "updated_at = to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS') WHERE id = %s",
            (badge, sale_id))
        updated.append({"sale_id": sale_id, "shipment_status": badge})
    return updated


def reset_demo_recipes(conn: psycopg.Connection, *, apply: bool = False,
                       restore_stock: bool = False,
                       backup_file: Optional[str] = None,
                       report_dir: Optional[str] = None) -> Dict[str, Any]:
    """Delete all recipes and production runs, or preview exactly that.

    Dry run (default) only reads. Apply requires ``backup_file`` to name an
    existing dump; every deleted row and the stock delta are written to
    ``reset_report_<UTC timestamp>.json`` in ``report_dir``.

    The caller owns the transaction: this function never commits or rolls back.
    """
    version = _schema_version(conn)
    if version != DEMO_SCHEMA_VERSION:
        raise DemoResetRefused(
            f"refusing to reset: schema version is {version!r}, expected "
            f"{DEMO_SCHEMA_VERSION}. The reset runs before the v8 migration.")
    if apply and not (backup_file and os.path.isfile(backup_file)):
        raise DemoResetRefused(
            "apply requires --backup-file naming an existing database dump")
    if restore_stock and not apply:
        raise DemoResetRefused(
            "restoring stock requires apply (--yes); a dry run writes nothing")

    tables = _existing_tables(conn, _DELETE_ORDER)
    counts = {table: _count(conn, table) for table in _DELETE_ORDER if table in tables}
    stock_delta = _stock_delta(conn)

    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "schema_version": version,
        "apply": bool(apply),
        "backup_file": backup_file,
        "counts": counts,
        "stock_delta": stock_delta,
        "deleted": {},
        "invoices_reset": [],
        "sales_updated": [],
        "stock_restored": [],
        "report_path": None,
    }

    if not apply:
        return report

    links = conn.execute(
        "SELECT DISTINCT sale_id, invoice_id FROM production_run_links"
    ).fetchall()
    sale_ids = sorted({row[0] for row in links})
    invoice_ids = sorted({row[1] for row in links if row[1] is not None})

    report["deleted"] = {table: _export(conn, table) for table in counts}
    for table in _DELETE_ORDER:
        if table in tables:
            conn.execute(
                sql.SQL("DELETE FROM {}").format(sql.Identifier(table)))

    if invoice_ids:
        reset_ids = [
            row[0] for row in conn.execute(
                "UPDATE invoices SET status = 'planned' "
                "WHERE id = ANY(%s) AND status = 'produced' RETURNING id",
                (invoice_ids,)).fetchall()
        ]
        report["invoices_reset"] = sorted(reset_ids)

    report["sales_updated"] = _derived_state_after_reset(conn, sale_ids)

    if restore_stock:
        reason = f"DEMO_DATA_RESET restore of {counts.get('production_runs', 0)} demo run(s)"
        restored = []
        for item in stock_delta:
            if item["quantity"] <= 0:
                continue
            ok = update_stock(
                conn, item["chemical_name"], item["quantity"],
                unit=item["unit"], reason=reason, atomic=False)
            restored.append({**item, "restored": bool(ok)})
        report["stock_restored"] = restored

    log_audit_action(
        conn, "DEMO_DATA_RESET", "recipe", None,
        new_value=json.dumps(
            {"counts": counts, "stock_delta": stock_delta,
             "stock_restored": report["stock_restored"],
             "backup_file": backup_file},
            default=str, sort_keys=True),
        atomic=False)

    directory = os.path.abspath(report_dir or os.getcwd())
    os.makedirs(directory, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(directory, f"reset_report_{stamp}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)
    report["report_path"] = path
    return report
