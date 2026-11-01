"""Delete the demo recipes and runs before the v8 schema migration.

Dry run by default. Applying needs ``--yes`` **and** an existing pg_dump file
named by ``--backup-file``. Stock consumed by the demo runs is restored only
when ``--restore-stock`` is passed; the delta is printed either way.

Typical cutover:

    python scripts/reset_demo_recipes.py                  # preview
    pg_dump "$DATABASE_URL" -Fc -f demo_backup.dump
    python scripts/reset_demo_recipes.py --yes \\
        --backup-file demo_backup.dump --restore-stock

Connect with raw psycopg: importing ``raas_tracker.db.get_connection`` here
would apply the very migration this reset has to run before.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from raas_tracker.demo_reset import (  # noqa: E402  (path set above)
    DEMO_RESET_LOCK_KEY,
    DemoResetRefused,
    reset_demo_recipes,
)


def _print_report(report) -> None:
    print("DEMO RESET APPLIED" if report["apply"] else "DRY RUN - nothing written")
    print(f"schema version: {report['schema_version']}")
    print("rows:")
    for table, count in report["counts"].items():
        print(f"  {table}: {count}")
    print("stock restore delta (per chemical):")
    if not report["stock_delta"]:
        print("  (none)")
    for item in report["stock_delta"]:
        print(f"  {item['chemical_name']}: +{item['quantity']:g} {item['unit']}")
    for item in report["stock_restored"]:
        state = "restored" if item["restored"] else "NOT FOUND - adjust manually"
        print(f"  restore {item['chemical_name']}: {state}")
    for sale in report["sales_updated"]:
        print(f"  sale {sale['sale_id']}: shipment_status={sale['shipment_status']}")
    if report["invoices_reset"]:
        print(f"  invoices reset to planned: {report['invoices_reset']}")
    if report["report_path"]:
        print(f"report: {report['report_path']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Delete demo recipes/runs before the v8 migration.")
    parser.add_argument("--yes", action="store_true",
                        help="apply the reset (default is a dry run)")
    parser.add_argument("--backup-file",
                        help="path to an existing pg_dump (required with --yes)")
    parser.add_argument("--restore-stock", action="store_true",
                        help="add the runs' deducted quantities back to stock")
    parser.add_argument("--report-dir", default=".",
                        help="directory for reset_report_<timestamp>.json")
    args = parser.parse_args(argv)

    if args.yes:
        if not args.backup_file or not os.path.isfile(args.backup_file):
            print("refused: --yes requires --backup-file naming an existing dump",
                  file=sys.stderr)
            return 2
    elif args.restore_stock:
        print("refused: --restore-stock requires --yes", file=sys.stderr)
        return 2

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    conn = psycopg.connect(dsn, prepare_threshold=None)
    try:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (DEMO_RESET_LOCK_KEY,))
        report = reset_demo_recipes(
            conn,
            apply=args.yes,
            restore_stock=args.restore_stock,
            backup_file=args.backup_file,
            report_dir=args.report_dir,
        )
        if args.yes:
            conn.commit()
        else:
            conn.rollback()
        _print_report(report)
        return 0
    except DemoResetRefused as exc:
        conn.rollback()
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - surfaced to the operator
        conn.rollback()
        print(f"reset failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
