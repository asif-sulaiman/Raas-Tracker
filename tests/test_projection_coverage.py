"""Slice 3 RED: coverage, completeness and the consistency of one forecast.

A purchasing number is only as good as the truth attached to it. These cases pin
the parts of the contract that a correct-looking total can still get wrong:

* **Coverage travels with the totals.** Open PIs that no one has planned yet are
  listed, an active plan that no longer matches the PI is surfaced as incomplete,
  and neither is allowed to be presented as a complete answer.
* **Filtering must not lose demand.** A material registered outward-only stays
  out of Chemical Stock, but its requirement still has to appear — in
  `unstocked_requirements` — or the buying list silently under-orders.
* **No N+1.** The statement count is a property of the query shape, not of how
  many PIs happen to be open.
* **One snapshot.** Everything — stock, PI progress, formulas, production
  history — is read from one REPEATABLE READ transaction, so a concurrent
  production plus stock change cannot be woven into a forecast that describes
  no instant that ever existed. A session-level `SET` is not available here
  (TRANSACTION pooler), and the projection may not commit the caller's work.
* **ETag over the complete response.** If coverage changes while the numbers do
  not, revalidation must not hand back the stale "everything is planned" answer.

`raas_tracker/pi_demos.py` and the inventory projection do not exist yet, so
every case fails — that is the correct RED for Task 5b-1.
"""
import json

from test_pi_demos import (  # noqa: F401  (shared Slice 3 test scaffolding)
    _chem, _coverage, _create_ok, _line_ids, _plan, _pi, _product, _produce,
    _invoice, _rows, _svc,
)

from raas_tracker import db as dbmod


class CountingConn:
    """Proxy that counts statements routed through the connection it wraps.

    Only ``execute``/``executemany`` are counted: a helper that opens a real
    cursor or a second connection is invisible here, which is why the test
    asserts "does not grow" rather than trusting a bare zero.
    """

    def __init__(self, conn):
        self._conn = conn
        self.statements = []

    def execute(self, query, params=None, **kwargs):
        self.statements.append(" ".join(str(query).split()))
        if params is None:
            return self._conn.execute(query, **kwargs)
        return self._conn.execute(query, params, **kwargs)

    def executemany(self, query, seq):
        self.statements.append(" ".join(str(query).split()))
        return self._conn.executemany(query, seq)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _inventory(admin_client):
    response = admin_client.get("/api/chemicals/inventory")
    assert response.status_code == 200, response.get_json()
    return response


def _planned_chem(admin_client, db, pi, chem, ordered=100, percentage=60):
    """One PI with one activated formulation of `chem` at `percentage`."""
    sid = _pi(db, pi, [("A", ordered, "KG")])
    _create_ok(admin_client, sid)
    assert _plan(admin_client, db, sid,
                 [_product("A", [(chem, percentage)])]).status_code == 200
    return sid


# --------------------------------------------------------------------------- #
# 1-3 — the three coverage states a buyer must be able to tell apart
# --------------------------------------------------------------------------- #
def test_open_pis_without_a_plan_are_reported_alongside_the_totals(admin_client, db):
    """Unplanned demand is listed, not folded into the total or dropped.

    The numbers cover active plans; the PI list says which open PIs are not in
    them, so nobody reads a short buying list as a complete one.

    The state word is part of that sentence. It used to read ``complete`` here,
    on the argument that "complete" described only the arithmetic over the plans
    the forecast claims to cover. That reading is the one this file rejects: the
    per-chemical zeros beside an unplanned PI mean "nothing owed *within the
    activated plans*", which is not something a buyer can act on, and the word
    ``complete`` next to a non-empty ``open_pis_without_plan`` is precisely how a
    400 KG of unplanned demand gets read as no demand at all. Unplanned PIs make
    the answer ``incomplete``; the zeros stay, because an unplanned PI has no
    formulation to attribute them to.
    """
    _chem(db, "Chem Cov", qty=0)
    planned = _planned_chem(admin_client, db, "PI-COV-PLANNED", "Chem Cov", ordered=100)
    unplanned = _pi(db, "PI-COV-UNPLANNED", [("Later", 400, "KG")])

    coverage = _coverage(_svc().project_purchasing(db))
    assert coverage["state"] == "incomplete", coverage
    assert unplanned in {row["sale_id"] for row in coverage["open_pis_without_plan"]}, coverage
    assert planned not in {row["sale_id"] for row in coverage["open_pis_without_plan"]}, coverage

    body = _inventory(admin_client).get_json()
    assert body["coverage"]["state"] == "incomplete", body["coverage"]
    text = json.dumps(body).lower()
    assert "pi-cov-unplanned" in text, text


def test_complete_is_claimed_only_when_nothing_is_unplanned_or_incomplete(admin_client, db):
    """The one data shape allowed to say ``complete``: nothing left outside.

    Every other state has its own word — ``no_active_plans`` when nothing is
    activated at all, ``incomplete`` when any open PI is unplanned or any active
    plan no longer matches its PI — so ``complete`` is a claim a buyer can act on
    rather than the absence of a problem being reported. Planning the late PI is
    what restores the claim; the label follows the lists, never the totals.
    """
    _chem(db, "Chem State", qty=0)
    _planned_chem(admin_client, db, "PI-COV-STATE", "Chem State")

    coverage = _coverage(_svc().project_purchasing(db))
    assert coverage["state"] == "complete", coverage
    assert coverage["open_pis_without_plan"] == [], coverage
    assert coverage["incomplete_plans"] == [], coverage

    late = _pi(db, "PI-COV-STATE-LATE", [("Later", 50, "KG")])
    coverage = _coverage(_svc().project_purchasing(db))
    assert coverage["state"] == "incomplete", coverage
    assert late in {row["sale_id"] for row in coverage["open_pis_without_plan"]}, coverage

    # The projection above armed a REPEATABLE READ snapshot on `db`; the demo
    # below is committed on the client's own connection, so `db` must end that
    # snapshot before it can read (or re-project) the newly planned PI.
    db.commit()
    _create_ok(admin_client, late)
    assert _plan(admin_client, db, late,
                 [_product("Later", [("Chem State", 60)])]).status_code == 200
    coverage = _coverage(_svc().project_purchasing(db))
    assert coverage["state"] == "complete", coverage
    assert coverage["open_pis_without_plan"] == [], coverage


def test_an_incomplete_active_plan_is_surfaced_instead_of_a_partial_total(admin_client, db):
    """A product added to the PI after activation makes the active plan stale.

    Silently summing only the formulated products would publish a purchasing
    total that omits real demand and still reads as complete.
    """
    _chem(db, "Chem Grow", qty=0)
    sid = _planned_chem(admin_client, db, "PI-COV-GROW", "Chem Grow", ordered=100)
    assert _coverage(_svc().project_purchasing(db))["state"] == "complete"

    db.execute("INSERT INTO sale_items (sale_id, product_name, quantity, unit_price, unit) "
               "VALUES (%s, 'Added Late', 80, 1, 'KG')", (sid,))
    db.commit()

    coverage = _coverage(_svc().project_purchasing(db))
    assert coverage["state"] == "incomplete", coverage
    assert sid in {row["sale_id"] for row in coverage["incomplete_plans"]}, coverage
    body = _inventory(admin_client).get_json()
    assert json.dumps(body["coverage"]).lower().count("added late") >= 1, body["coverage"]


def test_an_unstocked_material_stays_out_of_stock_but_not_out_of_demand(admin_client, db):
    """Outward-only identities are excluded from Chemical Stock by design; the
    planning summary must still carry their requirement so filtering never
    loses demand.
    """
    hidden = _chem(db, "Outward Only", qty=0)
    db.execute("UPDATE chemicals SET in_house_registered = FALSE WHERE id = %s", (hidden,))
    db.commit()
    _planned_chem(admin_client, db, "PI-COV-HIDDEN", "Outward Only", ordered=100)

    body = _inventory(admin_client).get_json()
    assert "outward only" not in {m["name"].strip().lower() for m in body["materials"]}, body
    summary = {row["name"].strip().lower(): row for row in body["unstocked_requirements"]}
    assert "outward only" in summary, body["unstocked_requirements"]
    assert summary["outward only"]["required_quantity"] == 60.0, summary
    assert summary["outward only"]["projected_quantity"] == 60.0, summary


# --------------------------------------------------------------------------- #
# 4 — the query shape
# --------------------------------------------------------------------------- #
def test_the_forecast_query_count_does_not_grow_with_the_number_of_pis(admin_client, db):
    """Bounded bulk queries: one more PI must not mean one more statement.

    A per-PI loop is invisible on a demo database and ruinous on the live one,
    so the count is measured, not assumed.
    """
    _chem(db, "Chem Bulk Cov", qty=10)
    _planned_chem(admin_client, db, "PI-COV-Q1", "Chem Bulk Cov")

    one = CountingConn(db)
    assert _rows(_svc().project_purchasing(one))["chem bulk cov"]["required_quantity"] == 60.0
    first_statements = len(one.statements)

    _planned_chem(admin_client, db, "PI-COV-Q2", "Chem Bulk Cov")
    _planned_chem(admin_client, db, "PI-COV-Q3", "Chem Bulk Cov")

    many = CountingConn(db)
    rows = _rows(_svc().project_purchasing(many))
    assert rows["chem bulk cov"]["required_quantity"] == 180.0, rows
    assert len(many.statements) == first_statements, (many.statements, first_statements)


# --------------------------------------------------------------------------- #
# 5 — revalidation must not serve a stale "complete"
# --------------------------------------------------------------------------- #
def test_changed_coverage_changes_the_body_and_the_etag_even_when_totals_match(admin_client, db):
    """Identical numbers, different truth: an ETag over the totals alone would
    answer 304 and keep a "no unplanned demand" forecast cached.
    """
    _chem(db, "Chem Etag", qty=0)
    _planned_chem(admin_client, db, "PI-COV-ETAG", "Chem Etag", ordered=100)

    first = _inventory(admin_client)
    assert first.get_json()["coverage"]["state"] == "complete", first.get_json()["coverage"]
    etag = first.headers.get("ETag")
    assert etag, dict(first.headers)
    assert _inventory(admin_client).headers.get("ETag") == etag

    _pi(db, "PI-COV-ETAG-LATE", [("Unplanned", 500, "KG")])

    changed = _inventory(admin_client)
    body = changed.get_json()
    # Same totals, different truth — and the truth now moves the state word too,
    # which is the part a totals-only ETag would have left cached as "complete".
    assert body["coverage"]["state"] == "incomplete", body["coverage"]
    assert "pi-cov-etag-late" in json.dumps(body).lower(), body["coverage"]
    assert changed.headers.get("ETag") != etag, (changed.headers.get("ETag"), etag)
    assert changed.headers.get("Cache-Control", "").startswith("private"), changed.headers


# --------------------------------------------------------------------------- #
# 6 — one snapshot, one transaction, no session state
# --------------------------------------------------------------------------- #
def test_a_concurrent_production_and_stock_update_cannot_weave_two_snapshots(
        pg_dsn, admin_client, db):
    """The forecast is read once, under REPEATABLE READ, from the caller's
    transaction — so two committed changes landing mid-read cannot produce a
    figure that describes an instant that never existed.

    Ordered 300 with 200 in stock projects 100. A concurrent commit that records
    50 produced and buys 100 more must move the whole forecast together
    (required 250, stock 300, projected 0); a mixed read would show required 250
    against the old 200 of stock, which is 50 — a number that was never true.
    """
    pi_demos = _svc()
    chem_id = _chem(db, "Chem Snapshot", qty=200)
    sid = _pi(db, "PI-COV-SNAP", [("A", 300, "KG")])
    item_id = _line_ids(db, sid)["A"]
    _create_ok(admin_client, sid)
    assert _plan(admin_client, db, sid,
                 [_product("A", [("Chem Snapshot", 100)], water=0)]).status_code == 200

    snapshot = dbmod.get_connection(pg_dsn)
    writer = dbmod.get_connection(pg_dsn)
    try:
        snapshot.commit()          # start the projection from a fresh transaction
        counted = CountingConn(snapshot)
        first = _rows(pi_demos.project_purchasing(counted))["chem snapshot"]
        assert first["required_quantity"] == 300.0, first
        assert first["projected_quantity"] == 100.0, first
        baseline = len(counted.statements)

        invoice = _invoice(writer, sid, "INV-COV-SNAP", [(item_id, "A", "KG", 50)])
        _produce(writer, sid, item_id, "A", "KG", 50, invoice)
        writer.execute("UPDATE chemicals SET current_qty = 300 WHERE id = %s", (chem_id,))
        writer.commit()

        again = _rows(pi_demos.project_purchasing(counted))["chem snapshot"]
        assert again == first, (again, first)
        assert len(counted.statements) == 2 * baseline, counted.statements

        snapshot.commit()          # a new snapshot sees both changes together
        moved = _rows(pi_demos.project_purchasing(snapshot))["chem snapshot"]
        assert moved["required_quantity"] == 250.0, moved
        assert moved["projected_quantity"] == 0.0, moved
    finally:
        snapshot.close()
        writer.close()

    # Transaction-scoped only: a session-level SET would leak onto the next
    # client of the TRANSACTION pooler, and a projection may not commit the
    # caller's work.
    statements = " ".join(counted.statements).lower()
    for statement in counted.statements:
        head = statement.strip().lower()
        assert not head.startswith("commit"), statement
        assert not head.startswith("rollback"), statement
    if "transaction_isolation" in statements:
        assert "set local" in statements, statements
        assert "set transaction_isolation" not in statements, statements
