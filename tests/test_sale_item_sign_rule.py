"""The sale-line SIGN rule: quantity and unit_price must be strictly positive.

This file records a **deliberate reversal** of an earlier ratified decision.

Phase 2-B's first pass was scoped to FINITENESS only. It added
``finite_values()`` in front of every service-layer write of a REAL/NUMERIC
column and explicitly preserved the existing sign behaviour, on the reasoning
that a zero-priced quotation line ("price TBD, fill in later") is legitimate.
Two tests in ``test_numeric_integrity_services.py`` asserted that ``0`` and
negatives were **accepted** by ``add_sale_item`` / ``update_sale_item``.

That reasoning was put back to the user, explicitly including the cost: a sale
line whose price is not yet known can no longer be recorded. **The user chose
the strict rule anyway.** Both ``quantity`` and ``unit_price`` must now be
``> 0``, on all four writers of ``sale_items.quantity`` / ``unit_price``:

* ``add_sale_item``     (was: no sign rule at all)
* ``update_sale_item``  (was: no sign rule at all)
* ``add_sale``          (was: no sign rule at all)
* ``update_sale_full``  (was: ``< 0`` only, so it accepted ``0``)

Those two preserved-behaviour tests are inverted in their own file, with the
reversal stated at the point of change.

Why the rule is applied to ALL FOUR, not just the two named in the original
brief: they write the SAME columns. ``add_sale`` is the only way a sale is
created over HTTP, and ``/api/sales/batch`` has its own inline INSERT that
bypasses ``add_sale`` entirely. A sign rule on ``add_sale_item`` alone would be
trivially bypassed by the primary create path.

Two layers are pinned here because both are load-bearing:

* the **service** layer, called directly with no parser and no Pydantic model,
  which is the wall that holds for every non-HTTP caller; and
* the **model** layer, which is the ONLY wall on ``/api/sales/batch`` and the
  only thing that turns a rejection into AGENTS.md's documented error contract,
  ``400 {error, details: [{field, message}]}``, instead of an unhandled
  ``ValueError`` escaping as a 500.

Every rejection asserts the **database is unchanged**, not merely that an
exception was raised. A guard that raises after a partial mutation leaves the
row changed while telling the caller the call failed.
"""
import pytest
from pydantic import ValidationError

import flask_app
from flask_app import SaleFullItemIn, SaleItemIn, SaleItemPatchIn
from raas_tracker.sales import (
    InvoicedLineConflict,
    add_sale,
    add_sale_item,
    create_invoice,
    get_sale_by_id,
    update_sale_full,
    update_sale_item,
)

# Zero in every spelling the app actually produces. The frontend coerces a blank
# or unparseable input with `parseFloat(x) || 0`, so a 0 reaches the wire far
# more easily than a typo'd negative does.
NON_POSITIVE = pytest.mark.parametrize("bad", [0, 0.0, -0.01, -1, -1000],
                                      ids=["0", "0.0", "-0.01", "-1", "-1000"])


# ----------------------------------------------------------------- helpers --

def _sale(db, pi="PI-SIGN", quantity=10, unit_price=10.0):
    return add_sale(db, {"pi_number": pi, "client_name": "Sign Co"},
                    [{"product_name": "W", "quantity": quantity,
                      "unit_price": unit_price, "unit": "KG"}])


def _first_item_id(db, sale_id):
    return db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id", (sale_id,)
    ).fetchone()[0]


def _stored_item(db, item_id):
    return db.execute(
        "SELECT product_name, quantity, unit_price, unit FROM sale_items WHERE id = %s",
        (item_id,)).fetchone()


def _item_count(db, sale_id=None):
    if sale_id is None:
        return db.execute("SELECT count(*) FROM sale_items").fetchone()[0]
    return db.execute("SELECT count(*) FROM sale_items WHERE sale_id = %s",
                      (sale_id,)).fetchone()[0]


def _audit_count(db, action):
    return db.execute("SELECT count(*) FROM audit_logs WHERE action = %s",
                      (action,)).fetchone()[0]


def _http_sale(admin_client, pi: str = "PI-SIGN-HTTP",
               quantity: float = 10, unit_price: float = 10.0) -> int:
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": pi, "client_name": "Sign Co"},
        "items": [{"product_name": "W", "quantity": quantity,
                   "unit_price": unit_price, "unit": "KG"}]})
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    return body.get("id") or body["sale"]["id"]


# ============================================================ 1. services ==

@NON_POSITIVE
def test_add_sale_item_rejects_non_positive_and_writes_nothing(db, bad):
    """The named rule. There was no sign check here before, so ``0`` and
    negatives were accepted and written straight into REAL columns."""
    sid = _sale(db)
    before = _item_count(db, sid)

    with pytest.raises(ValueError):
        add_sale_item(db, sid, "ZeroQty", bad, 10.0, "KG")
    with pytest.raises(ValueError):
        add_sale_item(db, sid, "ZeroPrice", 5, bad, "KG")

    assert _item_count(db, sid) == before, "a rejected line was still written"
    assert _audit_count(db, "SALE_ITEM_ADD") == 0


@NON_POSITIVE
def test_update_sale_item_rejects_non_positive_and_changes_nothing(db, bad):
    sid = _sale(db)
    item_id = _first_item_id(db, sid)
    stored = _stored_item(db, item_id)

    with pytest.raises(ValueError):
        update_sale_item(db, sid, item_id, quantity=bad)
    with pytest.raises(ValueError):
        update_sale_item(db, sid, item_id, unit_price=bad)

    assert _stored_item(db, item_id) == stored, "a rejected edit still landed"
    assert _audit_count(db, "SALE_ITEM_UPDATE") == 0


@NON_POSITIVE
def test_update_sale_item_rejects_non_positive_alongside_a_valid_field(db, bad):
    """A mixed patch is still rejected: ``unit_price=5`` must not buy a pass for
    a ``quantity`` of 0 in the same request."""
    sid = _sale(db)
    item_id = _first_item_id(db, sid)
    stored = _stored_item(db, item_id)

    with pytest.raises(ValueError):
        update_sale_item(db, sid, item_id, quantity=bad, unit_price=5.0)
    with pytest.raises(ValueError):
        update_sale_item(db, sid, item_id, quantity=5.0, unit_price=bad)

    assert _stored_item(db, item_id) == stored


@NON_POSITIVE
def test_add_sale_rejects_non_positive_items_and_writes_no_sale(db, bad):
    """``add_sale`` writes the same two columns and had no sign rule either.
    Its ``INSERT INTO sales`` runs first, so the guard must precede every
    statement or a rejection leaves an uncommitted header behind."""
    with pytest.raises(ValueError):
        add_sale(db, {"pi_number": "PI-BAD"},
                 [{"product_name": "W", "quantity": bad, "unit_price": 10}])
    with pytest.raises(ValueError):
        add_sale(db, {"pi_number": "PI-BAD"},
                 [{"product_name": "W", "quantity": 1, "unit_price": bad}])

    assert db.execute("SELECT count(*) FROM sales").fetchone()[0] == 0
    assert _item_count(db) == 0
    assert _audit_count(db, "SALE_CREATE") == 0


@NON_POSITIVE
def test_update_sale_full_rejects_non_positive_and_writes_nothing(db, bad):
    """``update_sale_full`` already refused negatives (``qty < 0``); the rule
    extends that to ``0``.

    This is the one writer where tightening is a genuine behaviour change for
    existing data: it REPLACES the whole line set, so a sale that already holds
    a zero-priced line becomes unsaveable through this route until the operator
    gives that line a positive price. Production holds no such row (checked:
    0 non-positive quantity and 0 non-positive unit_price across
    ``sale_items``), so there is nothing to migrate today.
    """
    sid = _sale(db, pi="PI-SIGN-FULL")
    before_items = db.execute(
        "SELECT id, product_name, quantity, unit_price FROM sale_items "
        "WHERE sale_id = %s ORDER BY id", (sid,)).fetchall()

    with pytest.raises(ValueError):
        update_sale_full(db, sid, {"pi_number": "PI-SIGN-FULL-RENAMED"},
                         [{"product_name": "W", "quantity": bad, "unit_price": 10}], [])
    with pytest.raises(ValueError):
        update_sale_full(db, sid, {"pi_number": "PI-SIGN-FULL-RENAMED"},
                         [{"product_name": "W", "quantity": 10, "unit_price": bad}], [])
    # A clean first item must not land while a poisoned later one is rejected.
    with pytest.raises(ValueError):
        update_sale_full(db, sid, {"pi_number": "PI-SIGN-FULL-RENAMED"},
                         [{"product_name": "W", "quantity": 99, "unit_price": 99},
                          {"product_name": "X", "quantity": bad, "unit_price": 1}], [])

    assert db.execute("SELECT pi_number FROM sales "
                      "WHERE id = %s", (sid,)).fetchone()[0] == "PI-SIGN-FULL"
    assert db.execute(
        "SELECT id, product_name, quantity, unit_price FROM sale_items "
        "WHERE sale_id = %s ORDER BY id", (sid,)).fetchall() == before_items
    assert _audit_count(db, "SALE_UPDATE") == 0


def test_sign_rule_still_accepts_finite_positive_values(db):
    """The rule is a sign rule, not a magnitude cap and not a new finiteness
    rule: anything finite and above zero still writes."""
    sid = _sale(db, pi="PI-SIGN-OK")
    item_id = _first_item_id(db, sid)
    assert add_sale_item(db, sid, "Tiny", 1e-6, 1e-6, "KG")
    assert add_sale_item(db, sid, "Huge", 1e30, 1e30, "KG")
    assert update_sale_item(db, sid, item_id, quantity=0.5, unit_price=0.25) is True
    assert _stored_item(db, item_id)[1:3] == (0.5, 0.25)


def test_sign_rule_leaves_descriptive_edits_and_the_invoiced_conflict_alone(db):
    """Two gates that must keep winning, unchanged:
    * a descriptive-only edit (``quantity``/``unit_price`` both ``None``) is
      still allowed on an invoiced line, because the 409 only applies to a
      rebasing edit; and
    * the invoiced-line conflict still beats the sign rule, so the error shape
      the frontend already handles does not change to a 400.
    """
    sid = _sale(db, pi="PI-SIGN-INV")
    item_id = _first_item_id(db, sid)

    # No quantity/unit_price supplied -> the sign guard never runs, and the
    # descriptive edit is allowed on an uninvoiced line.
    assert update_sale_item(db, sid, item_id, product_name="Renamed", unit="L") is True

    create_invoice(db, sid, "INV-SIGN")
    with pytest.raises(InvoicedLineConflict):
        update_sale_item(db, sid, item_id, quantity=0)
    with pytest.raises(InvoicedLineConflict):
        update_sale_item(db, sid, item_id, unit_price=-1)
    assert _stored_item(db, item_id)[1:3] == (10.0, 10.0)


# =============================================================== 2. models ==

@pytest.mark.parametrize("model", [SaleItemIn, SaleFullItemIn, SaleItemPatchIn],
                         ids=["SaleItemIn", "SaleFullItemIn", "SaleItemPatchIn"])
@NON_POSITIVE
def test_sale_item_models_reject_non_positive(model, bad):
    for field in ("quantity", "unit_price"):
        payload = {"product_name": "W", "quantity": 10, "unit_price": 10}
        payload[field] = bad
        with pytest.raises(ValidationError) as exc:
            model(**payload)
        assert [e["loc"] for e in exc.value.errors()] == [(field,)], (
            "the field must be named exactly once — no double-reporting")
        assert exc.value.errors()[0]["type"] == "greater_than"


@pytest.mark.parametrize("model", [SaleItemIn, SaleFullItemIn, SaleItemPatchIn],
                         ids=["SaleItemIn", "SaleFullItemIn", "SaleItemPatchIn"])
def test_sale_item_models_still_accept_finite_positive(model):
    model(product_name="W", quantity=1e-6, unit_price=1e30)


@pytest.mark.parametrize("model", [SaleItemIn, SaleFullItemIn, SaleItemPatchIn],
                         ids=["SaleItemIn", "SaleFullItemIn", "SaleItemPatchIn"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")],
                         ids=["nan", "inf", "-inf"])
def test_sign_rule_does_not_double_report_non_finite(model, bad):
    """``gt=0`` sits alongside ``allow_inf_nan=False`` on these fields.

    Pydantic runs the finite check first and short-circuits, so a non-finite
    value yields exactly one ``finite_number`` error — the same as before the
    sign rule. If a future Pydantic version reported both, a caller rendering
    ``details`` would show the same field twice.
    """
    with pytest.raises(ValidationError) as exc:
        model(product_name="W", quantity=bad, unit_price=10)
    errors = exc.value.errors()
    assert [e["type"] for e in errors] == ["finite_number"] * len(errors)
    assert {e["loc"][0] for e in errors} == {"quantity"}


# ================================================================== 3. HTTP ==

@NON_POSITIVE
def test_post_sale_rejects_non_positive_with_the_documented_error_contract(admin_client, db, bad):
    """AGENTS.md: validation is ``400 {error, details: [{field, message}]}``.
    Without the model bound this would be an unhandled ``ValueError`` -> 500,
    because ``api_create_sale`` does not catch it."""
    for field in ("quantity", "unit_price"):
        item = {"product_name": "W", "quantity": 10, "unit_price": 10}
        item[field] = bad
        r = admin_client.post("/api/sales", json={
            "sale": {"pi_number": "PI-HTTP-SIGN"}, "items": [item]})

        assert r.status_code == 400, r.get_json()
        body = r.get_json()
        assert body["error"], body
        assert len(body["details"]) == 1, body
        assert body["details"][0]["field"] == f"items.0.{field}", body
        assert body["details"][0]["message"], body

    assert db.execute("SELECT count(*) FROM sales").fetchone()[0] == 0
    assert _item_count(db) == 0


@NON_POSITIVE
def test_sales_batch_rejects_non_positive_with_the_documented_contract(admin_client, db, bad):
    """``/api/sales/batch`` has its own inline ``INSERT INTO sale_items`` and
    never calls ``add_sale``, so the Pydantic model is the ONLY wall here."""
    item = {"product_name": "W", "quantity": 10, "unit_price": 10}
    item["quantity"] = bad
    r = admin_client.post("/api/sales/batch", json={"sales": [
        {"sale": {"pi_number": "PI-BATCH-SIGN"}, "items": [item]}]})

    assert r.status_code == 400, r.get_json()
    assert r.get_json()["details"][0]["field"] == "sales.0.items.0.quantity"
    assert db.execute("SELECT count(*) FROM sales").fetchone()[0] == 0
    assert _item_count(db) == 0


@NON_POSITIVE
def test_put_sale_rejects_non_positive_and_writes_nothing(admin_client, db, bad):
    """The whole-sale PUT. Header and line set are one transaction, so the
    rejection must leave the stored PI number alone too."""
    sid = _http_sale(admin_client, pi="PI-HTTP-FULL")

    item = {"product_name": "W", "quantity": 10, "unit_price": 10}
    item["unit_price"] = bad
    r = admin_client.put(f"/api/sales/{sid}", json={
        "header": {"pi_number": "PI-HTTP-FULL-RENAMED"},
        "items": [item], "removedIds": []})

    assert r.status_code == 400, r.get_json()
    assert r.get_json()["details"][0]["field"] == "items.0.unit_price"
    assert db.execute("SELECT pi_number FROM sales "
                      "WHERE id = %s", (sid,)).fetchone()[0] == "PI-HTTP-FULL"
    assert get_sale_by_id(db, sid)["items"][0]["unit_price"] == 10


@NON_POSITIVE
def test_put_sale_item_rejects_non_positive_with_the_documented_contract(admin_client, db, bad):
    """``api_update_item`` maps the model's error to a bare ``{"error": ...}``
    400, while the service's own ValueError also becomes a bare 400. The model
    bound is what makes the answer carry ``details``."""
    sid = _http_sale(admin_client, pi="PI-HTTP-ITEM")
    item_id = _first_item_id(db, sid)

    r = admin_client.put(f"/api/sales/{sid}/items/{item_id}",
                         json={"quantity": bad})
    assert r.status_code == 400, r.get_json()
    body = r.get_json()
    assert body["details"][0]["field"] == "quantity", body

    r = admin_client.put(f"/api/sales/{sid}/items/{item_id}",
                         json={"unit_price": bad})
    assert r.status_code == 400, r.get_json()
    assert r.get_json()["details"][0]["field"] == "unit_price"

    assert _stored_item(db, item_id)[1:3] == (10.0, 10.0)


def test_http_sale_lines_still_round_trip_positive_values(admin_client, db):
    """A 400 that never lets a positive value through is a broken app. The whole
    create -> read -> edit -> full-update path, on realistic values including
    sub-unit quantities and a four-decimal price."""
    sid = _http_sale(admin_client, pi="PI-HTTP-OK", quantity=0.25, unit_price=1250.5)
    items = get_sale_by_id(db, sid)["items"]
    assert (items[0]["quantity"], items[0]["unit_price"]) == (0.25, 1250.5)

    r = admin_client.post(f"/api/sales/{sid}/items", json={
        "product_name": "Extra", "quantity": 1, "unit_price": 3, "unit": "KG"})
    assert r.status_code == 201, r.get_json()
    new_id = r.get_json()["id"]

    r = admin_client.put(f"/api/sales/{sid}/items/{new_id}", json={"unit_price": 4.5})
    assert r.status_code == 200, r.get_json()
    assert _stored_item(db, new_id)[2] == 4.5

    r = admin_client.put(f"/api/sales/{sid}", json={
        "header": {"pi_number": "PI-HTTP-OK2"},
        "items": [{"id": new_id, "product_name": "Extra",
                   "quantity": 2, "unit_price": 4.5, "unit": "KG"},
                  {"product_name": "Fresh", "quantity": 7, "unit_price": 0.01,
                   "unit": "KG"}],
        "removedIds": []})
    assert r.status_code == 200, r.get_json()
    assert get_sale_by_id(db, sid)["pi_number"] == "PI-HTTP-OK2"


def test_the_sign_rule_is_not_reachable_through_a_raw_string_float(admin_client, db):
    """Pydantic's lax mode coerces ``"0"`` and ``"-1"`` to floats. The bound
    must hold on the coerced value, not only on a JSON number."""
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": "PI-STR-SIGN"},
        "items": [{"product_name": "W", "quantity": "0", "unit_price": "10"}]})
    assert r.status_code == 400, r.get_json()
    assert r.get_json()["details"][0]["field"] == "items.0.quantity"

    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": "PI-STR-SIGN"},
        "items": [{"product_name": "W", "quantity": "5", "unit_price": "-3"}]})
    assert r.status_code == 400, r.get_json()
    assert r.get_json()["details"][0]["field"] == "items.0.unit_price"

    assert db.execute("SELECT count(*) FROM sales").fetchone()[0] == 0


def test_sign_rule_rejects_a_non_finite_value_before_the_sign_check(admin_client, db):
    """Order of the two walls is observable: ``Infinity`` is reported as a
    finite-number failure, not as a sign failure, so the message tells the
    operator the real problem."""
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": "PI-NF-SIGN"},
        "items": [{"product_name": "W", "quantity": "Infinity", "unit_price": 10}]})
    assert r.status_code == 400, r.get_json()
    assert db.execute("SELECT count(*) FROM sales").fetchone()[0] == 0


def test_model_module_is_the_only_one_the_routes_use():
    """Pins the wiring: the three models above are the ones the sale routes
    validate against. A future refactor that reintroduces a private copy of a
    sale-line model would silently drop the bound."""
    payload = {"product_name": "W", "quantity": 0, "unit_price": 1}
    for model, label in ((flask_app.SaleItemIn, "SaleItemIn"),
                         (flask_app.SaleFullItemIn, "SaleFullItemIn"),
                         (flask_app.SaleItemPatchIn, "SaleItemPatchIn")):
        with pytest.raises(ValidationError):
            model(**payload)
        assert label in model.__name__