"""NaN/Infinity quantities must be rejected at every numeric entry point.

`nan <= 0` is False and `inf > 0` is True, so plain "> 0" guards wave
NaN/Infinity straight through into SQL (real accepts 'NaN'/'Infinity') and
report math, where they silently poison totals instead of erroring.

Two boundaries are covered here. The `math.isfinite()` guards below are the
service layer. The `_FiniteModel` cases at the bottom are the *input* boundary:
`allow_inf_nan=False` on the Pydantic models, which is the only thing standing
between a non-finite float and a `REAL` column. The HTTP parser half of Phase
2-A lives in `test_numeric_integrity.py`.
"""
import math

import pytest
from pydantic import ValidationError

import flask_app
from raas_tracker.recipes import create_production_run


def _produce(admin_client, payload):
    return admin_client.post("/api/recipes/Any/produce", json=payload)


# Phase 2-A moved the *first* line of defence on these routes one step earlier:
# `_FiniteJSONProvider` rejects the bare `NaN`/`Infinity` literals while the body
# is still being decoded, so a request carrying one can no longer reach schema
# validation or `_req_float` at all. The outcome — 400, nothing written — is
# unchanged and strictly stronger; only the error *shape* differs, so these
# assert the status and the presence of an `error` key. The layers underneath
# are pinned separately by the direct-model and direct-helper tests below.


def _assert_rejected(r):
    assert r.status_code == 400, r.get_data(as_text=True)
    assert r.get_json().get("error")


def test_produce_rejects_nan_recipe_qty(admin_client):
    r = _produce(admin_client, {
        "production_qty": 5, "company_id": 1,
        "recipes": [{"recipe_name": "Any", "qty": float("nan")}],
    })
    _assert_rejected(r)


def test_produce_rejects_infinite_recipe_qty(admin_client):
    r = _produce(admin_client, {
        "production_qty": 5, "company_id": 1,
        "recipes": [{"recipe_name": "Any", "qty": float("inf")}],
    })
    _assert_rejected(r)


def test_produce_rejects_nan_production_qty(admin_client):
    r = _produce(admin_client, {
        "production_qty": float("nan"), "company_id": 1,
    })
    _assert_rejected(r)


def test_produce_rejects_infinite_production_qty(admin_client):
    r = _produce(admin_client, {
        "production_qty": float("inf"), "company_id": 1,
    })
    _assert_rejected(r)


def test_generate_rejects_nan_qty(admin_client):
    r = admin_client.post("/api/reports/generate", json={
        "recipes": [{"company_id": 1, "recipe_name": "x"}],
        "qty": float("nan"),
    })
    _assert_rejected(r)


def test_generate_rejects_infinite_qty(admin_client):
    r = admin_client.post("/api/reports/generate", json={
        "recipes": [{"company_id": 1, "recipe_name": "x"}],
        "qty": float("inf"),
    })
    _assert_rejected(r)


def test_export_rejects_nan_qty(admin_client):
    r = admin_client.post("/api/reports/export", json={
        "recipes": [{"company_id": 1, "recipe_name": "x"}],
        "qty": float("nan"),
    })
    _assert_rejected(r)


# ---- the layers the parser now sits in front of ----
#
# `ProductionRecipeIn.qty` / `ProductionRunCreateIn.production_qty` carry a
# per-field `allow_inf_nan=False` and `_req_float` carries its own
# `math.isfinite` check. Both predate the provider and both must keep working on
# their own: they are the guards for any caller that does not arrive as an HTTP
# JSON body.


@pytest.mark.parametrize("model_name,payload", [
    ("ProductionRecipeIn", {"qty": 0.0}),
    ("ProductionRunCreateIn", {"production_qty": 0.0}),
])
@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")],
                         ids=["inf", "-inf", "nan"])
def test_production_models_reject_non_finite_at_the_schema(model_name, payload, bad):
    model = getattr(flask_app, model_name)
    body = dict(payload)
    body[next(f for f in body)] = bad
    with pytest.raises(ValidationError):
        model.model_validate(body)


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan"),
                                 "inf", "-Infinity", "nan", "NaN"])
def test_req_float_rejects_non_finite(bad):
    with pytest.raises(ValueError, match="finite"):
        flask_app._req_float({"v": bad}, "v")


# ---- raw-dict numeric ingress: the routes with no model behind them ----
#
# `PUT /api/chemicals/reorder` and `PUT /api/recipes/<name>` have no Pydantic
# model, so `allow_inf_nan=False` never runs on them and the route's own guard is
# the only wall between a non-finite value and a `REAL` column. Neither
# `_FiniteJSONProvider` nor `float()` saves them either: a *quoted* `"Infinity"`
# is an ordinary string to the parser (not a `parse_constant` literal) and
# `float("Infinity")` succeeds, and `1e309` is a perfectly valid JSON number that
# overflows to `inf`. `float(data.get(...) or 0)` is no guard at all —
# `inf < 0` is False and `nan < 0` is False.
#
# `0 or 0` also silently swallowed `null`/`""`; `_req_float` reports them.

_NON_FINITE_RAW = ["Infinity", "-Infinity", "NaN", "1e309"]


def _raw_level(admin_client, db, bad, field="reorder_level", name="Rdq"):
    admin_client.post("/api/chemicals", json={"name": name, "qty": 10, "unit": "KG"})
    before = db.execute("SELECT reorder_level FROM chemicals WHERE name = %s",
                        (name,)).fetchone()[0]
    r = admin_client.put("/api/chemicals/reorder",
                         json={"name": name, field: bad})
    after = db.execute("SELECT reorder_level FROM chemicals WHERE name = %s",
                       (name,)).fetchone()[0]
    return r, before, after


@pytest.mark.parametrize("bad", _NON_FINITE_RAW, ids=_NON_FINITE_RAW)
def test_reorder_route_rejects_non_finite_and_writes_nothing(admin_client, db, bad):
    """MEDIUM-1: `"Infinity"` used to commit `inf` into a REAL NOT NULL column.

    Permanent and unrecoverable — and every later `GET /api/chemicals` then
    serialises invalid JSON for every client, not just this one.
    """
    r, before, after = _raw_level(admin_client, db, bad)
    assert r.status_code == 400, r.get_data(as_text=True)
    assert r.is_json and r.get_json().get("error")
    assert after == before


def test_reorder_route_still_accepts_a_finite_value(admin_client, db):
    """The guard must not over-reject: the existing round trip must still work."""
    r, _before, after = _raw_level(admin_client, db, 25)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert after == 25


def test_reorder_route_rejects_bare_1e309_number_token(admin_client, db):
    """`1e309` is a *valid* JSON number, so `parse_constant` never fires.

    Only the route guard can refuse it — which is the concrete reason the raw
    ingress cannot lean on the provider.
    """
    admin_client.post("/api/chemicals",
                      json={"name": "Rdq", "qty": 10, "unit": "KG"})
    r = admin_client.put("/api/chemicals/reorder", content_type="application/json",
                         data='{"name": "Rdq", "reorder_level": 1e309}')
    assert r.status_code == 400, r.get_data(as_text=True)
    assert db.execute("SELECT reorder_level FROM chemicals WHERE name = %s",
                      ("Rdq",)).fetchone()[0] == 0


# ---- the same hole on PUT /api/recipes/<name> ----
#
# Found while sweeping `flask_app.py` for the reorder defect: `total_quantity`
# and `water_percentage` are read straight out of the raw dict and handed to
# `update_recipe` with no coercion at all. `total_quantity` had no guard of any
# kind; `water_percentage` had `0 <= v <= 100`, which `inf` trips by accident
# (`inf > 100`) but `nan` walks straight through — both comparisons are False.

def _seed_recipe(admin_client, db, name="NFRec"):
    admin_client.post("/api/companies", json={"name": "NFCo"})
    cid = db.execute("SELECT id FROM companies WHERE name = %s",
                     ("NFCo",)).fetchone()[0]
    # A master recipe can only be created for a product already registered to
    # the company, and a sale is what registers one.
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": "PI-NF", "client_name": "x", "company_id": cid},
        "items": [{"product_name": "C1", "quantity": 10, "unit_price": 5}]})
    assert r.status_code == 201, r.get_json()
    r = admin_client.post("/api/recipes", json={
        "name": name, "yield": 5, "company_id": cid, "product_name": "C1"})
    assert r.status_code == 201, r.get_json()
    return cid


@pytest.mark.parametrize("field", ["total_quantity", "water_percentage"])
@pytest.mark.parametrize("bad", _NON_FINITE_RAW, ids=_NON_FINITE_RAW)
def test_recipe_update_route_rejects_non_finite_and_writes_nothing(
        admin_client, db, field, bad):
    cid = _seed_recipe(admin_client, db)
    col = "water_percentage" if field == "water_percentage" else "total_quantity"
    before = db.execute(f"SELECT {col} FROM recipes WHERE name = %s",
                        ("NFRec",)).fetchone()[0]
    r = admin_client.put("/api/recipes/NFRec", json={"company_id": cid, field: bad})
    assert r.status_code == 400, r.get_data(as_text=True)
    assert r.is_json and r.get_json().get("error")
    assert db.execute(f"SELECT {col} FROM recipes WHERE name = %s",
                      ("NFRec",)).fetchone()[0] == before


def test_recipe_update_route_still_accepts_a_finite_value(admin_client, db):
    cid = _seed_recipe(admin_client, db)
    r = admin_client.put("/api/recipes/NFRec", json={
        "company_id": cid, "total_quantity": 120, "water_percentage": 10})
    assert r.status_code == 200, r.get_data(as_text=True)
    row = db.execute("SELECT total_quantity, water_percentage FROM recipes "
                     "WHERE name = %s", ("NFRec",)).fetchone()
    assert row == (120, 10)


def test_recipe_update_route_omitted_field_keeps_the_current_value(admin_client, db):
    """`None` means "leave alone" on this route — the frontend relies on it."""
    cid = _seed_recipe(admin_client, db)
    r = admin_client.put("/api/recipes/NFRec", json={
        "company_id": cid, "total_quantity": 90})
    assert r.status_code == 200, r.get_data(as_text=True)
    r = admin_client.put("/api/recipes/NFRec", json={"company_id": cid})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert db.execute("SELECT total_quantity FROM recipes WHERE name = %s",
                      ("NFRec",)).fetchone()[0] == 90


@pytest.mark.parametrize("method,path", [
    ("post", "/api/recipes/NFRec/items"),
    ("put", "/api/recipes/NFRec"),
    ("put", "/api/recipes/NFRec/items/C1"),
    ("post", "/api/recipes/NFRec/produce"),
])
def test_raw_dict_int_coercion_rejects_an_overflowing_company_id(
        admin_client, db, method, path):
    """`int(float('inf'))` raises `OverflowError`, not `TypeError`/`ValueError`.

    A non-finite `company_id` therefore skipped the 400 and hit the catch-all
    `Exception` handler as a 500 — the same unhandled-body-defect shape the 400
    handler exists to prevent. All four recipe routes read `company_id` out of
    the raw dict and coerce it the same way.

    `1e309` is the payload because it is the form that actually gets through: a
    bare `Infinity` is refused by the parser, but `1e309` is valid JSON that
    only overflows when the number is parsed.
    """
    _seed_recipe(admin_client, db)
    r = getattr(admin_client, method)(
        path, content_type="application/json",
        data='{"company_id": 1e309}')
    assert r.status_code == 400, r.get_data(as_text=True)
    assert r.is_json and r.get_json().get("error")


def test_create_production_run_service_guard_rejects_nan(db):
    with pytest.raises(ValueError, match="must be positive"):
        create_production_run(db, 1, "nope", float("nan"), atomic=True)


def test_create_production_run_service_guard_rejects_inf(db):
    with pytest.raises(ValueError, match="must be positive"):
        create_production_run(db, 1, "nope", float("inf"), atomic=True)


# ==================== Phase 2-A: model-level finite guard ====================
#
# One case per (model, numeric field) on the money/quantity write surface. The
# payload under each case is fully valid — its only defect is the value of the
# named field — so a failure here can only be the missing `allow_inf_nan=False`.
#
# `+inf` is the one that slips past a comparison guard (`inf > 0` is True,
# `inf >= 0` is True); `nan`/`-inf` are also asserted so that a future fix which
# special-cases only `inf` cannot pass.

_NON_FINITE_CASES = [
    ("ChemicalCreateIn", "qty",
     {"name": "C1", "qty": 0.0, "reorder_level": 0.0}),
    ("ChemicalCreateIn", "reorder_level",
     {"name": "C1", "qty": 0.0, "reorder_level": 0.0}),
    ("RecipeCreateIn", "yield_qty",
     {"name": "R1", "company_id": 1, "product_name": "P1", "yield_qty": 1.0}),
    ("InvoiceBatchLineIn", "quantity",
     {"sale_item_id": 1, "quantity": 1.0}),
    ("InvoicePayIn", "payment_amount",
     {"payment_amount": 1.0, "payment_date": "2026-01-01"}),
    ("SaleFullItemIn", "quantity",
     {"product_name": "P1", "quantity": 1.0, "unit_price": 1.0}),
    ("SaleFullItemIn", "unit_price",
     {"product_name": "P1", "quantity": 1.0, "unit_price": 1.0}),
    ("PaymentIn", "payment_amount",
     {"payment_amount": 1.0, "payment_date": "2026-01-01"}),
    ("PaymentPatchIn", "payment_amount",
     {"payment_amount": 1.0, "payment_date": "2026-01-01"}),
    ("SaleItemPatchIn", "quantity",
     {"quantity": 1.0}),
    ("SaleItemPatchIn", "unit_price",
     {"unit_price": 1.0}),
]

_NON_FINITE_IDS = [f"{m}.{f}" for m, f, _ in _NON_FINITE_CASES]


@pytest.mark.parametrize("model_name,field,payload", _NON_FINITE_CASES,
                         ids=_NON_FINITE_IDS)
@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")],
                         ids=["inf", "-inf", "nan"])
def test_money_model_rejects_non_finite(model_name, field, payload, bad):
    model = getattr(flask_app, model_name)
    body = dict(payload)
    body[field] = bad
    with pytest.raises(ValidationError):
        model.model_validate(body)


@pytest.mark.parametrize("model_name,field,payload", _NON_FINITE_CASES,
                         ids=_NON_FINITE_IDS)
def test_money_model_still_accepts_finite(model_name, field, payload):
    """The guard must not over-reject. `1e308` is finite and stays valid.

    The real `REAL` columns are float4 (max ~3.4e38), so a value this large is
    nonsense business data rather than a valid write — but rejecting it is a
    column-range concern (Phase 3), not a finiteness one. Asserted at the model
    boundary so tightening `allow_inf_nan` can never quietly become a magnitude
    cap.
    """
    model = getattr(flask_app, model_name)
    body = dict(payload)
    body[field] = 1e308
    validated = model.model_validate(body)
    assert math.isfinite(validated.model_dump()[field])
    assert validated.model_dump()[field] == 1e308
