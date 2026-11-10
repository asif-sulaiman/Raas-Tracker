"""A PI line that will never ship must stop counting in the commercial figures.

The Commercial Report emits one ``is_provisional`` remainder row for every PI
line that is not fully invoiced, and its KPIs are plain sums over those rows.
An unshipped balance therefore holds Gross Sales and Due open forever — on the
user's live data a 50 KG remainder keeps PI 12322332 showing $648 gross and
$72 due against $576 actually billed.

The mark lives on the PI line (``sale_items.wont_ship``), not on the report row
or in the viewer's session, because the decision that a balance will not ship
belongs to the document and must apply for everyone. The report alone was only
half the ask: what a PI says it is worth is not the account, what was invoiced
is, so a retired balance leaves every figure the app calls an account — while an
un-invoiced balance that has *not* been retired stays in them and keeps its
Provisional row, because it may still be invoiced or shipped later.

These tests pin the properties that make that safe:

1. the mark suppresses only the *remainder* — invoiced money and the payments
   allocated to it stay in the report untouched;
2. suppression happens inside the ``remainders`` CTE, so payment allocation is
   recomputed over the surviving rows instead of dropping a payment that was
   anchored to the removed one;
3. the board, the stage tiles and the pipeline CSV net the same amount from one
   shared definition, so no two figures ever disagree about one PI;
4. the ship-time "will you invoice the rest?" nag and the daily maturity bell
   both go quiet on a retired balance — a question that can never be answered
   is not a reminder.
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


# --------------------------------------------------------------------------- #
# the pipeline figures
#
# The report was only half the ask. The user's rule: "PI কত টাকার তা হিসাবের
# মুল না" — what a PI is worth is not the account, what was invoiced is. So a
# retired balance leaves *every* figure the app calls an account, and an
# un-invoiced balance that has not been retired stays in them and keeps its
# Provisional row, because it may still be invoiced or shipped later.
# --------------------------------------------------------------------------- #
def _board(admin_client, sid):
    """One sale exactly as the pipeline board renders it."""
    return next(s for s in admin_client.get("/api/sales").get_json()["sales"]
                if s["id"] == sid)


def test_the_board_nets_a_retired_balance_out(admin_client, db):
    """Value and Balance become the accounted money, not the document total.

    PI 12322332 on the user's screen: 450 KG @ 1.44 = $648 with $576 billed.
    Marking the remainder puts the $72 back the way every other figure reads
    it, and clearing it takes the mark — never the PI line — so $648 returns.
    """
    sid, item_id = _invoiced_line(db, pi="PI-WS-BOARD")

    row = _board(admin_client, sid)
    assert (row["total_value"], row["balance"], row["item_count"]) == (648, 72, 1)

    set_sale_item_wont_ship(db, sid, item_id, 1)
    row = _board(admin_client, sid)
    assert (row["total_value"], row["balance"], row["item_count"]) == (576, 0, 1)

    set_sale_item_wont_ship(db, sid, item_id, 0)
    assert _board(admin_client, sid)["total_value"] == 648


def test_the_stage_tiles_net_the_retired_balance(admin_client, db):
    """Home's pipeline total is the same sum, so it nets the same way.

    The sale still counts: retiring its remainder is a money decision, not a
    deletion, so the stage keeps its row in ``count``.
    """
    sid, item_id = _invoiced_line(db, pi="PI-WS-SUMMARY")

    before = admin_client.get("/api/sales/summary").get_json()
    assert (before["pi_issued"]["count"], before["pi_issued"]["value"],
            before["total_pipeline_value"]) == (1, 648, 648)

    set_sale_item_wont_ship(db, sid, item_id, 1)
    after = admin_client.get("/api/sales/summary").get_json()
    assert (after["pi_issued"]["count"], after["pi_issued"]["value"],
            after["total_pipeline_value"]) == (1, 576, 576)


def test_the_pipeline_csv_agrees_with_the_board(admin_client, db):
    """The export is the board on paper — a figure the board nets must be netted here too."""
    import csv
    import io

    sid, item_id = _invoiced_line(db, pi="PI-WS-CSV")

    def row():
        text = admin_client.get("/api/sales/export").get_data(as_text=True)
        return next(r for r in csv.DictReader(io.StringIO(text)) if r["id"] == str(sid))

    assert (row()["total_value_usd"], row()["balance_usd"]) == ("648.0", "72.0")
    set_sale_item_wont_ship(db, sid, item_id, 1)
    assert (row()["total_value_usd"], row()["balance_usd"]) == ("576.0", "0.0")


def test_a_retired_remainder_stops_the_ship_time_nag(admin_client, db):
    """``has_uninvoiced_remainder`` asks "will you invoice the rest?" after every ship.

    On a retired balance that question is un-answerable — the rest is never
    coming — so the flag must read the PI the same way the money does. One
    predicate in the query that already joins invoiced quantity per line takes
    the marked line out of both sums at once.
    """
    sid, item_id = _invoiced_line(db, pi="PI-WS-NAG")
    assert _board(admin_client, sid)["has_uninvoiced_remainder"] is True

    set_sale_item_wont_ship(db, sid, item_id, 1)
    assert _board(admin_client, sid)["has_uninvoiced_remainder"] is False

    set_sale_item_wont_ship(db, sid, item_id, 0)
    assert _board(admin_client, sid)["has_uninvoiced_remainder"] is True


def test_a_marked_line_never_counts_above_the_pi_document(admin_client, db):
    """The retired part is capped at ordered quantity — ``LEAST`` is not decoration.

    500 KG billed against a 450 KG PI: the accounted value of a marked line is
    its billed quantity, so an over-invoiced line must not turn the mark into
    a raise. $720 would be more than the PI ever said.
    """
    sid, item_id = _invoiced_line(db, pi="PI-WS-OVER", invoiced=500)
    assert _board(admin_client, sid)["total_value"] == 648

    set_sale_item_wont_ship(db, sid, item_id, 1)
    assert _board(admin_client, sid)["total_value"] == 648


def test_a_retired_line_under_a_legacy_payment_reads_negative(admin_client, db):
    """The accepted corner, pinned rather than clamped.

    A sale-level payment, no invoice, and its only line retired: the PI now
    accounts for $0 while $576 is still held, so Balance shows -$576.
    ``GREATEST(..., 0)`` would hide this — and would hide a genuine
    overpayment in the same column, which is money the operator needs to see.
    """
    sid, item_id = _line(db, pi="PI-WS-LEGACY")
    db.execute("INSERT INTO sale_payments (sale_id, payment_date, payment_amount) "
               "VALUES (%s, '2026-02-01', 576)", (sid,))
    db.commit()
    row = _board(admin_client, sid)
    assert (row["total_value"], row["balance"]) == (648, 72)

    set_sale_item_wont_ship(db, sid, item_id, 1)
    row = _board(admin_client, sid)
    assert (row["total_value"], row["balance"]) == (0, -576)


def test_a_retired_legacy_sale_raises_no_maturity_reminder(client, monkeypatch, db):
    """A bell that can never be cleared is a bug, not a reminder.

    The scan is limited to sales with no invoice, so no billed quantity exists
    here and the retired line nets to nothing outright — a payment can never
    catch up to a balance the admin retired, so the sale must leave the check.
    """
    monkeypatch.setenv("CRON_SECRET", "test-secret")
    headers = {"Authorization": "Bearer test-secret"}
    from datetime import date as _date

    sid = add_sale(db, {"pi_number": "PI-WS-DUE", "client_name": "WS Co",
                        "pi_date": "2026-01-10", "maturity_date": _date.today().isoformat()},
                   [{"product_name": "A", "quantity": 100, "unit_price": 50,
                     "unit": "KG"}])
    item_id = db.execute("SELECT id FROM sale_items WHERE sale_id = %s",
                         (sid,)).fetchone()[0]

    assert client.post("/api/cron/maturity-check",
                       headers=headers).get_json()["checked"] == 1

    set_sale_item_wont_ship(db, sid, item_id, 1)
    body = client.post("/api/cron/maturity-check", headers=headers).get_json()
    assert (body["checked"], body["notified"]) == (0, 0)


# --------------------------------------------------------------------------- #
# Slice 3: the retired remainder has to survive into planning and into the
# purchasing projection, or the money and the buying list disagree about one PI.
# --------------------------------------------------------------------------- #
def _pi_demos_project(db):
    """Slice 3's read-only net purchasing projection (`Task 5b-2` lands it)."""
    from raas_tracker import pi_demos

    return pi_demos.project_purchasing(db)


def test_a_retired_remainder_still_needs_a_formula_and_projects_the_balance(admin_client, db):
    """450 ordered, 400 billed, 100 produced, then retired: the target is the
    billed 400, so 300 output units still need a formulation and a balance.

    A projection that dropped the marked line wholesale would tell the plant
    there is nothing to buy for a PI that still owes 300 KG.
    """
    from test_pi_demos import _chem, _create_ok, _plan, _product, _produce, _rows

    sid, item_id = _invoiced_line(db, pi="PI-WS-PROJ", quantity=450, invoiced=400)
    invoice = db.execute("SELECT id FROM invoices WHERE sale_id = %s",
                         (sid,)).fetchone()[0]
    _produce(db, sid, item_id, "A", "KG", 100, invoice)
    _chem(db, "Chem WS", qty=0)
    assert set_sale_item_wont_ship(db, sid, item_id, 1) is True

    _create_ok(admin_client, sid)
    unformulated = _plan(admin_client, db, sid, [])
    assert unformulated.status_code == 400, unformulated.get_json()

    activated = _plan(admin_client, db, sid, [_product("A", [("Chem WS", 50)])])
    assert activated.status_code == 200, activated.get_json()
    row = _rows(_pi_demos_project(db))["chem ws"]
    assert row["required_quantity"] == 150.0, row   # 300 remaining units x 50%
    assert row["projected_quantity"] == 150.0, row


def test_a_fully_retired_line_needs_no_formula_and_adds_no_demand(admin_client, db):
    """Retired with nothing billed, the target is `min(450, 0)` = 0: no
    formulation is required, and none of its material enters the buying list.
    """
    from test_pi_demos import _chem, _create_ok, _plan, _required, _rows

    sid, item_id = _line(db, pi="PI-WS-ZERO", quantity=450)
    _chem(db, "Chem Gone", qty=100)
    assert set_sale_item_wont_ship(db, sid, item_id, 1) is True

    _create_ok(admin_client, sid)
    assert _plan(admin_client, db, sid, []).status_code == 200

    rows = _rows(_pi_demos_project(db))
    assert _required(rows, "Chem Gone") == 0.0, rows
    assert db.execute("SELECT COUNT(*) FROM pi_demo_products WHERE scope = 'active'"
                      ).fetchone()[0] == 0
