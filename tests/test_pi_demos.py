"""Slice 3 RED: PI demo recipes — explicit creation, drafts, and the gate.

A demo recipe is a *planning* formulation for one PI. It is not a production
plan and it never touches stock: the whole point is to answer "what do I still
have to buy" before an LC or an invoice exists, so a demo must be creatable the
moment a PI does, must be savable while incomplete, and must only affect the
forecast once it is activated.

The rules these tests pin, because they are the ones most easily gotten wrong:

* **GET is a read.** It may not create a demo or a formulation row on a miss —
  an auto-created empty demo would silently turn "no plan yet" into "plan with
  nothing in it", which is exactly the state the coverage report has to surface.
* **Two working sets, one counter each.** `draft_revision` moves when a draft
  changes; `active_revision` records *which* draft revision became the live
  snapshot. Incrementing an unrelated counter and calling it a draft revision
  would make every stale-tab save look current.
* **Activation is a gate, not a balance check.** Unbalanced percentages, a
  missing product-unit → chemical-unit conversion, no ingredients, or a zero
  percentage must all be refused, and a refusal must leave the previous active
  rows and activation metadata exactly as they were. A stock shortage is NOT a
  failure here — forecasting shortages is the feature.
* **Auto water snapshots.** A draft may leave water NULL meaning "derive it";
  the active row must store the *resolved* number, because the active scope is
  the operational formula and `chk_pi_demo_active_water` exists to make that so.
* **A won't-ship line is not filtered out of demand.** Its retained target is
  `min(ordered, non-void invoiced)`: ordered 100 / invoiced 60 / produced 20
  still needs a formulation, for the remaining 40 output units. Only a wholly
  retired zero-target product drops out and needs none.

Routes and `raas_tracker/pi_demos.py` do not exist yet: every test here fails
with `ModuleNotFoundError` on the service import or on a 404/405 from Flask.
That is the correct RED for Task 5b-1.
"""
import json

import pytest

from raas_tracker.sales import add_sale, set_sale_item_wont_ship


# --------------------------------------------------------------------------- #
# helpers — defined once, reused by every case
# --------------------------------------------------------------------------- #
def _svc():
    """The Slice 3 service module. Missing until Task 5b-2 lands: that is RED."""
    from raas_tracker import pi_demos
    return pi_demos


def _chem(db, name, unit="KG", qty=0.0):
    ident = db.execute(
        "INSERT INTO chemicals (name, unit, current_qty) VALUES (%s, %s, %s) "
        "RETURNING id", (name, unit, qty)).fetchone()[0]
    # Committed, like every other helper here: an app request runs on its own
    # connection, so an uncommitted chemical is invisible to the draft route
    # that has to resolve its name.
    db.commit()
    return ident


def _convert(db, from_unit, to_unit, factor):
    """A unit path the plan needs, so activation can pass and be revoked later."""
    db.execute("INSERT INTO unit_conversions (from_unit, to_unit, factor) "
               "VALUES (%s, %s, %s) ON CONFLICT (from_unit, to_unit) "
               "DO UPDATE SET factor = EXCLUDED.factor",
               (from_unit, to_unit, factor))
    db.commit()


def _pi(db, pi, lines):
    """One PI at pi_issued. `lines` is [(product_name, quantity, unit)]."""
    sid = add_sale(db, {"pi_number": pi, "client_name": pi},
                   [{"product_name": n, "quantity": q, "unit_price": 1, "unit": u}
                    for n, q, u in lines])
    db.commit()
    return sid


def _line_ids(db, sid):
    return {r[0]: r[1] for r in db.execute(
        "SELECT product_name, id FROM sale_items WHERE sale_id = %s", (sid,)).fetchall()}


def _invoice(db, sid, number, lines):
    """A real non-void invoice: `lines` is [(sale_item_id, name, unit, qty)]."""
    inv = db.execute("INSERT INTO invoices (sale_id, invoice_number, status) "
                     "VALUES (%s, %s, 'planned') RETURNING id", (sid, number)).fetchone()[0]
    for item_id, name, unit, qty in lines:
        db.execute("INSERT INTO invoice_items (invoice_id, sale_item_id, product_name, "
                   "unit, quantity, unit_price, line_total) "
                   "VALUES (%s, %s, %s, %s, %s, 1, %s)", (inv, item_id, name, unit, qty, qty))
    db.commit()
    return inv


def _produce(db, sid, item_id, name, unit, qty, invoice_id, tag="", recipe=None):
    """Record production of `qty` of `name` for this PI through its invoice.

    `recipe` runs the production through a recipe that already exists: an
    invoice may carry only one recipe (`idx_recipes_invoice_id`), so a case that
    plants an invoice recipe must produce through that same recipe — and through
    its existing product slot, which `idx_recipe_products_recipe_product` also
    allows only one of.
    """
    if recipe is None:
        comp = db.execute("INSERT INTO companies (name) VALUES (%s) RETURNING id",
                          (f"DemoCo-{sid}-{invoice_id}-{tag}{qty}",)).fetchone()[0]
        recipe = db.execute("INSERT INTO recipes (name, company_id, invoice_id) "
                            "VALUES (%s, %s, %s) RETURNING id",
                            (f"DemoRun-{sid}-{invoice_id}-{tag}{qty}", comp,
                             invoice_id)).fetchone()[0]
    slot = db.execute("SELECT id FROM recipe_products WHERE recipe_id = %s "
                      "AND lower(product_name) = lower(%s)",
                      (recipe, name)).fetchone()
    if slot is None:
        slot = db.execute(
            "INSERT INTO recipe_products (recipe_id, product_name, unit) "
            "VALUES (%s, %s, %s) RETURNING id", (recipe, name, unit)).fetchone()[0]
    else:
        slot = slot[0]
    run = db.execute("INSERT INTO production_runs (recipe_id, sale_item_id, qty_produced) "
                     "VALUES (%s, %s, %s) RETURNING id",
                     (recipe, item_id, qty)).fetchone()[0]
    db.execute("INSERT INTO production_run_links (run_id, sale_id, invoice_id) "
               "VALUES (%s, %s, %s)", (run, sid, invoice_id))
    db.execute("INSERT INTO production_run_outputs (run_id, recipe_product_id, "
               "product_name, unit, qty_produced) VALUES (%s, %s, %s, %s, %s)",
               (run, slot, name, unit, qty))
    db.commit()
    return run


def _demo_row(client, sid):
    return client.post(f"/api/sales/{sid}/demo-recipe", json={})


def _get_demo(client, sid):
    return client.get(f"/api/sales/{sid}/demo-recipe")


def _put_draft(client, sid, expected_revision, products):
    return client.put(f"/api/sales/{sid}/demo-recipe/draft",
                      json={"expected_revision": expected_revision, "products": products})


def _post_activate(client, sid, expected_revision):
    return client.post(f"/api/sales/{sid}/demo-recipe/activate",
                       json={"expected_revision": expected_revision})


def _body(resp):
    payload = resp.get_json()
    return payload if isinstance(payload, dict) else {}


def _demo(resp):
    """The demo envelope, whichever wrapper the route uses."""
    payload = _body(resp)
    return payload.get("demo", payload)


def _product(name, items, unit="KG", water=None):
    """A draft product: unit, explicit/auto water, and its ingredient rows."""
    return {"product_name": name, "unit": unit, "water_percentage": water,
            "items": [{"chemical": chem, "percentage": pct} for chem, pct in items]}


def _demo_id(db, sid):
    row = db.execute("SELECT id FROM pi_demos WHERE sale_id = %s", (sid,)).fetchone()
    return row[0] if row else None


def _rev(db, sid):
    """The CURRENT draft revision, so no case hard-codes an off-by-one."""
    return db.execute("SELECT draft_revision FROM pi_demos WHERE sale_id = %s",
                      (sid,)).fetchone()[0]


def _counts(db):
    return {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("pi_demos", "pi_demo_products", "pi_demo_items")}


def _scope_products(db, demo_id, scope):
    """{normalized product name: (unit, stored water or None)} for one scope."""
    return {r[0].strip().lower(): (r[1], None if r[2] is None else float(r[2]))
            for r in db.execute(
                "SELECT product_name, unit, water_percentage FROM pi_demo_products "
                "WHERE demo_id = %s AND scope = %s", (demo_id, scope)).fetchall()}


def _scope_items(db, demo_id, scope):
    """{(normalized product name, chemical name): percentage} for one scope."""
    return {(r[0].strip().lower(), r[1]): float(r[2]) for r in db.execute(
        "SELECT p.product_name, c.name, i.percentage FROM pi_demo_items i "
        "JOIN pi_demo_products p ON p.id = i.demo_product_id "
        "JOIN chemicals c ON c.id = i.chemical_id "
        "WHERE p.demo_id = %s AND p.scope = %s", (demo_id, scope)).fetchall()}


def _active_snapshot(db, demo_id):
    """Everything activation is allowed to write, for the untouched-after-failure probe."""
    return (_scope_products(db, demo_id, "active"), _scope_items(db, demo_id, "active"),
            db.execute("SELECT active_revision, activated_at, activated_by FROM pi_demos "
                       "WHERE id = %s", (demo_id,)).fetchone())


def _create_ok(client, sid):
    r = _demo_row(client, sid)
    assert r.status_code == 201, _body(r)
    return _demo(r)


def _draft_ok(client, db, sid, products):
    r = _put_draft(client, sid, _rev(db, sid), products)
    assert r.status_code == 200, _body(r)
    return _demo(r)


def _activate_now(client, db, sid):
    return _post_activate(client, sid, _rev(db, sid))


def _plan(client, db, sid, products):
    """Draft these products, then activate at the revision that just landed."""
    _draft_ok(client, db, sid, products)
    return _activate_now(client, db, sid)


def _rejected(resp, *names):
    """A 400 with field details that names each offending product."""
    assert resp.status_code == 400, _body(resp)
    payload = _body(resp)
    assert payload.get("details"), payload
    text = json.dumps(payload).lower()
    for name in names:
        assert name.lower() in text, (name, payload)


def _no_active_plan(db, sid):
    assert _scope_products(db, _demo_id(db, sid), "active") == {}
    assert _scope_items(db, _demo_id(db, sid), "active") == {}


# --------------------------------------------------------------------------- #
# 1-3 — creation, the read-only GET, one demo per PI
# --------------------------------------------------------------------------- #
def test_a_demo_can_be_created_before_any_lc_or_invoice(admin_client, db):
    """Planning happens at pi_issued: no LC, no invoice, no recipe.

    Products seed from the PI lines with no ingredients and auto water, so the
    editor opens on the PI's own shape instead of an empty grid.
    """
    sid = _pi(db, "PI-DEMO-SEED", [("Product A", 100, "KG"), ("Product B", 30, "L")])
    assert db.execute("SELECT COUNT(*) FROM invoices WHERE sale_id = %s",
                      (sid,)).fetchone()[0] == 0
    assert db.execute("SELECT lc_number FROM sales WHERE id = %s", (sid,)).fetchone()[0] is None

    demo = _create_ok(admin_client, sid)
    demo_id = _demo_id(db, sid)
    assert demo["id"] == demo_id and demo["sale_id"] == sid
    assert demo["draft_revision"] == 1
    assert demo["active_revision"] is None and demo["activated_at"] is None

    # The draft mirrors the PI: one row per product, its PI unit, auto water,
    # and no invented ingredients.
    assert _scope_products(db, demo_id, "draft") == {
        "product a": ("KG", None), "product b": ("L", None)}
    assert _scope_items(db, demo_id, "draft") == {}
    assert _counts(db)["pi_demo_items"] == 0

    # The same demo reopens; a second GET is not a second create.
    assert _demo(_get_demo(admin_client, sid))["id"] == demo_id
    assert _counts(db) == {"pi_demos": 1, "pi_demo_products": 2, "pi_demo_items": 0}


def test_a_get_never_creates_a_demo_or_a_formulation_row(admin_client, db):
    """A read must leave "no plan yet" as "no plan yet".

    Auto-creating on GET would convert a missing plan into an empty draft, and
    the coverage report would stop listing the PI as unplanned — the operator
    would lose exactly the demand the report exists to surface.
    """
    assert _counts(db) == {"pi_demos": 0, "pi_demo_products": 0, "pi_demo_items": 0}
    sid = _pi(db, "PI-DEMO-READONLY", [("Ghost", 10, "KG")])

    for _ in range(3):
        assert _get_demo(admin_client, sid).status_code == 404
    assert _counts(db) == {"pi_demos": 0, "pi_demo_products": 0, "pi_demo_items": 0}
    assert _demo_id(db, sid) is None

    # Positive control: once a demo does exist, re-reading it is still inert.
    planned = _pi(db, "PI-DEMO-READONLY2", [("Ghost", 10, "KG")])
    _chem(db, "Chem A")
    demo_id = _create_ok(admin_client, planned)["id"]
    _draft_ok(admin_client, db, planned, [_product("Ghost", [("Chem A", 60)])])
    after_write = _counts(db)
    for _ in range(3):
        reopened = _get_demo(admin_client, planned)
        assert reopened.status_code == 200, _body(reopened)
        assert _demo(reopened)["id"] == demo_id
    assert _counts(db) == after_write
    assert _rev(db, planned) == 2


def test_one_demo_per_pi_and_a_missing_pi(admin_client, db):
    """`sale_id` is unique, so a double create is 409 — but the limit is per PI.

    A second PI must still get its own demo, and a demo bound to a PI that does
    not exist is a 404 rather than an orphan row.
    """
    first = _pi(db, "PI-DEMO-DUP", [("A", 10, "KG")])
    second = _pi(db, "PI-DEMO-DUP2", [("B", 10, "KG")])

    assert _demo_row(admin_client, first).status_code == 201
    conflict = _demo_row(admin_client, first)
    assert conflict.status_code == 409, _body(conflict)
    assert _counts(db)["pi_demos"] == 1

    assert _demo_row(admin_client, second).status_code == 201
    assert _counts(db)["pi_demos"] == 2
    assert _demo_row(admin_client, 999_999).status_code == 404
    assert _counts(db)["pi_demos"] == 2


# --------------------------------------------------------------------------- #
# 4-7 — revisions, the gate, and failure leaving the active snapshot alone
# --------------------------------------------------------------------------- #
def test_saving_a_draft_moves_only_the_draft_revision(admin_client, db):
    """An edit to the draft must not replace or suspend the active formula.

    `active_revision` keeps pointing at the draft revision that was activated;
    the draft's own counter is the only one that moves.
    """
    sid = _pi(db, "PI-DEMO-REV", [("A", 100, "KG")])
    _chem(db, "Chem A")
    _create_ok(admin_client, sid)
    assert _plan(admin_client, db, sid,
                 [_product("A", [("Chem A", 60)])]).status_code == 200
    demo_id = _demo_id(db, sid)
    before = _active_snapshot(db, demo_id)
    assert before[2][0] == 2

    changed = _draft_ok(admin_client, db, sid, [_product("A", [("Chem A", 30)], water=70)])
    assert changed["draft_revision"] == 3
    assert changed["active_revision"] == 2
    assert _active_snapshot(db, demo_id) == before
    # the draft moved; the active rows did not.
    assert _scope_products(db, demo_id, "draft") == {"a": ("KG", 70.0)}
    assert _scope_items(db, demo_id, "draft") == {("a", "Chem A"): 30.0}
    assert _scope_items(db, demo_id, "active") == {("a", "Chem A"): 60.0}


def test_activation_requires_balance_coverage_and_conversions(admin_client, db):
    """Three separate refusals and one success, so no gate can cover for another.

    (a) ingredients + explicit water that do not total 100,
    (b) a chemical whose unit cannot be reached from the product unit,
    (c) a clean plan.
    """
    sid = _pi(db, "PI-DEMO-UNBALANCED", [("A", 100, "KG")])
    _chem(db, "Chem A")
    _create_ok(admin_client, sid)
    _rejected(_plan(admin_client, db, sid,
                    [_product("A", [("Chem A", 60)], water=10)]), "A")
    _no_active_plan(db, sid)

    other = _pi(db, "PI-DEMO-NOCONV", [("B", 100, "KG")])
    _chem(db, "Vial Chem", unit="VIAL")
    _create_ok(admin_client, other)
    _rejected(_plan(admin_client, db, other,
                    [_product("B", [("Vial Chem", 60)])]), "B")
    _no_active_plan(db, other)

    good = _pi(db, "PI-DEMO-BALANCED", [("C", 100, "KG")])
    _create_ok(admin_client, good)
    ok = _plan(admin_client, db, good, [_product("C", [("Chem A", 60)])])
    assert ok.status_code == 200, _body(ok)
    assert _scope_products(db, _demo_id(db, good), "active") == {"c": ("KG", 40.0)}


def test_a_failed_activation_leaves_the_previous_active_formula_untouched(admin_client, db):
    """Reactivation is atomic: a refused gate must not suspend or half-replace
    the live snapshot, and must not restamp actor/time or bump the revision.

    The forecast the plant is buying against stays whatever it was until a
    complete plan actually activates.
    """
    sid = _pi(db, "PI-DEMO-ATOMIC", [("A", 100, "KG"), ("B", 50, "KG")])
    _chem(db, "Chem A")
    _chem(db, "Chem B")
    _create_ok(admin_client, sid)
    assert _plan(admin_client, db, sid,
                 [_product("A", [("Chem A", 60)]), _product("B", [("Chem B", 70)])]).status_code == 200
    demo_id = _demo_id(db, sid)
    before = _active_snapshot(db, demo_id)

    # A draft may be incomplete; activating it is what must fail.
    _draft_ok(admin_client, db, sid, [_product("A", [("Chem A", 60)]), _product("B", [])])
    _rejected(_activate_now(admin_client, db, sid), "B")
    assert _active_snapshot(db, demo_id) == before
    assert before[1] == {("a", "Chem A"): 60.0, ("b", "Chem B"): 70.0}
    assert before[2][2] == "admin"


def test_a_stale_expected_revision_is_a_409_on_draft_and_on_activation(admin_client, db):
    """Two tabs, one plan: the loser is told, and nothing is written twice.

    The revision is checked before the gate runs, so a stale activation cannot
    be "rescued" by happening to describe a currently valid formulation.
    """
    sid = _pi(db, "PI-DEMO-STALE", [("A", 100, "KG")])
    _chem(db, "Chem A")
    _create_ok(admin_client, sid)

    stale = _put_draft(admin_client, sid, 7, [_product("A", [("Chem A", 60)])])
    assert stale.status_code == 409, _body(stale)
    assert _rev(db, sid) == 1 and _demo_id(db, sid) is not None
    assert _counts(db)["pi_demo_items"] == 0

    products = [_product("A", [("Chem A", 60)])]
    _draft_ok(admin_client, db, sid, products)
    old_activation = _post_activate(admin_client, sid, 1)
    assert old_activation.status_code == 409, _body(old_activation)
    assert _scope_products(db, _demo_id(db, sid), "active") == {}
    assert db.execute("SELECT active_revision FROM pi_demos WHERE sale_id = %s",
                      (sid,)).fetchone()[0] is None
    assert _post_activate(admin_client, sid, 2).status_code == 200


# --------------------------------------------------------------------------- #
# 8-11 — water, ingredients, percentages, and product mapping
# --------------------------------------------------------------------------- #
def test_explicit_water_overrides_auto_and_auto_water_snapshots_the_resolved_value(admin_client, db):
    """The active row stores a NUMBER, never "derive it later".

    An explicit 45 wins over 100 - 55; a NULL draft water resolves at activation
    to round(100 - 62, 6) = 38 and that 38 is what gets stored.
    """
    explicit = _pi(db, "PI-DEMO-WATER-EXPLICIT", [("A", 100, "KG")])
    _chem(db, "Chem A")
    _create_ok(admin_client, explicit)
    assert _plan(admin_client, db, explicit,
                 [_product("A", [("Chem A", 55)], water=45)]).status_code == 200
    assert _scope_products(db, _demo_id(db, explicit), "active") == {"a": ("KG", 45.0)}

    auto = _pi(db, "PI-DEMO-WATER-AUTO", [("A", 100, "KG")])
    _create_ok(admin_client, auto)
    assert _plan(admin_client, db, auto, [_product("A", [("Chem A", 62)])]).status_code == 200
    stored = _scope_products(db, _demo_id(db, auto), "active")["a"][1]
    assert stored == 38.0, stored


def test_a_product_with_no_ingredients_cannot_activate(admin_client, db):
    """No ingredients means UNRESOLVED water, not an automatic 100% water plan.

    Auto-filling "water = 100" would activate a formulation that buys nothing,
    silently dropping this PI's demand out of the purchasing projection.
    """
    sid = _pi(db, "PI-DEMO-EMPTY", [("Solo", 100, "KG")])
    _create_ok(admin_client, sid)
    _draft_ok(admin_client, db, sid, [_product("Solo", [])])
    _rejected(_activate_now(admin_client, db, sid), "Solo")
    _no_active_plan(db, sid)
    assert db.execute("SELECT COUNT(*) FROM pi_demo_products WHERE scope = 'active' "
                      "AND water_percentage = 100").fetchone()[0] == 0


@pytest.mark.parametrize("products,offender", [
    ([_product("A", [("Chem A", 0)])], "zero percentage"),
    ([_product("A", [("Chem A", 140)])], "percentage over 100"),
    ([_product("A", [("Chem A", 30), ("Chem A", 30)])], "duplicate chemical"),
])
def test_an_included_percentage_of_zero_or_out_of_range_cannot_activate(
        admin_client, db, products, offender):
    """Every included ingredient must be finite, positive and in range, and one
    chemical may not appear twice in a product.

    A zero percentage is legal DRAFT state ("not decided yet") and illegal ACTIVE
    state, so the refusal may land on the draft write or on the gate — what may
    never happen is an active row.
    """
    sid = _pi(db, f"PI-DEMO-PCT-{offender}", [("A", 100, "KG")])
    _chem(db, "Chem A")
    assert _demo_row(admin_client, sid).status_code == 201
    saved = _put_draft(admin_client, sid, _rev(db, sid), products)
    if saved.status_code == 200:
        refused = _activate_now(admin_client, db, sid)
        assert refused.status_code == 400, _body(refused)
        assert _body(refused).get("details"), _body(refused)
    else:
        assert saved.status_code == 400, _body(saved)
    assert _scope_products(db, _demo_id(db, sid), "active") == {}
    assert _scope_items(db, _demo_id(db, sid), "active") == {}


def test_missing_current_products_and_unmapped_rows_get_field_errors(admin_client, db):
    """The gate compares the plan to the PI, in both directions.

    A current PI product with no formulation is a field error naming that
    product; a row for a product the PI does not sell is refused rather than
    quietly mapped onto a similar name.
    """
    sid = _pi(db, "PI-DEMO-MAPPING", [("Wanted", 100, "KG"), ("Other", 50, "KG")])
    _chem(db, "Chem A")
    _create_ok(admin_client, sid)

    _rejected(_plan(admin_client, db, sid, [_product("Wanted", [("Chem A", 60)])]),
              "Other")
    _no_active_plan(db, sid)
    _rejected(_plan(admin_client, db, sid,
                    [_product("Wanted", [("Chem A", 60)]), _product("Ghost", [("Chem A", 60)])]),
              "Ghost")
    _no_active_plan(db, sid)

    ok = _plan(admin_client, db, sid,
               [_product("Wanted", [("Chem A", 60)]), _product("Other", [("Chem A", 40)])])
    assert ok.status_code == 200, _body(ok)
    assert set(_scope_products(db, _demo_id(db, sid), "active")) == {"wanted", "other"}


# --------------------------------------------------------------------------- #
# 12-14 — the retired-remainder rule and plan completeness
# --------------------------------------------------------------------------- #
def _wont_ship_remainder(db, pi="PI-DEMO-REMAINDER"):
    """Ordered 100, invoiced 60, produced 20, then marked won't-ship: target 60,
    remaining 40 output units — and a formulation is still required."""
    sid = _pi(db, pi, [("Retiring", 100, "KG")])
    item_id = _line_ids(db, sid)["Retiring"]
    inv = _invoice(db, sid, f"INV-{pi}", [(item_id, "Retiring", "KG", 60)])
    _produce(db, sid, item_id, "Retiring", "KG", 20, inv)
    assert set_sale_item_wont_ship(db, sid, item_id, 1) is True
    return sid


def test_a_wont_ship_remainder_still_needs_a_formulation_and_projects_40(admin_client, db):
    """The marked line is NOT filtered out of demand: its target is the retained
    `min(ordered, non-void invoiced)` = 60, less 20 produced = 40 output units.

    Dropping the line would erase this PI from the buying list; using the full
    100 would buy for balance the admin already retired.
    """
    sid = _wont_ship_remainder(db)
    _chem(db, "Chem R")
    _create_ok(admin_client, sid)
    _rejected(_activate_now(admin_client, db, sid), "Retiring")
    _no_active_plan(db, sid)

    assert _plan(admin_client, db, sid,
                 [_product("Retiring", [("Chem R", 60)])]).status_code == 200
    row = _rows(_svc().project_purchasing(db))["chem r"]
    assert row["required_quantity"] == 24.0, row   # 40 output units x 60%
    assert row["projected_quantity"] == 24.0, row   # nothing in stock yet


def test_a_wholly_retired_zero_target_product_needs_no_formulation(admin_client, db):
    """Retired with nothing invoiced, the target is 0: no demand, no formulation.

    A product that stays formulated anyway must still contribute nothing, so the
    retired remainder cannot leak back into the buying list.
    """
    sid = _pi(db, "PI-DEMO-RETIRED", [("Live", 50, "KG"), ("Retired", 100, "KG")])
    item_id = _line_ids(db, sid)["Retired"]
    assert set_sale_item_wont_ship(db, sid, item_id, 1) is True
    _chem(db, "Chem L")
    _create_ok(admin_client, sid)

    assert _plan(admin_client, db, sid,
                 [_product("Live", [("Chem L", 60)])]).status_code == 200
    assert _rows(_svc().project_purchasing(db))["chem l"]["required_quantity"] == 30.0

    # The same plan with a formulation for the retired product changes nothing:
    # a zero target contributes no demand even when it has a formula.
    other = _pi(db, "PI-DEMO-RETIRED2", [("Live", 50, "KG"), ("Retired", 100, "KG")])
    other_item = _line_ids(db, other)["Retired"]
    assert set_sale_item_wont_ship(db, other, other_item, 1) is True
    _chem(db, "Chem Z", qty=0)
    _create_ok(admin_client, other)
    assert _plan(admin_client, db, other,
                 [_product("Live", [("Chem L", 60)]),
                  _product("Retired", [("Chem Z", 60)])]).status_code == 200
    rows = _rows(_svc().project_purchasing(db))
    assert rows["chem l"]["required_quantity"] == 60.0   # both PIs' live 50 x 60%
    assert _required(rows, "Chem Z") == 0.0               # retired: no demand at all


def test_a_new_pi_product_missing_from_an_active_plan_marks_the_plan_incomplete(admin_client, db):
    """PI quantities are live; only the formula is a snapshot.

    A product added after activation makes the ACTIVE plan incomplete: the PI is
    surfaced, and the totals must not be presented as complete.
    """
    sid = _pi(db, "PI-DEMO-GREW", [("Ten", 100, "KG")])
    _chem(db, "Chem G")
    _create_ok(admin_client, sid)
    assert _plan(admin_client, db, sid, [_product("Ten", [("Chem G", 60)])]).status_code == 200
    coverage = _coverage(_svc().project_purchasing(db))
    assert coverage["state"] == "complete", coverage

    db.execute("INSERT INTO sale_items (sale_id, product_name, quantity, unit_price, unit) "
               "VALUES (%s, 'Eleven', 30, 1, 'KG')", (sid,))
    db.commit()

    coverage = _coverage(_svc().project_purchasing(db))
    assert coverage["state"] == "incomplete", coverage
    assert sid in {row["sale_id"] for row in coverage["incomplete_plans"]}, coverage
    text = json.dumps(coverage).lower()
    assert "eleven" in text and "pi-demo-grew" in text, coverage
    # the formulated product's demand survives; only completeness is refused.
    assert _rows(_svc().project_purchasing(db))["chem g"]["required_quantity"] == 60.0


# --------------------------------------------------------------------------- #
# projection accessors (shared shape with test_pi_demo_projection.py)
# --------------------------------------------------------------------------- #
def _rows(projection):
    """Every per-chemical row the projection returns, keyed by lowercased name."""
    found = {}

    def walk(node):
        if isinstance(node, dict):
            if "projected_quantity" in node and isinstance(node.get("name"), str):
                found.setdefault(node["name"].strip().lower(), node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(projection)
    return found


def _coverage(projection):
    coverage = projection.get("coverage")
    assert isinstance(coverage, dict), coverage
    return coverage


def _required(rows, name, default=0.0):
    """A material's remaining requirement, 0 when the plan puts no demand on it."""
    row = rows.get(name.strip().lower())
    return default if row is None else row["required_quantity"]
