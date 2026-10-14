"""Phase 2-A: nothing non-finite may enter the process at the input boundary.

Two independent walls, both proved here:

1. **The parser.** ``app.json`` is the app-wide choke point for every
   JSON body. Python's ``json`` module accepts the bare literals ``NaN``,
   ``Infinity`` and ``-Infinity`` by default (they are not valid JSON, but the
   stdlib is lenient), so ``{"payment_amount": Infinity}`` used to reach the
   service layer as ``float('inf')``. The provider now rejects them and the
   request fails as a clean 400 before a route body ever runs.

2. **The shape of the failure.** A body that cannot be parsed is a client
   error. It must not surface through the catch-all ``Exception`` handler as a
   500 — a 500 says "retry later", which for a permanently malformed body sends
   the caller into a pointless retry loop and buries the real fault in logs.

The happy-path tests at the bottom are as important as the rejections: the
provider is app-wide, so a false positive here breaks every route in the app.
"""
import math

import pytest

import flask_app
from raas_tracker.sales import get_sale_total_paid


def _put_json(client, url, raw_body):
    """PUT a raw JSON string — ``json=`` cannot express a bare ``Infinity``."""
    return client.put(url, data=raw_body, content_type="application/json")


def _post_json(client, url, raw_body):
    """POST a raw JSON string — ``json=`` cannot express a bare ``Infinity``."""
    return client.post(url, data=raw_body, content_type="application/json")


def _make_sale(admin_client, tag="PI-NF"):
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": tag, "client_name": "NonFinite"},
        "items": [{"product_name": "P1", "quantity": 10, "unit_price": 100}]})
    assert r.status_code in (200, 201), r.get_json()
    return r.get_json().get("id") or r.get_json()["sale"]["id"]


# ============================ provider unit ============================

def test_app_json_provider_is_the_finite_one():
    assert isinstance(flask_app.app.json, flask_app._FiniteJSONProvider)


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_provider_rejects_bare_constants(literal):
    with pytest.raises(ValueError):
        flask_app.app.json.loads('{"v": %s}' % literal)


def test_provider_accepts_finite_numbers_and_quoted_constants():
    provider = flask_app.app.json
    # 1e308 is the largest magnitude most inputs will ever carry; it is finite
    # and must survive. 1e309 would parse to +inf via `float()` overflow but is
    # NOT a JSON constant, so `parse_constant` correctly does not fire on it —
    # magnitude overflow is a separate concern from the bare literals.
    assert provider.loads('{"v": 1e308}')["v"] == 1e308
    assert provider.loads('{"v": -1e308}')["v"] == -1e308
    assert provider.loads('{"v": 0}')["v"] == 0
    assert provider.loads('{"v": 0.01}')["v"] == 0.01
    # A *quoted* "Infinity" is an ordinary string and must stay one: it then
    # fails normal float coercion, which is already a 400.
    assert provider.loads('{"v": "Infinity"}')["v"] == "Infinity"


# ======================= HTTP: bare literals rejected =======================

@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_bare_constant_in_body_is_400_not_500(admin_client, literal):
    """The forgery cascade must never start: 400, before any route body runs."""
    sid = _make_sale(admin_client, f"PI-{literal}")
    r = _put_json(admin_client, f"/api/sales/{sid}/payment",
                  '{"payment_amount": %s, "payment_date": "2026-01-01"}' % literal)
    assert r.status_code == 400, r.get_data(as_text=True)
    assert r.is_json
    assert r.get_json().get("error")


def test_rejected_payment_writes_nothing(admin_client, db):
    """400 means *rejected*, not *partially applied*.

    Asserted rather than inferred: the sale must still carry no payment row and
    must not have advanced to `completed`.
    """
    sid = _make_sale(admin_client, "PI-POISON")
    before_stage = db.execute("SELECT stage FROM sales WHERE id = %s",
                              (sid,)).fetchone()[0]

    r = _put_json(admin_client, f"/api/sales/{sid}/payment",
                  '{"payment_amount": Infinity, "payment_date": "2026-01-01"}')
    assert r.status_code == 400, r.get_data(as_text=True)

    assert db.execute("SELECT count(*) FROM sale_payments WHERE sale_id = %s",
                      (sid,)).fetchone()[0] == 0
    assert db.execute("SELECT stage FROM sales WHERE id = %s",
                      (sid,)).fetchone()[0] == before_stage
    assert get_sale_total_paid(db, sid) == 0


def test_app_wide_provider_blocks_a_non_financial_route(admin_client, db):
    """Proof the choke point is app-wide, not a per-route patch.

    `/api/chemicals` carries no money, yet an `Infinity` body is rejected the
    same way and nothing lands in `chemicals`.
    """
    r = _post_json(admin_client, "/api/chemicals",
                   '{"name": "PoisonChem", "qty": Infinity, "unit": "KG"}')
    assert r.status_code == 400, r.get_data(as_text=True)
    assert db.execute("SELECT 1 FROM chemicals WHERE name = %s",
                      ("PoisonChem",)).fetchone() is None


def test_parser_rejects_a_constant_no_model_will_ever_look_at(admin_client, db):
    """Isolate the parser from the model guard.

    The other HTTP cases pass with *either* wall standing, because
    `allow_inf_nan=False` catches the value afterwards. This one puts the
    non-finite in an unknown key, which Pydantic ignores — so nothing but the
    parser can reject it, and reverting the provider alone makes this fail.
    That is the whole justification for keeping the app-wide provider.
    """
    sid = _make_sale(admin_client, "PI-PARSER")
    r = _put_json(admin_client, f"/api/sales/{sid}/payment",
                  '{"payment_amount": 100, "payment_date": "2026-01-01", '
                  '"junk": Infinity}')
    assert r.status_code == 400, r.get_data(as_text=True)
    assert db.execute("SELECT count(*) FROM sale_payments WHERE sale_id = %s",
                      (sid,)).fetchone()[0] == 0


# ====================== HTTP: body-parse failure shape ======================

def test_malformed_json_body_is_400_not_500(admin_client):
    r = _put_json(admin_client, "/api/sales/1/payment", "{not json")
    assert r.status_code == 400, r.get_data(as_text=True)
    assert r.is_json
    assert r.get_json().get("error")


def test_empty_body_still_reports_the_models_own_validation_error(admin_client):
    """`{}` parses, so it must still reach the model and fail there."""
    r = _put_json(admin_client, "/api/sales/1/payment", "{}")
    assert r.status_code == 400
    body = r.get_json()
    assert body["error"] == "Invalid payload"
    assert body["details"][0]["field"] == "payment_amount"


# ============================ happy path ============================

def test_ordinary_numeric_body_still_works(admin_client):
    sid = _make_sale(admin_client, "PI-OK")
    r = admin_client.put(f"/api/sales/{sid}/payment",
                         json={"payment_amount": 250.5,
                               "payment_date": "2026-01-01"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["total_paid"] == 250.5


def test_quoted_numeric_string_still_works(admin_client):
    """The frontend sends money as a string; that path must survive."""
    sid = _make_sale(admin_client, "PI-STR")
    r = _put_json(admin_client, f"/api/sales/{sid}/payment",
                  '{"payment_amount": "250.50", "payment_date": "2026-01-01"}')
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["total_paid"] == 250.5


def test_quoted_infinity_string_is_rejected_by_float_parsing(admin_client):
    """`"Infinity"` stays a string and fails coercion — unchanged, still 400."""
    sid = _make_sale(admin_client, "PI-QINF")
    r = _put_json(admin_client, f"/api/sales/{sid}/payment",
                  '{"payment_amount": "Infinity", "payment_date": "2026-01-01"}')
    assert r.status_code == 400, r.get_data(as_text=True)


def test_large_but_finite_amount_still_works(admin_client, db):
    """A big-but-finite magnitude is not a finiteness violation.

    `1e30` rather than `1e308`: `REAL` is float4 (max ~3.4e38), so this is the
    largest magnitude that is simultaneously finite *and* storable. It proves
    the provider is not a magnitude cap.
    """
    sid = _make_sale(admin_client, "PI-BIG")
    r = _put_json(admin_client, f"/api/sales/{sid}/payment",
                  '{"payment_amount": 1e30, "payment_date": "2026-01-01"}')
    assert r.status_code == 200, r.get_data(as_text=True)
    stored = db.execute("SELECT payment_amount FROM sale_payments "
                        "WHERE sale_id = %s", (sid,)).fetchone()[0]
    assert math.isfinite(stored) and stored == 1e30


def test_non_money_route_with_an_ordinary_body_still_works(admin_client, db):
    r = admin_client.post("/api/chemicals",
                          json={"name": "FineChem", "qty": 12.5, "unit": "KG"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert db.execute("SELECT current_qty FROM chemicals WHERE name = %s",
                      ("FineChem",)).fetchone()[0] == 12.5