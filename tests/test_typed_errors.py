"""Phase 4-A — the HTTP status must come from the exception TYPE, not the wording.

Before this phase ``flask_app._state_error_status`` classified a service
refusal by matching the *words* of its message::

    "invoice already"                  -> 409
    "invoice not produced"             -> 409
    "Invoice not found"                -> 404
    "linked sale " / "linked invoice " -> 404
    everything else                    -> 400

Re-typing "invoice not produced yet" as "this invoice has not been produced"
silently turned a 409 into a 400 and nothing noticed. The same shape lived in
``_invoice_item_error`` ("remain"/"exceed" -> 409, exact-match -> 404), and
then in the LC surface in three more places: ``_lc_attach_error``, the inline
``"already exists" in msg.lower()`` in ``POST /api/lcs``, and the five-way
substring list in ``POST /api/lcs/<id>/move``.

Those tests pin the status to the TYPE:

* every typed class carries its own ``status_code``;
* ``status_for`` is the single entry point and falls back to 400 for a plain
  ``ValueError``;
* the status is still correct when the message is deliberately reworded — the
  load-bearing test; without it this change is cosmetic;
* the real HTTP routes still answer 409/404 with byte-identical error text;
* the LC surface answers a reworded refusal correctly **through the route**,
  which is the only place the old mappers were consulted;
* cross-tenant attach is indistinguishable from a missing sale (see
  ``test_attach_does_not_disclose_another_tenants_sale``).

The remediation pass that followed the Oracle gate closed three gaps in the
above: the 5% tolerance breach answered 400 on two of its three routes and
409 on the third; ``sales.InvoicedLineConflict`` claimed a 409 in its
docstring while sitting outside the hierarchy, kept there only by three
handwritten handlers; and two docstrings justified the hierarchy by
requirements that do not exist yet.
"""

import ast
import pathlib

import pytest

from raas_tracker._exceptions import (
    InvoiceAlreadyAdvanced,
    InvoiceNotFound,
    InvoiceNotProduced,
    LcNotFound,
    LcNotReadyForShipment,
    LcNotReadyPastShipment,
    LcNumberAlreadyExists,
    LcSaleNotFound,
    LifecycleConflict,
    LinkedInvoiceNotFound,
    LinkedSaleNotFound,
    SaleItemNotFound,
    ServiceError,
    ToleranceExceeded,
    status_for,
)
from raas_tracker.sales import InvoicedLineConflict


# Every class -> the status it must report, regardless of its message.
_TYPED = [
    (InvoiceAlreadyAdvanced, 409),
    (InvoiceNotProduced, 409),
    (ToleranceExceeded, 409),
    (InvoicedLineConflict, 409),
    (InvoiceNotFound, 404),
    (LinkedSaleNotFound, 404),
    (LinkedInvoiceNotFound, 404),
    (SaleItemNotFound, 404),
    (LcNumberAlreadyExists, 409),
    (LcNotReadyForShipment, 409),
    (LcNotReadyPastShipment, 409),
    (LcNotFound, 404),
    (LcSaleNotFound, 404),
]

# A message that matches NONE of the prefixes the deleted mappers used to
# match on. If any mapper were still reading the wording, this string would
# fall through to 400.
_UNMATCHABLE = "refusal: 42 (wording chosen to match no legacy prefix)"


# ---------- helpers (patterns from tests/test_production_atomicity.py) ----------


def _company(admin_client, db, name):
    r = admin_client.post("/api/companies", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return db.execute("SELECT id FROM companies WHERE name = %s", (name,)).fetchone()[0]


def _sale(admin_client, company_id, pi, product, qty=10, price=5):
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": pi, "client_name": "x", "company_id": company_id},
        "items": [{"product_name": product, "quantity": qty,
                   "unit_price": price, "unit": "KG"}]})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _invoice(admin_client, db, sale_id, number):
    lines = [{"sale_item_id": r[0], "quantity": float(r[1])}
             for r in db.execute(
                 "SELECT id, quantity FROM sale_items WHERE sale_id = %s "
                 "ORDER BY id", (sale_id,)).fetchall()]
    r = admin_client.post(f"/api/sales/{sale_id}/invoices",
                          json={"invoice_number": number,
                                "invoice_date": "2026-01-15",
                                "lines": lines})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["invoice_id"]


def _sale_item_id(db, sale_id):
    return db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id", (sale_id,)
    ).fetchone()[0]


def _recipe(admin_client, company_id, product, recipe_name, chem_name, pct=10):
    r = admin_client.post("/api/chemicals",
                          json={"name": chem_name, "qty": 100, "unit": "KG"})
    assert r.status_code == 200, r.get_json()
    r = admin_client.post("/api/recipes", json={
        "name": recipe_name, "yield": 100, "company_id": company_id,
        "product_name": product})
    assert r.status_code == 201, r.get_json()
    r = admin_client.post(f"/api/recipes/{recipe_name}/items", json={
        "chemical": chem_name, "percentage": pct, "company_id": company_id})
    assert r.status_code == 200, r.get_json()


def _invoice_status(db, invoice_id):
    return db.execute("SELECT status FROM invoices WHERE id = %s",
                      (invoice_id,)).fetchone()[0]


# ---------- 1. every class carries its own status ----------


@pytest.mark.parametrize("exc_cls,expected", _TYPED)
def test_each_class_maps_to_its_intended_status(exc_cls, expected):
    assert exc_cls.status_code == expected
    assert status_for(exc_cls("anything at all")) == expected


@pytest.mark.parametrize("exc_cls,_expected", _TYPED)
def test_every_typed_error_is_a_value_error(exc_cls, _expected):
    """Backward compatibility: existing `except ValueError` handlers must
    keep catching these, or every route silently turns a refusal into a 500."""
    exc = exc_cls("x")
    assert isinstance(exc, ValueError)
    assert isinstance(exc, ServiceError)
    with pytest.raises(ValueError):
        raise exc


# ---------- 2. plain ValueError fallback ----------


@pytest.mark.parametrize("message", [
    "quantity must be finite and > 0",
    "",
    "Invoice not found-ish",
    "invoice already-ish",
])
def test_status_for_plain_value_error_is_400(message):
    assert status_for(ValueError(message)) == 400


def test_status_for_unrelated_exception_is_400():
    assert status_for(KeyError("invoice already shipped")) == 400


# ---------- 3. THE LOAD-BEARING TEST: wording is irrelevant ----------


@pytest.mark.parametrize("exc_cls,expected", _TYPED)
def test_status_survives_a_reworded_message(exc_cls, expected):
    """The whole point of the phase: a reworded refusal keeps its status.

    Against the deleted mappers every one of these returned 400.
    """
    assert status_for(exc_cls(_UNMATCHABLE)) == expected


def test_no_legacy_prefix_matcher_remains():
    """The string-classification helpers are gone, not deprecated."""
    import flask_app

    assert not hasattr(flask_app, "_state_error_status")
    assert not hasattr(flask_app, "_invoice_item_error")
    assert not hasattr(flask_app, "_lc_attach_error")


# The LC surface carried three more classifiers. Two were bare substrings with
# no helper name to grep for, so this walks the AST: a textual scan would also
# match the comments that document what was removed.
#
# The `"recipes:" / "invoices:" / "production:"` triple is the dangerous one —
# shared vocabulary. Any readiness message that ever grew a colon in those
# words flipped a 400 into a 409 with no code change at all.
_LEGACY_NEEDLES = {
    "already exists",
    "not ready for shipment",
    "not ready to go past shipment",
    "recipes:",
    "invoices:",
    "production:",
}

# `_lc_attach_error` matched by exact-equality / prefix / suffix instead.
_LEGACY_AFFIXES = {("startswith", "sale "), ("endswith", "not found")}


def _flask_app_source():
    import flask_app

    return flask_app, pathlib.Path(flask_app.__file__).read_text(encoding="utf-8")


def _string_membership_tests(src):
    """Every `"literal" in <expr>` / `"literal" not in <expr>` in the module.

    Comments and docstrings are not nodes, so prose describing a deleted
    matcher cannot satisfy or trip this.
    """
    hits = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Compare):
            continue
        for op, comparator in zip(node.ops, node.comparators):
            if not isinstance(op, (ast.In, ast.NotIn)):
                continue
            for side in (node.left, comparator):
                if isinstance(side, ast.Constant) and isinstance(side.value, str):
                    hits.append((side.value, node.lineno))
    return hits


def _affix_calls(src):
    """Every `.startswith("literal")` / `.endswith("literal")` in the module."""
    hits = []
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("startswith", "endswith")
                and node.args):
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            hits.append((node.func.attr, first.value, node.lineno))
    return hits


def test_no_lc_substring_classifier_remains_in_source():
    """None of the six legacy needles (nor `.startswith("sale ")` / `.endswith("not found")`) appears as a string-membership predicate in `flask_app.py`. This checks those specific literals, not the general property of "no status classifier exists"
    text remains in `flask_app.py`.

    The guarantee is exactly that sentence. The scan is an `ast.Compare` with
    an `In`/`NotIn` operator and a string `Constant` operand, over one file —
    so it does NOT catch a loop over a `Name`, a dict of prefixes keyed on a
    `Name`, `re.match`, or a mapper living inside `raas_tracker/`. It is a
    tripwire for the exact shape that was removed, not a proof that no
    message-based classification exists anywhere. Comments and docstrings are
    not nodes, so prose describing a deleted matcher cannot trip it.
    """
    _mod, src = _flask_app_source()
    survivors = [
        (needle, line) for needle, line in _string_membership_tests(src)
        if needle in _LEGACY_NEEDLES
    ]
    assert survivors == [], f"LC substring classifiers still present: {survivors}"


def test_no_lc_affix_classifier_remains_in_source():
    """`_lc_attach_error`'s startswith/endswith pair, gone the same way."""
    _mod, src = _flask_app_source()
    survivors = [
        hit for hit in _affix_calls(src) if (hit[0], hit[1]) in _LEGACY_AFFIXES
    ]
    assert survivors == [], f"LC affix classifiers still present: {survivors}"


def test_status_for_is_the_only_classifier_the_lc_routes_use():
    """The LC routes must not hand-roll a second status decision.

    Structural, not `callable()`: an earlier draft of this test only asserted
    that ``status_for`` exists, which cannot detect the very thing its name
    claims to guard. What actually constitutes a second decision is an
    ``except`` handler on a typed exception that returns a hardcoded status,
    so this walks the handlers the same way the InvoicedLineConflict guard does.

    One literal is deliberate and documented: ``api_move_lc`` returns 409 from
    ``except LifecycleConflict`` because that route's 409 body carries readiness
    detail ``status_for`` cannot express. It is in agreement with the hierarchy,
    so it is a redundant literal rather than a divergent decision.
    """
    _mod, src = _flask_app_source()
    from raas_tracker import _exceptions as ex

    typed = {name for name in dir(ex)
             if isinstance(getattr(ex, name), type)
             and issubclass(getattr(ex, name), ex.ServiceError)
             and name != "ServiceError"}
    redundant = sorted({
        handler for handler in _except_handler_type_names(src)
        if handler == "LifecycleConflict"
    })
    assert redundant == ["LifecycleConflict"], (
        "an extra LifecycleConflict handler appeared - confirm it is the one "
        f"documented deliberate literal, got: {redundant}")


def _except_handler_type_names(src):
    """Every `except <Name>:` type name in the module."""
    names = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if handler.type is None:
                continue
            name = handler.type
            if isinstance(name, ast.Name):
                names.append(name.id)
            elif isinstance(name, ast.Tuple):
                names.extend(el.id for el in name.elts
                             if isinstance(el, ast.Name))
    return names


def test_invoiced_line_conflict_is_a_typed_409():
    """It claimed "routes map this to 409, never 400" while carrying no status.

    Three handlers hardcoded the 409 as a workaround for `status_for` answering
    400 — the type was outside the hierarchy, so the single entry point could
    not see it. Folding it into `LifecycleConflict` is what makes that claim
    true instead of enforced by three copies of a literal.
    """
    assert issubclass(InvoicedLineConflict, LifecycleConflict)
    assert InvoicedLineConflict.status_code == 409
    assert status_for(InvoicedLineConflict("anything at all")) == 409
    assert status_for(InvoicedLineConflict(_UNMATCHABLE)) == 409


def test_no_bespoke_invoiced_line_conflict_handler_remains_in_flask_app():
    """`status_for` must be the single entry point the routes go through.

    Structural: an `except InvoicedLineConflict:` block IS the workaround this
    pass removed, whatever status the handler body returned.
    """
    _mod, src = _flask_app_source()
    survivors = [n for n in _except_handler_type_names(src)
                 if n == "InvoicedLineConflict"]
    assert survivors == [], (
        f"bespoke InvoicedLineConflict handlers still present: {survivors}")


# ---------- 4. real HTTP routes ----------


def test_produce_route_409_for_terminal_invoice(admin_client, db):
    """recipes.py:703 — producing against a shipped invoice is a 409."""
    cid = _company(admin_client, db, "TypedProdCo")
    sid = _sale(admin_client, cid, "PI-TP-1", "TypedProd")
    _recipe(admin_client, cid, "TypedProd", "TypedRecipe", "TypedChem")
    inv = _invoice(admin_client, db, sid, "INV-TP-1")
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/ship",
                          json={"actual_ship_date": "2026-10-20"})
    assert r.status_code == 201, r.get_json()

    r = admin_client.post("/api/recipes/TypedRecipe/produce", json={
        "production_qty": 10, "company_id": cid,
        "sale_ids": [sid], "invoice_ids": [inv]})
    assert r.status_code == 409, r.get_json()
    assert r.get_json()["error"] == "invoice already shipped"
    assert _invoice_status(db, inv) == "shipped"


def test_produce_route_404_for_missing_linked_sale(admin_client, db):
    """recipes.py:530 — a sale id outside this company is a 404."""
    cid_a = _company(admin_client, db, "TypedOwnA")
    _sale(admin_client, cid_a, "PI-TOA-1", "TypedProdA")
    _recipe(admin_client, cid_a, "TypedProdA", "TypedRecipeA", "TypedChemA")
    cid_b = _company(admin_client, db, "TypedOwnB")
    sid_b = _sale(admin_client, cid_b, "PI-TOB-1", "TypedProdB")

    r = admin_client.post("/api/recipes/TypedRecipeA/produce", json={
        "production_qty": 10, "company_id": cid_a,
        "sale_ids": [sid_b]})
    assert r.status_code == 404, r.get_json()
    assert r.get_json()["error"] == (
        f"linked sale {sid_b} not found for this company")


def test_produce_route_404_for_missing_linked_invoice(admin_client, db):
    """recipes.py:547 — own sale, but the invoice belongs to another company."""
    cid_a = _company(admin_client, db, "TypedInvA")
    sid_a = _sale(admin_client, cid_a, "PI-TIA-1", "TypedProdAI")
    _recipe(admin_client, cid_a, "TypedProdAI", "TypedRecipeAI", "TypedChemAI")
    cid_b = _company(admin_client, db, "TypedInvB")
    sid_b = _sale(admin_client, cid_b, "PI-TIB-1", "TypedProdBI")
    inv_b = _invoice(admin_client, db, sid_b, "INV-TIB-1")

    r = admin_client.post("/api/recipes/TypedRecipeAI/produce", json={
        "production_qty": 10, "company_id": cid_a,
        "sale_ids": [sid_a], "invoice_ids": [inv_b]})
    assert r.status_code == 404, r.get_json()
    assert r.get_json()["error"] == (
        f"linked invoice {inv_b} not found for this company")


def test_book_route_409_when_not_produced(admin_client, db):
    """sales.py:2191 — booking a shipment before production is a 409."""
    cid = _company(admin_client, db, "TypedBookCo")
    sid = _sale(admin_client, cid, "PI-TB-1", "TypedBookProd")
    _recipe(admin_client, cid, "TypedBookProd", "TypedBookRecipe", "TypedBookChem")
    inv = _invoice(admin_client, db, sid, "INV-TB-1")

    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "2026-11-01"})
    assert r.status_code == 409, r.get_json()
    assert r.get_json()["error"].startswith(
        "invoice not produced yet — run production from the recipe page for: ")
    assert _invoice_status(db, inv) == "planned"


def test_ship_route_409_for_terminal_invoice(admin_client, db):
    """sales.py:2224 — shipping an already-paid invoice is a 409."""
    cid = _company(admin_client, db, "TypedShipCo")
    sid = _sale(admin_client, cid, "PI-TS-1", "TypedShipProd")
    inv = _invoice(admin_client, db, sid, "INV-TS-1")
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/pay",
                          json={"payment_amount": 50, "payment_date": "2026-10-25"})
    assert r.status_code == 201, r.get_json()

    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/ship",
                          json={"actual_ship_date": "2026-11-02"})
    assert r.status_code == 409, r.get_json()
    assert r.get_json()["error"] == "invoice already paid"


def test_invoice_item_route_409_over_tolerance(admin_client, db):
    """sales.py:1733 — beyond the 5% over-invoice ceiling is a 409."""
    cid = _company(admin_client, db, "TypedTolCo")
    sid = _sale(admin_client, cid, "PI-TT-1", "TypedTolProd")
    item_id = _sale_item_id(db, sid)
    inv = _invoice(admin_client, db, sid, "INV-TT-1")

    r = admin_client.post(f"/api/invoices/{inv}/items",
                          json={"sale_item_id": item_id, "quantity": 11})
    assert r.status_code == 409, r.get_json()
    # Seeded invoice already carries the PI line's full 10, so the add brings
    # the cumulative to 21 against a 10.5 ceiling.
    assert r.get_json()["error"] == (
        f"PI line {item_id}: invoiced 21.0 exceeds 10.500000 "
        "(PI qty 10.0 + 5%)")


def test_tolerance_breach_is_409_on_every_invoice_write_route(admin_client, db):
    """ONE condition -> ONE status, on all three writers of an invoice line.

    `ToleranceExceeded` is a conflict with what the sale has already been
    invoiced for, so it is a 409 (RFC 9110: retrying the identical body will
    never succeed). `POST /api/invoices/<id>/items` already answered 409; the
    invoice-create and invoices-batch routes hardcoded 400 for the *same*
    refusal, so a client could not tell a fixable payload error from an
    unfixable state conflict — it depended on which door it came in by.
    """
    cid = _company(admin_client, db, "TypedTol3Co")
    sid = _sale(admin_client, cid, "PI-TT3-1", "TypedTol3Prod")
    item_id = _sale_item_id(db, sid)
    # The seeded invoice already carries the PI line's full 10, so every
    # request below trips the same 10.5 ceiling.
    inv = _invoice(admin_client, db, sid, "INV-TT3-SEED")

    r_create = admin_client.post(f"/api/sales/{sid}/invoices", json={
        "invoice_number": "INV-TT3-A",
        "invoice_date": "2026-01-15",
        "lines": [{"sale_item_id": item_id, "quantity": 1}]})
    r_batch = admin_client.post(f"/api/sales/{sid}/invoices/batch", json={
        "invoices": [{"invoice_number": "INV-TT3-B",
                      "invoice_date": "2026-01-15",
                      "lines": [{"sale_item_id": item_id, "quantity": 1}]}]})
    r_item = admin_client.post(f"/api/invoices/{inv}/items",
                               json={"sale_item_id": item_id, "quantity": 1})

    assert [r_create.status_code, r_batch.status_code, r_item.status_code] == [
        409, 409, 409], [r_create.get_json(), r_batch.get_json(),
                         r_item.get_json()]
    # Same refusal text on all three, so the status is the only thing that
    # could differ between them.
    for r in (r_create, r_batch, r_item):
        assert r.get_json()["error"].startswith(
            f"PI line {item_id}: invoiced 11.0 exceeds 10.500000"), r.get_json()

    # The refusals rolled back: neither create left a header behind.
    assert {row[0] for row in db.execute(
        "SELECT invoice_number FROM invoices WHERE sale_id = %s",
        (sid,)).fetchall()} == {"INV-TT3-SEED"}


def test_invoice_item_route_404_missing_sale_item(admin_client, db):
    """sales.py:2592 — a sale item that does not exist is a 404."""
    cid = _company(admin_client, db, "TypedItemCo")
    sid = _sale(admin_client, cid, "PI-TI-1", "TypedItemProd")
    inv = _invoice(admin_client, db, sid, "INV-TI-1")

    r = admin_client.post(f"/api/invoices/{inv}/items",
                          json={"sale_item_id": 999999, "quantity": 1})
    assert r.status_code == 404, r.get_json()
    assert r.get_json()["error"] == "sale item not found"


def test_invoice_item_route_still_400_for_plain_validation(admin_client, db):
    """A plain ValueError must stay a 400 — typing is not a status upgrade."""
    cid = _company(admin_client, db, "TypedPlainCo")
    sid = _sale(admin_client, cid, "PI-TPL-1", "TypedPlainProd")
    other = _sale(admin_client, cid, "PI-TPL-2", "TypedPlainProd2")
    item_id = _sale_item_id(db, other)
    inv = _invoice(admin_client, db, sid, "INV-TPL-1")

    r = admin_client.post(f"/api/invoices/{inv}/items",
                          json={"sale_item_id": item_id, "quantity": 1})
    assert r.status_code == 400, r.get_json()
    assert r.get_json()["error"] == (
        "sale item does not belong to this invoice's sale")


def _two_line_sale(admin_client, company_id, pi, product):
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": pi, "client_name": "x", "company_id": company_id},
        "items": [{"product_name": product + "A", "quantity": 4,
                   "unit_price": 25, "unit": "KG"},
                  {"product_name": product + "B", "quantity": 2,
                   "unit_price": 15, "unit": "KG"}]})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def test_invoiced_line_conflict_routes_are_409_through_status_for(admin_client, db):
    """Behaviour-preserving fold: 409 before, 409 after, now from the type.

    These three routes each carried their own `except InvoicedLineConflict`
    block with a hardcoded 409 — the only reason they were not 400. With the
    class in the hierarchy they reach the same status through `status_for`, so
    this pins that deleting the three handlers changed no status.
    """
    cid = _company(admin_client, db, "TypedILCCo")
    sid = _two_line_sale(admin_client, cid, "PI-ILC-1", "TypedILC")
    ids = [row[0] for row in db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id",
        (sid,)).fetchall()]
    invoiced_id = ids[0]
    _invoice(admin_client, db, sid, "INV-ILC-1")

    r_delete = admin_client.delete(f"/api/sales/{sid}/items/{invoiced_id}")
    r_patch = admin_client.put(f"/api/sales/{sid}/items/{invoiced_id}",
                               json={"quantity": 8})
    r_bulk = admin_client.put(f"/api/sales/{sid}", json={
        "header": {"client_name": "x", "pi_number": "PI-ILC-1",
                   "company_id": cid},
        "items": [{"id": ids[1], "product_name": "TypedILCB", "quantity": 2,
                   "unit_price": 15, "unit": "KG"}],
        "removedIds": [invoiced_id]})

    assert [r_delete.status_code, r_patch.status_code, r_bulk.status_code] == [
        409, 409, 409], [r_delete.get_json(), r_patch.get_json(),
                         r_bulk.get_json()]
    for r in (r_delete, r_patch, r_bulk):
        assert "invoice lines" in r.get_json()["error"], r.get_json()

    # Nothing moved: the refusals rolled back rather than committing a
    # half-applied edit (the bulk PUT writes the header before it checks).
    assert [row[0] for row in db.execute(
        "SELECT product_name FROM sale_items WHERE sale_id = %s ORDER BY id",
        (sid,)).fetchall()] == ["TypedILCA", "TypedILCB"]
    assert db.execute("SELECT quantity FROM sale_items WHERE id = %s",
                      (invoiced_id,)).fetchone()[0] == 4


# ---------- 5. service-level sites with no reachable route ----------


def test_ship_invoice_missing_invoice_raises_typed_not_found(db):
    """sales.py:2220 — unreachable via HTTP (the route checks first) but it
    still must carry its status on the exception."""
    from raas_tracker.sales import ship_invoice

    with pytest.raises(InvoiceNotFound) as ei:
        ship_invoice(db, 999999, "2026-11-01", "INV-GHOST")
    assert str(ei.value) == "Invoice not found"
    assert status_for(ei.value) == 404


def test_mark_invoice_paid_missing_invoice_raises_typed_not_found(db):
    """sales.py:2383."""
    from raas_tracker.sales import mark_invoice_paid

    with pytest.raises(InvoiceNotFound) as ei:
        mark_invoice_paid(db, 999999, 10, "2026-11-01")
    assert str(ei.value) == "Invoice not found"
    assert status_for(ei.value) == 404


def test_update_invoice_status_backward_move_is_a_typed_conflict(db):
    """sales.py:2141 — the shared forward-only guard every lifecycle writer
    funnels through, so its status must not depend on wording either."""
    from raas_tracker import sales as salesmod

    co = db.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id",
        ("TypedFwdCo",)).fetchone()[0]
    db.commit()
    sid = salesmod.add_sale(
        db, {"pi_number": "PI-TF-1", "client_name": "x", "company_id": co},
        [{"product_name": "TypedFwdProd", "quantity": 10, "unit_price": 5}])
    item = db.execute("SELECT id FROM sale_items WHERE sale_id = %s",
                      (sid,)).fetchone()[0]
    inv = salesmod.create_invoice(
        db, sid, "INV-TF-1",
        lines=[{"sale_item_id": item, "quantity": 10}],
        invoice_date="2026-01-15")["invoice_id"]
    assert salesmod.update_invoice_status(db, inv, "produced") is True

    with pytest.raises(InvoiceAlreadyAdvanced) as ei:
        salesmod.update_invoice_status(db, inv, "planned")
    assert str(ei.value) == "invoice already produced"
    assert status_for(ei.value) == 409


def test_assert_invoice_mutable_missing_invoice_is_typed(db):
    """sales.py:1745 — the frozen-line guard's own not-found."""
    from raas_tracker.sales import _assert_invoice_mutable

    with pytest.raises(InvoiceNotFound) as ei:
        _assert_invoice_mutable(db, 999999)
    assert str(ei.value) == "invoice not found"
    assert status_for(ei.value) == 404


# ---------- 6. LC surface: the three remaining mappers ----------
#
#   flask_app._lc_attach_error            "lc not found" / "sale … not found"
#   api_create_lc's inline branch         "already exists" in msg.lower()
#   api_move_lc's five-way substring list "not ready for shipment" / "recipes:"
#                                         "invoices:" / "not ready to go past
#                                         shipment" / "production:"
#
# Every status below is asserted through the real HTTP route.


def _mk_lc(admin_client, company_id, lc_number, **kw):
    r = admin_client.post("/api/lcs",
                          json={"company_id": company_id,
                                "lc_number": lc_number, **kw})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _raiser(exc):
    """A service stub that refuses with `exc` however it is called."""
    def _boom(*_args, **_kwargs):
        raise exc
    return _boom


# A readiness snapshot with every barrier satisfied, so a patched
# `move_lc_stage` is actually reached instead of the route's own pre-check
# short-circuiting first.
_ALL_READY = {"invoices_ok": True, "recipes_ok": True, "produced_ok": True,
              "detail": {}}


# ---------- 6a. real routes, real service refusals ----------


def test_lc_create_route_409_for_duplicate_number(admin_client, db):
    """lcs.py:72 — a duplicate LC number for the same company is a 409.

    Used to be `if "already exists" in msg.lower()`.
    """
    cid = _company(admin_client, db, "TypedLcDupCo")
    _mk_lc(admin_client, cid, "LC-TLD-1")

    r = admin_client.post("/api/lcs",
                          json={"company_id": cid, "lc_number": "LC-TLD-1"})
    assert r.status_code == 409, r.get_json()
    assert "already exists" in r.get_json()["error"]


def test_lc_create_route_still_400_for_plain_validation(admin_client, db):
    """Typing the duplicate must not upgrade unrelated refusals."""
    cid = _company(admin_client, db, "TypedLcPlainCo")
    r = admin_client.post("/api/lcs", json={"company_id": cid})
    assert r.status_code == 400, r.get_json()
    assert isinstance(r.get_json().get("details"), list)


def test_lc_attach_route_404_for_missing_lc(admin_client, db):
    """lcs.py:168 — no LC with that id."""
    cid = _company(admin_client, db, "TypedLcNoLcCo")
    sid = _sale(admin_client, cid, "PI-TNL-1", "TypedLcNoLcProd")

    r = admin_client.post("/api/lcs/987654321/pis", json={"sale_ids": [sid]})
    assert r.status_code == 404, r.get_json()
    assert r.get_json()["error"] == "LC not found"


def test_lc_attach_route_404_for_missing_sale(admin_client, db):
    """lcs.py:177 — no sale with that id."""
    cid = _company(admin_client, db, "TypedLcNoSaleCo")
    lc = _mk_lc(admin_client, cid, "LC-TNS-1")

    r = admin_client.post(f"/api/lcs/{lc['id']}/pis",
                          json={"sale_ids": [987654321]})
    assert r.status_code == 404, r.get_json()
    assert r.get_json()["error"] == "sale 987654321 not found"


def test_lc_attach_route_400_for_plain_validation(admin_client, db):
    """Empty sale_ids never reaches the service; stays a 400 shape error."""
    cid = _company(admin_client, db, "TypedLcAttachValCo")
    lc = _mk_lc(admin_client, cid, "LC-TAV-1")
    r = admin_client.post(f"/api/lcs/{lc['id']}/pis", json={"sale_ids": []})
    assert r.status_code == 400, r.get_json()
    assert isinstance(r.get_json().get("details"), list)


def test_lc_move_route_409_when_not_ready_for_shipment(admin_client, db):
    """lcs.py:318 — crossing into shipment_ongoing with no invoice is a 409."""
    cid = _company(admin_client, db, "TypedLcShipCo")
    lc = _mk_lc(admin_client, cid, "LC-TLS-1")
    sid = _sale(admin_client, cid, "PI-TLS-1", "TypedLcShipProd")
    assert admin_client.post(f"/api/lcs/{lc['id']}/pis",
                             json={"sale_ids": [sid]}).status_code == 200

    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "shipment_ongoing"})
    assert r.status_code == 409, r.get_json()
    body = r.get_json()
    assert body["error"].startswith("LC is not ready for shipment: ")
    assert body["invoices_ok"] is False
    assert body["missing"]
    # the refusal moved nothing
    assert admin_client.get(
        f"/api/lcs/{lc['id']}").get_json()["stage"] == "lc_received"


def test_lc_move_route_400_for_invalid_stage(admin_client, db):
    """An unparseable target stage is a plain ValueError -> 400, not a 409."""
    cid = _company(admin_client, db, "TypedLcStageCo")
    lc = _mk_lc(admin_client, cid, "LC-TSTG-1")

    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "not_a_stage"})
    assert r.status_code == 400, r.get_json()
    assert r.get_json()["error"] == "invalid stage: not_a_stage"


# ---------- 6b. THE LOAD-BEARING ROUTE TEST: wording is irrelevant ----------
#
# `_UNMATCHABLE` matches none of the deleted prefixes. Under the old mappers
# every request below answered 400. These drive the real routes with the real
# handlers and a refusal carrying that message, so a status that moves means
# something is still reading the text.


def test_route_409_survives_a_reworded_duplicate_refusal(
        admin_client, db, monkeypatch):
    import flask_app

    cid = _company(admin_client, db, "TypedLcWordDupCo")
    monkeypatch.setattr(flask_app, "create_lc",
                        _raiser(LcNumberAlreadyExists(_UNMATCHABLE)))

    r = admin_client.post("/api/lcs",
                          json={"company_id": cid, "lc_number": "LC-TWD-1"})
    assert r.status_code == 409, r.get_json()
    assert r.get_json()["error"] == _UNMATCHABLE


@pytest.mark.parametrize("exc_cls", [LcNotFound, LcSaleNotFound])
def test_route_404_survives_a_reworded_attach_refusal(
        exc_cls, admin_client, db, monkeypatch):
    import flask_app

    cid = _company(admin_client, db, "TypedLcWordAttCo")
    lc = _mk_lc(admin_client, cid, f"LC-TWA-{exc_cls.__name__}")
    monkeypatch.setattr(flask_app, "attach_pis",
                        _raiser(exc_cls(_UNMATCHABLE)))

    r = admin_client.post(f"/api/lcs/{lc['id']}/pis", json={"sale_ids": [1]})
    assert r.status_code == 404, r.get_json()
    assert r.get_json()["error"] == _UNMATCHABLE


@pytest.mark.parametrize("exc_cls", [LcNotReadyForShipment,
                                     LcNotReadyPastShipment])
def test_route_409_survives_a_reworded_readiness_refusal(
        exc_cls, admin_client, db, monkeypatch):
    import flask_app

    cid = _company(admin_client, db, "TypedLcWordRdyCo")
    lc = _mk_lc(admin_client, cid, f"LC-TWR-{exc_cls.__name__}")
    # Let the move reach the service so the patched refusal is the one that
    # decides the status.
    monkeypatch.setattr(flask_app, "check_lc_shipment_ready",
                        lambda *a, **kw: dict(_ALL_READY))
    monkeypatch.setattr(flask_app, "move_lc_stage",
                        _raiser(exc_cls(_UNMATCHABLE)))

    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "payment_due"})
    assert r.status_code == 409, r.get_json()
    assert r.get_json()["error"] == _UNMATCHABLE


def test_route_400_survives_a_reworded_plain_refusal(admin_client, db,
                                                      monkeypatch):
    """The negative control: an untyped refusal is still a 400.

    Without this the tests above would also pass if the route simply answered
    409 (or 404) to everything.
    """
    import flask_app

    cid = _company(admin_client, db, "TypedLcWordPlainCo")
    lc = _mk_lc(admin_client, cid, "LC-TWP-1")
    monkeypatch.setattr(flask_app, "move_lc_stage",
                        _raiser(ValueError(_UNMATCHABLE)))

    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "pi_issued"})
    assert r.status_code == 400, r.get_json()
    assert r.get_json()["error"] == _UNMATCHABLE


# ---------- 6c. the LC service gates carry their own status ----------


def test_move_lc_stage_shipment_gate_is_typed(admin_client, db):
    """lcs.py:324 — the readiness refusal is typed where it is constructed."""
    from raas_tracker import lcs as lcsmod

    cid = _company(admin_client, db, "TypedLcSvcShipCo")
    lc = _mk_lc(admin_client, cid, "LC-SVC-SHIP-1")
    sid = _sale(admin_client, cid, "PI-SVCS-1", "TypedLcSvcShipProd")
    lcsmod.attach_pis(db, lc["id"], [sid])
    db.commit()

    with pytest.raises(LcNotReadyForShipment) as ei:
        lcsmod.move_lc_stage(db, lc["id"], "shipment_ongoing")
    assert str(ei.value).startswith("LC is not ready for shipment: ")
    assert status_for(ei.value) == 409


def test_move_lc_stage_payment_gate_is_typed(admin_client, db):
    """lcs.py:347 — invoiced but never produced: the other half of the 409.

    The old route matched this on "recipes:" / "production:" — bare vocabulary
    shared with half the readiness payload.
    """
    from raas_tracker import lcs as lcsmod

    cid = _company(admin_client, db, "TypedLcSvcPayCo")
    lc = _mk_lc(admin_client, cid, "LC-SVC-PAY-1")
    sid = _sale(admin_client, cid, "PI-SVCP-1", "TypedLcSvcPayProd")
    lcsmod.attach_pis(db, lc["id"], [sid])
    _invoice(admin_client, db, sid, "INV-SVCP-1")
    db.commit()

    with pytest.raises(LcNotReadyPastShipment) as ei:
        lcsmod.move_lc_stage(db, lc["id"], "payment_due")
    assert str(ei.value).startswith("LC is not ready to go past shipment: ")
    assert status_for(ei.value) == 409


# ---------- 6d. SECURITY: no cross-tenant enumeration oracle ----------
#
# `attach_pis` used to raise "sale N belongs to a different company" and the
# route mapped that to **400** while a sale that does not exist mapped to
# **404** — so walking `sale_id`s told an attacker which ids are real and
# belong to somebody else. Both must now be the same 404 with the same body.


def test_attach_does_not_disclose_another_tenants_sale(admin_client, db):
    cid_a = _company(admin_client, db, "TypedEnumOwnA")
    cid_b = _company(admin_client, db, "TypedEnumOtherB")
    lc = _mk_lc(admin_client, cid_a, "LC-TENUM-1")
    foreign = _sale(admin_client, cid_b, "PI-TENUM-1", "TypedEnumProd")
    absent = 987654321

    r_foreign = admin_client.post(f"/api/lcs/{lc['id']}/pis",
                                  json={"sale_ids": [foreign]})
    r_absent = admin_client.post(f"/api/lcs/{lc['id']}/pis",
                                 json={"sale_ids": [absent]})

    assert r_foreign.status_code == r_absent.status_code == 404
    # Identical template: the only difference is the id the caller sent.
    assert (r_foreign.get_json()["error"].replace(str(foreign), "{id}")
            == r_absent.get_json()["error"].replace(str(absent), "{id}")
            == "sale {id} not found")
    assert "different company" not in r_foreign.get_json()["error"]


def test_attach_cross_tenant_raises_the_same_class_as_missing(admin_client, db):
    from raas_tracker import lcs as lcsmod

    cid_a = _company(admin_client, db, "TypedEnumSvcA")
    cid_b = _company(admin_client, db, "TypedEnumSvcB")
    lc = _mk_lc(admin_client, cid_a, "LC-TENUM-SVC-1")
    foreign = _sale(admin_client, cid_b, "PI-TENUM-SVC-1", "TypedEnumSvcProd")
    absent = 987654321

    with pytest.raises(LcSaleNotFound) as foreign_exc:
        lcsmod.attach_pis(db, lc["id"], [foreign])
    with pytest.raises(LcSaleNotFound) as absent_exc:
        lcsmod.attach_pis(db, lc["id"], [absent])

    assert (str(foreign_exc.value).replace(str(foreign), "{id}")
            == str(absent_exc.value).replace(str(absent), "{id}")
            == "sale {id} not found")
    assert status_for(foreign_exc.value) == status_for(absent_exc.value) == 404


def test_attach_leaves_neither_sale_linked(admin_client, db):
    """The 404 is a refusal, not a partial write."""
    from raas_tracker import lcs as lcsmod

    cid_a = _company(admin_client, db, "TypedEnumRollA")
    cid_b = _company(admin_client, db, "TypedEnumRollB")
    lc = _mk_lc(admin_client, cid_a, "LC-TENUM-ROLL-1")
    own = _sale(admin_client, cid_a, "PI-TENUM-ROLL-1", "TypedEnumRollProd")
    foreign = _sale(admin_client, cid_b, "PI-TENUM-ROLL-2", "TypedEnumRollProd2")

    with pytest.raises(LcSaleNotFound):
        lcsmod.attach_pis(db, lc["id"], [own, foreign])
    db.rollback()
    assert db.execute("SELECT lc_id FROM sales WHERE id = %s",
                      (own,)).fetchone()[0] is None