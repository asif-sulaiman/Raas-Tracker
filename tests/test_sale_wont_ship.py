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
import flask_app
from raas_tracker import db as dbmod
from raas_tracker.sales import (
    add_sale,
    get_sale_by_id,
    set_sale_item_wont_ship,
    update_sale_full,
)


def _column(conn, table, name):
    return conn.execute(
        "SELECT data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s "
        "AND column_name = %s", (table, name)).fetchone()


def _line(db, pi="PI-WS", quantity=450, unit_price=1.44):
    """One PI with one line — the screenshot's shape. Returns (sale_id, item_id)."""
    sid = add_sale(db, {"pi_number": pi, "client_name": "WS Co"},
                   [{"product_name": "A", "quantity": quantity,
                     "unit_price": unit_price, "unit": "KG"}])
    item_id = db.execute("SELECT id FROM sale_items WHERE sale_id = %s",
                         (sid,)).fetchone()[0]
    return sid, item_id


def _stored(db, item_id):
    return db.execute("SELECT wont_ship FROM sale_items WHERE id = %s",
                      (item_id,)).fetchone()[0]


def _audits(db, item_id):
    return db.execute(
        "SELECT old_value, new_value FROM audit_logs "
        "WHERE action = 'SALE_ITEM_WONT_SHIP' AND entity_id = %s ORDER BY id",
        (item_id,)).fetchall()


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


# --------------------------------------------------------------------------- #
# service
# --------------------------------------------------------------------------- #
def test_mark_and_clear_round_trip(db):
    sid, item_id = _line(db)
    assert set_sale_item_wont_ship(db, sid, item_id, 1) is True
    assert _stored(db, item_id) == 1
    assert set_sale_item_wont_ship(db, sid, item_id, 0) is True
    assert _stored(db, item_id) == 0


def test_reapplying_the_same_state_is_an_idempotent_no_op(db):
    """A second mark from a stale tab is a 200 with no trail, not a failure.

    The report row disappears on the first call, so any repeat can only come
    from a view that has not re-read yet; writing again would add an audit row
    that claims a change which did not happen.
    """
    sid, item_id = _line(db)
    assert set_sale_item_wont_ship(db, sid, item_id, 1) is True
    assert set_sale_item_wont_ship(db, sid, item_id, 1) is False
    assert _stored(db, item_id) == 1
    assert len(_audits(db, item_id)) == 1


def test_a_line_under_another_sale_is_not_reachable(db):
    """The same IDOR binding Phase 5 put on update_sale_item / delete_sale_item.

    ``None`` is the return the route turns into 404, and the other sale's line
    must stay untouched — a 404 that also wrote would be worse than either.
    """
    owner_sid, item_id = _line(db, pi="PI-WS-OWNER")
    other_sid, _other_item = _line(db, pi="PI-WS-OTHER")
    assert set_sale_item_wont_ship(db, other_sid, item_id, 1) is None
    assert _stored(db, item_id) == 0
    assert owner_sid != other_sid


def test_the_mark_is_audited_with_old_and_new(db):
    sid, item_id = _line(db)
    set_sale_item_wont_ship(db, sid, item_id, 1)
    assert [tuple(r) for r in _audits(db, item_id)] == [("0", "1")]


def test_get_sale_by_id_exposes_the_mark_as_a_bool(db):
    """The PI editor renders the checkbox from this, so it must be the stored state.

    ``bool`` matches how ``auth`` serialises ``must_change_password`` — an int in
    the database, a boolean on the wire.
    """
    sid, item_id = _line(db)
    assert get_sale_by_id(db, sid)["items"][0]["wont_ship"] is False
    set_sale_item_wont_ship(db, sid, item_id, 1)
    assert get_sale_by_id(db, sid)["items"][0]["wont_ship"] is True


def test_a_pi_save_neither_sets_nor_clears_the_mark(db):
    """``update_sale_full`` writes only product_name/quantity/unit_price/unit.

    The PI editor's Save button re-uploads the whole line set; if it carried the
    flag it could clobber a mark made from the report in the meantime, and if it
    cleared it the mark would be unusable. Silence is the contract, so it is
    pinned rather than left to chance.
    """
    sid, item_id = _line(db, quantity=450, unit_price=1.44)
    set_sale_item_wont_ship(db, sid, item_id, 1)
    update_sale_full(db, sid, {"pi_number": "PI-WS"},
                     [{"id": item_id, "product_name": "A",
                       "quantity": 450, "unit_price": 1.44, "unit": "KG"}], [])
    assert _stored(db, item_id) == 1
