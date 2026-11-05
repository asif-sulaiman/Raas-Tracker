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


# --------------------------------------------------------------------------- #
# HTTP surface
# --------------------------------------------------------------------------- #
def _invoiced_line(db, pi="PI-WS-HTTP", quantity=450, unit_price=1.44, invoiced=400):
    """The screenshot: one 450 KG line, 400 of it billed, so 50 KG stays provisional."""
    sid, item_id = _line(db, pi=pi, quantity=quantity, unit_price=unit_price)
    amount = round(invoiced * unit_price, 2)
    inv_id = db.execute(
        "INSERT INTO invoices (sale_id, invoice_number, status, amount) "
        "VALUES (%s, %s, 'planned', 0) RETURNING id", (sid, f"INV-{pi}")).fetchone()[0]
    db.execute(
        """INSERT INTO invoice_items (invoice_id, sale_item_id, product_name,
              unit, quantity, unit_price, line_total)
           VALUES (%s, %s, 'A', 'KG', %s, %s, %s)""",
        (inv_id, item_id, invoiced, unit_price, amount))
    db.execute("UPDATE invoices SET amount = %s WHERE id = %s", (amount, inv_id))
    db.execute("INSERT INTO sale_payments (sale_id, payment_date, payment_amount) "
               "VALUES (%s, '2026-02-01', %s)", (sid, amount))
    db.commit()
    return sid, item_id


def _figures(admin_client):
    """(provisional rows, kpi triple) exactly as the Commercial tab reads them."""
    rows = admin_client.get("/api/reports/live").get_json()
    periods = admin_client.get(
        "/api/reports/live/summary?group_by=none").get_json()["periods"]
    kpis = periods[0]["kpis"]
    return ([r for r in rows if r["is_provisional"]],
            (kpis["gross_sales"], kpis["received"], kpis["due"]))


def test_anonymous_is_refused(db):
    c = flask_app.app.test_client()
    assert c.put("/api/sales/1/items/1/wont-ship",
                 json={"wont_ship": True}).status_code == 401


def test_the_mark_is_an_admin_write(admin_client, user_client, db):
    sid, item_id = _line(db)
    r = user_client.put(f"/api/sales/{sid}/items/{item_id}/wont-ship",
                        json={"wont_ship": True})
    assert r.status_code == 403
    assert r.get_json()["error"] == "admin required"
    assert _stored(db, item_id) == 0

    assert admin_client.put(f"/api/sales/{sid}/items/{item_id}/wont-ship",
                            json={"wont_ship": True}).status_code == 200
    assert _stored(db, item_id) == 1


def test_the_flag_is_required_rather_than_assumed(admin_client, db):
    """An empty body must not read as "clear the mark".

    The field has no default, so a client that forgets it is told so instead of
    quietly putting an unshipped balance back into the figures.
    """
    sid, item_id = _line(db)
    set_sale_item_wont_ship(db, sid, item_id, 1)

    r = admin_client.put(f"/api/sales/{sid}/items/{item_id}/wont-ship", json={})
    assert r.status_code == 400
    assert [d["field"] for d in r.get_json()["details"]] == ["wont_ship"]
    assert _stored(db, item_id) == 1


def test_a_line_of_another_sale_answers_404(admin_client, db):
    owner_sid, item_id = _line(db, pi="PI-WS-OWNER")
    other_sid, _ = _line(db, pi="PI-WS-OTHER")

    r = admin_client.put(f"/api/sales/{other_sid}/items/{item_id}/wont-ship",
                         json={"wont_ship": True})
    assert r.status_code == 404
    assert r.get_json()["error"] == "Item not found"
    assert _stored(db, item_id) == 0
    assert owner_sid != other_sid


def test_the_mark_moves_the_live_figures_and_back(admin_client, db):
    """One row action, end to end: the balance leaves the money and returns.

    This is the request as the user made it — the 50 KG remainder of PI
    12322332 held $648 gross and $72 due for a balance that may never ship.
    """
    sid, item_id = _invoiced_line(db)

    provisional, kpis = _figures(admin_client)
    assert [(r["pi_number"], r["quantity"], r["sale_item_id"])
            for r in provisional] == [("PI-WS-HTTP", 50.0, item_id)]
    assert kpis == (648, 576, 72)

    r = admin_client.put(f"/api/sales/{sid}/items/{item_id}/wont-ship",
                         json={"wont_ship": True})
    assert r.status_code == 200
    assert r.get_json() == {"message": "Item updated", "wont_ship": True}

    provisional, kpis = _figures(admin_client)
    assert provisional == []
    assert kpis == (576, 576, 0)

    # The editor is the only un-mark path, so it must show the stored mark and
    # put the row back when it is cleared.
    assert admin_client.get(f"/api/sales/{sid}").get_json()["items"][0]["wont_ship"] is True
    assert admin_client.put(f"/api/sales/{sid}/items/{item_id}/wont-ship",
                            json={"wont_ship": False}).status_code == 200
    provisional, kpis = _figures(admin_client)
    assert [r["sale_item_id"] for r in provisional] == [item_id]
    assert kpis == (648, 576, 72)
