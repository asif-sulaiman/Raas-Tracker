"""One-off SQLite -> PostgreSQL migration for RAAS Tracker (Phase 4).

Reads the legacy SQLite file READ-ONLY (pass the backup copy, never the
live database) and loads every row into PostgreSQL, then fixes identity
sequences and verifies per-table counts.

Usage:
    set DATABASE_URL=postgresql://...   (never commit this value)
    python scripts/migrate_sqlite_to_pg.py --sqlite chem_stock.backup-YYYY-MM-DD.db --wipe

--wipe drops all public tables on the target first (test leftovers).
Without --wipe the script refuses to run on a non-empty target.
Re-runs are safe: always pass --wipe for a full migration.

Exit 0 only when every table verifies (sqlite count == pg count).
Prints usernames / PI numbers / counts only -- never hashes or tokens.
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg  # noqa: E402

from raas_tracker.db import _create_tables, get_connection  # noqa: E402

# FK-safe load order (parents before children).
TABLE_ORDER = [
    "chemicals",
    "recipes",
    "recipe_items",
    "uploads",
    "upload_rows",
    "unit_conversions",
    "reason_codes",
    "approval_workflow",
    "audit_logs",
    "reconciliation_periods",
    "sales",
    "sale_items",
    "sales_stage_history",
    "sale_payments",
    "users",
    "sessions",
    "login_attempts",
    "app_settings",
    "api_keys",
    "api_key_rate_limits",
    "notifications",
    "notification_reads",
]


def sqlite_tables(sconn):
    rows = sconn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name").fetchall()
    return [r[0] for r in rows]


def pg_tables(pconn):
    with pconn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        return {r[0] for r in cur.fetchall()}


def main() -> int:
    ap = argparse.ArgumentParser(description="Migrate RAAS Tracker SQLite data to PostgreSQL.")
    ap.add_argument("--sqlite", default="chem_stock.db",
                    help="SQLite source file (use the backup copy, not the live DB).")
    ap.add_argument("--dsn", default=None,
                    help="PostgreSQL DSN (default: DATABASE_URL env).")
    ap.add_argument("--wipe", action="store_true",
                    help="Drop all public tables on the target before loading.")
    args = ap.parse_args()

    dsn = args.dsn or os.getenv("DATABASE_URL")
    if not dsn:
        print("ERROR: set DATABASE_URL env (or --dsn). Never commit it.", flush=True)
        return 2
    if not os.path.exists(args.sqlite):
        print(f"ERROR: sqlite file not found: {args.sqlite}", flush=True)
        return 2

    sconn = sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True)
    pconn = psycopg.connect(dsn)

    existing = pg_tables(pconn)
    if existing and not args.wipe:
        print(f"ERROR: target has {len(existing)} tables; re-run with --wipe.", flush=True)
        return 2
    if args.wipe and existing:
        with pconn.cursor() as cur:
            quoted = ", ".join(f'"{t}"' for t in sorted(existing))
            cur.execute(f"DROP TABLE {quoted} CASCADE")
        pconn.commit()
        print(f"wiped {len(existing)} existing tables", flush=True)

    # Empty schema first (DDL only, no seeds): explicit source ids must
    # not collide with auto-seeded rows.
    _create_tables(pconn)
    pconn.commit()
    print("schema ready (no seeds yet)", flush=True)

    stables = sqlite_tables(sconn)
    missing = [t for t in TABLE_ORDER if t not in stables]
    if missing:
        print(f"WARNING: source lacks tables (skipped): {missing}", flush=True)

    counts = {}
    with pconn.cursor() as cur:
        for table in TABLE_ORDER:
            if table not in stables:
                continue
            cols = [r[1] for r in sconn.execute(f'PRAGMA table_info("{table}")')]
            placeholders = ", ".join(["%s"] * len(cols))
            quoted_cols = ", ".join(f'"{c}"' for c in cols)
            rows = sconn.execute(f'SELECT * FROM "{table}"').fetchall()
            if rows:
                cur.executemany(
                    f'INSERT INTO "{table}" ({quoted_cols}) VALUES ({placeholders})',
                    rows,
                )
            counts[table] = len(rows)
    pconn.commit()
    print(f"loaded {sum(counts.values())} rows across {len(counts)} tables", flush=True)

    # App seeds fill gaps without touching migrated rows (ON CONFLICT DO
    # NOTHING); also refreshes the schema signature. Seeds run after the
    # load so explicit source ids never collide with generated ones.
    pconn.close()
    pconn = get_connection(dsn)
    print("seeds ensured", flush=True)

    # Fix identity sequences (skip tables without an id sequence).
    # Empty tables restart at 1 (setval(..., 1, false)); others continue
    # after their current MAX(id).
    with pconn.cursor() as cur:
        for table in TABLE_ORDER:
            cur.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = %s AND column_name = 'id'",
                (table,))
            if not cur.fetchone():
                continue
            cur.execute("SELECT pg_get_serial_sequence(%s, 'id')", (table,))
            seq = cur.fetchone()[0]
            if not seq:
                continue
            cur.execute(f'SELECT COALESCE(MAX(id), 0) FROM "{table}"')
            max_id = cur.fetchone()[0]
            if max_id:
                cur.execute("SELECT setval(%s, %s)", (seq, max_id))
            else:
                cur.execute("SELECT setval(%s, 1, false)", (seq,))
    pconn.commit()
    print("sequences reset", flush=True)

    # Verify: every table must match the source count. app_settings is a
    # superset check: the app stamps its schema-signature key on top.
    ok = True
    with pconn.cursor() as cur:
        for table in TABLE_ORDER:
            if table not in stables:
                continue
            if table == "app_settings":
                skeys = {r[0] for r in sconn.execute('SELECT key FROM app_settings')}
                cur.execute('SELECT key FROM app_settings')
                pkeys = {r[0] for r in cur.fetchall()}
                if not skeys <= pkeys:
                    print(f"MISMATCH app_settings keys: missing {skeys - pkeys}", flush=True)
                    ok = False
                continue
            cur.execute(f'SELECT COUNT(*) FROM "{table}"')
            pg_n = cur.fetchone()[0]
            if pg_n != counts[table]:
                print(f"MISMATCH {table}: sqlite={counts[table]} pg={pg_n}", flush=True)
                ok = False
    # Spot checks (metadata only).
    with pconn.cursor() as cur:
        users = [r[0] for r in cur.execute("SELECT username FROM users ORDER BY id")]
        pis = [r[0] for r in cur.execute("SELECT pi_number FROM sales ORDER BY id")]
        settings = [r[0] for r in cur.execute("SELECT key FROM app_settings ORDER BY key")]
    print(f"users: {users}", flush=True)
    print(f"sales PIs: {pis}", flush=True)
    print(f"app_settings keys: {settings}", flush=True)

    sconn.close()
    pconn.close()
    print("VERIFY " + ("OK" if ok else "FAILED"), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
