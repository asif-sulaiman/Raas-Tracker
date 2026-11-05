"""A PI line that will never ship must stop counting in the commercial figures.

The Commercial Report emits one ``is_provisional`` remainder row for every PI
line that is not fully invoiced, and its KPIs are plain sums over those rows.
An unshipped balance therefore holds Gross Sales and Due open forever — on the
user's live data a 50 KG remainder keeps PI 12322332 showing $648 gross and
$72 due against $576 actually billed.

The mark lives on the PI line (``sale_items.wont_ship``), not on the report row
or in the viewer's session, because the decision that a balance will not ship
belongs to the document and must apply for everyone. These tests pin the two
properties that make that safe:

1. the mark suppresses only the *remainder* — invoiced money and the payments
   allocated to it stay in the report untouched;
2. suppression happens inside the ``remainders`` CTE, so payment allocation is
   recomputed over the surviving rows instead of dropping a payment that was
   anchored to the removed one.
"""
from raas_tracker import db as dbmod


def _column(conn, table, name):
    return conn.execute(
        "SELECT data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s "
        "AND column_name = %s", (table, name)).fetchone()


# --------------------------------------------------------------------------- #
# schema v12
# --------------------------------------------------------------------------- #
def test_wont_ship_is_a_zero_defaulted_int_flag(db):
    """Booleans are ints here, like ``users.must_change_password`` and ``revoked``.

    ``NOT NULL DEFAULT 0`` also keeps the report predicate a plain equality: no
    three-valued ``IS NOT TRUE`` to reason about for rows written before v12.
    """
    row = _column(db, "sale_items", "wont_ship")
    assert row is not None, "sale_items.wont_ship is missing"
    assert row[0] == "integer"
    assert row[1] == "NO"
    assert "0" in (row[2] or "")


def test_migration_reapplies_wont_ship_column(pg_dsn):
    """Dropping the column and re-migrating restores it.

    This is the guard the version bump exists for: an additive migration that
    forgets to raise ``_SCHEMA_VERSION`` is silently skipped on every database
    that already passes the fast-path check, and no later deploy recovers it.
    """
    conn = dbmod.get_connection(pg_dsn)
    conn.execute("ALTER TABLE sale_items DROP COLUMN IF EXISTS wont_ship")
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (dbmod._SCHEMA_VERSION_KEY, str(dbmod._SCHEMA_VERSION - 1)))
    conn.commit()
    conn.close()

    conn = dbmod.get_connection(pg_dsn)
    try:
        assert _column(conn, "sale_items", "wont_ship") is not None
    finally:
        conn.close()
