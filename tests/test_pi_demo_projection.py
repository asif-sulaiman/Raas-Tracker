"""Slice 3 RED: the net purchasing projection.

``Projected Quantity`` is **additional quantity to buy**, not gross demand:

    projected = max(total remaining active-plan requirement - in-house stock, 0)

so a 300 KG requirement against 200 KG on the shelf says "buy 100", and nothing
else. Three confusions would each make the number a lie, and each gets its own
case below:

* subtracting stock once per PI instead of once per chemical (double credit);
* letting production move the shortage when it only moved the requirement
  (in-house production spends the stock it just consumed);
* reporting an unknown as zero — a missing conversion or an unattributable
  historical output must come back as ``None``, while a genuine "nothing left to
  make" comes back as ``0.0``.

Demand is only ever read from an **activated** demo of a PI whose
``sales.stage != 'completed'``; ``paid`` is an invoice status and never a PI
predicate, so a shipped-but-unpaid PI keeps its remaining work and a fully
produced PI does not. Outward production lowers demand without lowering stock.
Every figure is computed on demand — there is no cache, no stored projection and
no TTL to invalidate.

`raas_tracker/pi_demos.py` does not exist yet, so every case here fails on that
import. That is the correct RED for Task 5b-1. The setup helpers are imported
from ``tests/test_pi_demos.py`` (same slice, same fixtures) rather than
re-derived.
"""
from test_pi_demos import (  # noqa: F401  (shared Slice 3 test scaffolding)
    _chem, _convert, _coverage, _create_ok, _invoice, _line_ids, _plan, _pi,
    _produce, _product, _required, _rows, _svc,
)

from raas_tracker.sales import set_sale_item_wont_ship


def _stock(db, name):
    return db.execute("SELECT current_qty FROM chemicals WHERE name = %s",
                      (name,)).fetchone()[0]


def _project(db):
    return _rows(_svc().project_purchasing(db))


def _one_activated(db, admin_client, pi, chem, ordered=100, unit="KG", product="A"):
    """One PI with one line and an activated 100%-chemical formulation.

    Percentages are 100 with water 0, so a requirement in chemical units equals
    a remaining output in product units and every number below is arithmetic.
    """
    sid = _pi(db, pi, [(product, ordered, unit)])
    _create_ok(admin_client, sid)
    response = _plan(admin_client, db, sid,
                    [_product(product, [(chem, 100)], unit=unit, water=0)])
    assert response.status_code == 200, response.get_json()
    return sid, _line_ids(db, sid)[product]


def _outward_line(db, chem_id, unit, qty=1000):
    """A real outward receipt line: bought for a client, never stocked."""
    client = db.execute("INSERT INTO companies (name) VALUES (%s) RETURNING id",
                        (f"Outward Co-{chem_id}",)).fetchone()[0]
    purchase = db.execute(
        "INSERT INTO chemical_purchases (supplier_name, purchase_number, "
        "purchase_date, delivery_type, client_id, created_by) "
        "VALUES ('Outward Sup', %s, '2026-03-01', 'outward', %s, 'admin') RETURNING id",
        (f"PO-OUT-{chem_id}", client)).fetchone()[0]
    line = db.execute(
        "INSERT INTO chemical_purchase_items (purchase_id, chemical_id, quantity, unit) "
        "VALUES (%s, %s, %s, %s) RETURNING id", (purchase, chem_id, qty, unit)).fetchone()[0]
    db.commit()
    return line, client


def _outward_evidence(db, run_id, chem_id, name, unit, required, line_id):
    """The run consumed that outward line: zero in-house deduction."""
    db.execute(
        "INSERT INTO production_run_items (run_id, chemical_id, chemical_name, "
        "required_qty, deducted_qty, unit, source_type, outward_purchase_line_id, "
        "purchase_number, purchase_date, supplier_name, source_qty, source_unit) "
        "VALUES (%s, %s, %s, %s, 0, %s, 'outward', %s, %s, '2026-03-01', "
        "'Outward Sup', %s, %s)",
        (run_id, chem_id, name, required, unit, line_id, f"PO-OUT-{chem_id}",
         required, unit))
    db.commit()


# --------------------------------------------------------------------------- #
# 1-3 — the shape of the number
# --------------------------------------------------------------------------- #
def test_projection_is_the_shortage_not_the_requirement(admin_client, db):
    """300 required against 200 in stock projects 100 — the headline example.

    Both figures travel together so the UI can say "300 needed, 200 held, buy 100".
    """
    _chem(db, "Chem Bulk", qty=200)
    _one_activated(db, admin_client, "PI-PROJ-NET", "Chem Bulk", ordered=300)

    row = _project(db)["chem bulk"]
    assert row["required_quantity"] == 300.0, row
    assert row["projected_quantity"] == 100.0, row


def test_two_pis_draw_on_the_same_stock_exactly_once(admin_client, db):
    """Stock is subtracted once per chemical, after the plans are summed.

    200 + 100 against 100 held is 200 to buy, never 100 + 0 — two PIs cannot
    each spend the same drum.
    """
    _chem(db, "Chem Shared", qty=100)
    _one_activated(db, admin_client, "PI-PROJ-SHARE-A", "Chem Shared", ordered=200)
    _one_activated(db, admin_client, "PI-PROJ-SHARE-B", "Chem Shared", ordered=100)

    row = _project(db)["chem shared"]
    assert row["required_quantity"] == 300.0, row
    assert row["projected_quantity"] == 200.0, row


def test_a_fully_stocked_requirement_floors_at_a_real_zero(admin_client, db):
    """Surplus stock is 0.0, not None: "nothing to buy" is a decision, "unknown"
    is not, and the UI must be able to tell them apart."""
    _chem(db, "Chem Covered", qty=150)
    _one_activated(db, admin_client, "PI-PROJ-COVERED", "Chem Covered", ordered=100)

    row = _project(db)["chem covered"]
    assert row["required_quantity"] == 100.0, row
    assert row["projected_quantity"] == 0.0, row
    assert row["projected_quantity"] is not None, row


# --------------------------------------------------------------------------- #
# 4-6 — production, in-house and outward
# --------------------------------------------------------------------------- #
def test_in_house_production_can_leave_the_shortage_unchanged(admin_client, db):
    """Producing spends stock as it spends demand, so the gap may not move.

    300 needed / 200 held projects 100. Making 100 leaves 200 needed / 100 held —
    still 100. Production is not required to shrink the shortage on every run.
    """
    chem_id = _chem(db, "Chem Make", qty=200)
    sid, item_id = _one_activated(db, admin_client, "PI-PROJ-MAKE", "Chem Make", ordered=300)
    assert _project(db)["chem make"]["projected_quantity"] == 100.0

    invoice = _invoice(db, sid, "INV-PROJ-MAKE", [(item_id, "A", "KG", 100)])
    _produce(db, sid, item_id, "A", "KG", 100, invoice)
    db.execute("UPDATE chemicals SET current_qty = 100 WHERE id = %s", (chem_id,))
    db.commit()

    row = _project(db)["chem make"]
    assert row["required_quantity"] == 200.0, row
    assert row["projected_quantity"] == 100.0, row


def test_outward_production_reduces_demand_without_reducing_stock(admin_client, db):
    """Material that arrived on the client's own outward receipt was never on
    our shelf: making 100 of it must cut the requirement and leave stock alone.
    """
    chem_id = _chem(db, "Chem Outward", qty=200)
    sid, item_id = _one_activated(db, admin_client, "PI-PROJ-OUT", "Chem Outward", ordered=300)
    line, _client = _outward_line(db, chem_id, "KG")

    invoice = _invoice(db, sid, "INV-PROJ-OUT", [(item_id, "A", "KG", 100)])
    run = _produce(db, sid, item_id, "A", "KG", 100, invoice)
    _outward_evidence(db, run, chem_id, "Chem Outward", "KG", 100, line)

    assert _stock(db, "Chem Outward") == 200, "an outward run must not deduct stock"
    row = _project(db)["chem outward"]
    assert row["required_quantity"] == 200.0, row
    assert row["projected_quantity"] == 0.0, row


def test_partial_full_and_overproduction_each_floor_separately(admin_client, db):
    """Remaining output is floored per PI and per product.

    Overproducing one product cannot pay for another product on the same PI,
    nor for the same product on a different PI.
    """
    _chem(db, "Chem Partial", qty=0)
    _chem(db, "Chem Full", qty=0)
    _chem(db, "Chem Over", qty=0)
    _chem(db, "Chem Sibling", qty=0)

    sid, item_id = _one_activated(db, admin_client, "PI-PROJ-PARTIAL", "Chem Partial", ordered=100)
    invoice = _invoice(db, sid, "INV-PARTIAL", [(item_id, "A", "KG", 100)])
    _produce(db, sid, item_id, "A", "KG", 40, invoice)
    assert _project(db)["chem partial"]["projected_quantity"] == 60.0

    done, done_item = _one_activated(db, admin_client, "PI-PROJ-FULL", "Chem Full", ordered=100)
    full_inv = _invoice(db, done, "INV-FULL", [(done_item, "A", "KG", 100)])
    _produce(db, done, done_item, "A", "KG", 100, full_inv)
    full = _project(db)["chem full"]
    assert full["required_quantity"] == 0.0, full
    assert full["projected_quantity"] == 0.0, full

    # Overproduction: 150 made against 100 ordered is zero, and the surplus
    # cannot be spent on this PI's second product or on another PI's 100.
    over = _pi(db, "PI-PROJ-OVER", [("Big", 100, "KG"), ("Sibling", 50, "KG")])
    over_items = _line_ids(db, over)
    _create_ok(admin_client, over)
    assert _plan(admin_client, db, over, [_product("Big", [("Chem Over", 100)], water=0),
                                          _product("Sibling", [("Chem Sibling", 100)], water=0)
                                          ]).status_code == 200
    over_inv = _invoice(db, over, "INV-OVER",
                        [(over_items["Big"], "Big", "KG", 100)])
    _produce(db, over, over_items["Big"], "Big", "KG", 150, over_inv)
    rows = _project(db)
    assert rows["chem over"]["projected_quantity"] == 0.0, rows["chem over"]
    assert rows["chem sibling"]["required_quantity"] == 50.0, rows["chem sibling"]

    other, other_item = _one_activated(db, admin_client, "PI-PROJ-OVER2", "Chem Over", ordered=100)
    assert _project(db)["chem over"]["required_quantity"] == 100.0
    assert _project(db)["chem over"]["projected_quantity"] == 100.0
    assert other != over and other_item != over_items["Big"]


# --------------------------------------------------------------------------- #
# 7-9 — grouping, units, and the unknown
# --------------------------------------------------------------------------- #
def test_duplicate_pi_lines_for_one_product_are_grouped(admin_client, db):
    """Two lines named "Blend" and " blend " are one product: 60 + 40 = 100 KG
    of demand, not two plans and not one of them ignored.
    """
    _chem(db, "Chem Blend", qty=0)
    sid = _pi(db, "PI-PROJ-GROUP", [("Blend", 60, "KG"), (" blend ", 40, "KG")])
    _create_ok(admin_client, sid)
    assert _plan(admin_client, db, sid,
                 [_product("Blend", [("Chem Blend", 100)], water=0)]).status_code == 200
    assert _project(db)["chem blend"]["required_quantity"] == 100.0


def test_units_are_converted_before_anything_is_summed(admin_client, db):
    """1 DRUM + 50 KG of the same product is 250 KG of chemical, in the
    chemical's registered unit — the product unit is not the answer's unit.
    """
    _chem(db, "Chem Drum", qty=0)
    sid = _pi(db, "PI-PROJ-UNITS", [("Dosed", 1, "DRUM"), ("Dosed", 50, "KG")])
    _create_ok(admin_client, sid)
    assert _plan(admin_client, db, sid,
                 [_product("Dosed", [("Chem Drum", 100)], unit="DRUM", water=0)]).status_code == 200

    row = _project(db)["chem drum"]
    assert row["required_quantity"] == 250.0, row   # DRUM -> KG is 200
    assert row["projected_quantity"] == 250.0, row


def test_a_missing_conversion_is_unknown_not_zero(admin_client, db):
    """No conversion path means the figure cannot be computed: null, and visibly
    different from the genuine zero of a covered requirement.
    """
    _chem(db, "Chem Ampoule", unit="VIAL", qty=0)
    _convert(db, "AMPOULE", "VIAL", 5)
    sid, _item = _one_activated(db, admin_client, "PI-PROJ-NOCONV", "Chem Ampoule",
                                ordered=100, unit="AMPOULE", product="Amp")
    # The conversion existed at activation and is gone now: the plan is valid
    # history, the arithmetic is not.
    db.execute("DELETE FROM unit_conversions WHERE from_unit = 'AMPOULE' "
               "OR to_unit = 'AMPOULE'")
    db.commit()

    row = _project(db)["chem ampoule"]
    assert row["required_quantity"] is None, row
    assert row["projected_quantity"] is None, row

    _chem(db, "Chem Covered 2", qty=500)
    _one_activated(db, admin_client, "PI-PROJ-ZERO", "Chem Covered 2", ordered=100)
    assert _project(db)["chem covered 2"]["projected_quantity"] == 0.0


# --------------------------------------------------------------------------- #
# 10-12 — which PIs count
# --------------------------------------------------------------------------- #
def _retire(db, sid):
    db.execute("UPDATE sales SET stage = 'completed' WHERE id = %s", (sid,))
    db.commit()


def test_a_completed_pi_contributes_no_demand(admin_client, db):
    """`sales.stage = 'completed'` is the only completion predicate, and it
    removes the PI from demand outright."""
    _chem(db, "Chem Done", qty=0)
    sid, _item = _one_activated(db, admin_client, "PI-PROJ-DONE", "Chem Done", ordered=100)
    assert _project(db)["chem done"]["projected_quantity"] == 100.0

    _retire(db, sid)
    assert _required(_project(db), "Chem Done") == 0.0


def test_a_reopened_pi_is_back_using_its_current_remaining_quantities(admin_client, db):
    """Reopening a PI restores eligibility from what is left, not from scratch:
    production recorded while it was closed still counts.
    """
    _chem(db, "Chem Reopen", qty=0)
    sid, item_id = _one_activated(db, admin_client, "PI-PROJ-REOPEN", "Chem Reopen", ordered=100)
    invoice = _invoice(db, sid, "INV-REOPEN", [(item_id, "A", "KG", 60)])
    _produce(db, sid, item_id, "A", "KG", 60, invoice)
    _retire(db, sid)
    assert _required(_project(db), "Chem Reopen") == 0.0

    db.execute("UPDATE sales SET stage = 'invoiced' WHERE id = %s", (sid,))
    db.commit()
    assert _project(db)["chem reopen"]["required_quantity"] == 40.0


def test_a_shipped_unpaid_pi_still_has_remaining_work(admin_client, db):
    """Shipment is not proof of production, and `paid` is an invoice status.

    Shipped with nothing produced keeps the full requirement; fully produced
    with payment outstanding has no remaining need.
    """
    _chem(db, "Chem Shipped", qty=0)
    sid, item_id = _one_activated(db, admin_client, "PI-PROJ-SHIP", "Chem Shipped", ordered=100)
    invoice = _invoice(db, sid, "INV-SHIP", [(item_id, "A", "KG", 100)])
    db.execute("INSERT INTO shipments (sale_id, ship_date, invoice_number) "
               "VALUES (%s, '2026-04-01', 'INV-SHIP')", (sid,))
    db.commit()
    assert _project(db)["chem shipped"]["projected_quantity"] == 100.0

    _produce(db, sid, item_id, "A", "KG", 100, invoice)
    assert _project(db)["chem shipped"]["projected_quantity"] == 0.0


# --------------------------------------------------------------------------- #
# 13-16 — the remainder rule and provenance
# --------------------------------------------------------------------------- #
def test_a_wont_ship_remainder_follows_non_void_invoiced_quantity(admin_client, db):
    """A marked line's target is `min(ordered, non-void invoiced)` — and voiding
    deletes the invoice, so the retained target falls to zero with it.
    """
    _chem(db, "Chem Remainder", qty=0)
    sid, item_id = _one_activated(db, admin_client, "PI-PROJ-REMAINDER",
                                  "Chem Remainder", ordered=100)
    invoice = _invoice(db, sid, "INV-REMAINDER", [(item_id, "A", "KG", 60)])
    assert set_sale_item_wont_ship(db, sid, item_id, 1) is True
    assert _project(db)["chem remainder"]["required_quantity"] == 60.0

    db.execute("DELETE FROM invoice_items WHERE invoice_id = %s", (invoice,))
    db.execute("DELETE FROM invoices WHERE id = %s", (invoice,))
    db.commit()
    assert _required(_project(db), "Chem Remainder") == 0.0


def test_an_invoice_recipe_requirement_is_not_subtracted_a_second_time(admin_client, db):
    """The demo formula prices the remaining output. An actual invoice recipe
    with different percentages does not add a second requirement, and its
    production is credited once.
    """
    chem_id = _chem(db, "Chem Double", qty=0)
    sid, item_id = _one_activated(db, admin_client, "PI-PROJ-DOUBLE", "Chem Double", ordered=100)
    invoice = _invoice(db, sid, "INV-DOUBLE", [(item_id, "A", "KG", 100)])

    # An invoice recipe that consumes only 25% per unit, and 40 units produced.
    client = db.execute("INSERT INTO companies (name) VALUES ('Double Co') RETURNING id").fetchone()[0]
    recipe = db.execute("INSERT INTO recipes (name, company_id, invoice_id) "
                        "VALUES ('Double Recipe', %s, %s) RETURNING id",
                        (client, invoice)).fetchone()[0]
    slot = db.execute("INSERT INTO recipe_products (recipe_id, product_name, unit) "
                      "VALUES (%s, 'A', 'KG') RETURNING id", (recipe,)).fetchone()[0]
    db.execute("INSERT INTO recipe_items (recipe_product_id, chemical_id, percentage) "
               "VALUES (%s, %s, 25)", (slot, chem_id))
    run = _produce(db, sid, item_id, "A", "KG", 40, invoice, recipe=recipe)
    db.execute("INSERT INTO production_run_items (run_id, chemical_id, chemical_name, "
               "required_qty, deducted_qty, unit) VALUES (%s, %s, 'Chem Double', 10, 10, 'KG')",
               (run, chem_id))
    db.commit()

    row = _project(db)["chem double"]
    assert row["required_quantity"] == 60.0, row    # 100 - 40 produced, at the DEMO 100%
    assert row["projected_quantity"] == 60.0, row


def test_an_ambiguous_historical_output_is_incomplete_not_credit(admin_client, db):
    """A run whose output cannot be attributed to one PI is unknown, so the
    affected figure is null and the plan is reported incomplete. Treating it as
    "not produced" or as "produced here" would both invent a number.
    """
    _chem(db, "Chem Ambiguous", qty=0)
    sid, item_id = _one_activated(db, admin_client, "PI-PROJ-AMBIG", "Chem Ambiguous",
                                  ordered=100, product="Alpha")
    invoice = _invoice(db, sid, "INV-AMBIG", [(item_id, "Alpha", "KG", 100)])
    run = _produce(db, sid, item_id, "Alpha", "KG", 40, invoice)

    # The same run also claims to belong to another PI selling the same name.
    other, other_item = _one_activated(db, admin_client, "PI-PROJ-AMBIG2", "Chem Ambiguous",
                                       ordered=100, product="Alpha")
    db.execute("INSERT INTO production_run_links (run_id, sale_id, invoice_id) "
               "VALUES (%s, %s, NULL)", (run, other))
    db.commit()

    row = _project(db)["chem ambiguous"]
    assert row["required_quantity"] is None, row
    assert row["projected_quantity"] is None, row
    assert _coverage(_svc().project_purchasing(db))["state"] == "incomplete"


def test_each_output_is_counted_once_however_many_links_reach_it(admin_client, db):
    """Two invoices of the same PI both point at one run: the 40 produced is
    credited once (60 remaining), never twice (20 remaining).
    """
    _chem(db, "Chem Dedup", qty=0)
    sid, item_id = _one_activated(db, admin_client, "PI-PROJ-DEDUP", "Chem Dedup", ordered=100)
    first = _invoice(db, sid, "INV-DEDUP-1", [(item_id, "A", "KG", 50)])
    second = _invoice(db, sid, "INV-DEDUP-2", [(item_id, "A", "KG", 50)])
    run = _produce(db, sid, item_id, "A", "KG", 40, first)
    db.execute("INSERT INTO production_run_links (run_id, sale_id, invoice_id) "
               "VALUES (%s, %s, %s)", (run, sid, second))
    db.commit()

    row = _project(db)["chem dedup"]
    assert row["required_quantity"] == 60.0, row
    assert row["projected_quantity"] == 60.0, row


# --------------------------------------------------------------------------- #
# 17 — coverage when nothing is planned
# --------------------------------------------------------------------------- #
def test_open_requirements_with_no_active_plans_are_a_state_not_a_zero(admin_client, db):
    """Unplanned demand is reported as unplanned. A zero here would tell the
    buyer there is nothing to order when nobody has planned it yet.
    """
    _chem(db, "Chem Unplanned", qty=50)
    sid = _pi(db, "PI-PROJ-NOPLAN", [("Waiting", 100, "KG")])

    projection = _svc().project_purchasing(db)
    coverage = _coverage(projection)
    assert coverage["state"] == "no_active_plans", coverage
    assert sid in {row["sale_id"] for row in coverage["open_pis_without_plan"]}, coverage
    # No active plan means no computed shortage to publish: the material is
    # either absent or unknown, never an authoritative "0 to buy".
    row = _rows(projection).get("chem unplanned")
    assert row is None or row["projected_quantity"] is None, row
