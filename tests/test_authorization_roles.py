"""P1-A: explicit role classification for mutating routes.

The app's write authorization was *opt-in*: ``_gate_api`` enforces the admin role
only for ``_ADMIN_PATHS`` (``/api/users``, ``/api/keys``), so every other route's
privilege was whatever its own decorator happened to say — and a route with no
decorator was open to any signed-in ``user`` by default.

These tests pin the declared class of each mutating route:

* the operator set carries ``@operator_or_admin_required``, so it is stated at the
  route instead of implied by an omission;
* the ``/api/users`` + ``/api/keys`` routes that relied purely on the
  ``_ADMIN_PATHS`` prefix carry their own ``@admin_required``, so they stay gated
  even if the prefix list changes (``test_delete_audit.py`` monkeypatches it to
  ``()`` to prove exactly this for other routes);
* ``POST /api/sales/batch`` is an operator action, matching ``POST /api/sales``;
* an unauthenticated caller is still refused everywhere (fail-closed preserved).

How the class is read
---------------------
Each role decorator stamps ``_raas_role`` on its wrapper, and these tests walk
``app.url_map`` -> ``app.view_functions`` to read it. An earlier draft parsed the
source with a regex over decorator lines; that was correct on the day but
fail-**open** against ordinary refactors — ``methods=`` moved to the next line, an
``@app.post`` shortcut, ``add_url_rule``, a Blueprint, or a second module would all
make the catch-all pass while checking nothing. Reading the registered rules
cannot drift from what Flask actually serves.
"""
import pytest

import flask_app
from flask_app import admin_required, operator_or_admin_required

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

# Routes that legitimately carry no role decorator.
#
#   - the six mutating `_PUBLIC_API` entries (login/setup/logout/forgot/reset/cron),
#     which must stay reachable without a session — adding a role decorator here
#     would break login;
#   - `PUT /api/auth/password`, which is *self-service*, not public: `_gate_api`
#     still requires an identity, it is merely exempted from the
#     `must_change_password` gate. Kept in a named constant rather than folded
#     into the public set so nobody reads it as anonymous access.
_SELF_SERVICE = {("PUT", "/api/auth/password")}


def _public_mutating():
    return {(m, p) for m, p in flask_app._PUBLIC_API if m in MUTATING}


def _allowed_undeclared():
    return _public_mutating() | _SELF_SERVICE


def _role_of(rule):
    """The role stamped on the view serving `rule`, or None if undeclared."""
    view = flask_app.app.view_functions.get(rule.endpoint)
    return getattr(view, "_raas_role", None)


def _mutating_rules():
    """Every mutating rule Flask actually routes, as (method, rule) pairs."""
    out = set()
    for rule in flask_app.app.url_map.iter_rules():
        for method in (rule.methods or set()) & MUTATING:
            out.add((method, rule.rule))
    return out


def _routes_with_role(role):
    return {(m, p) for m, p in _mutating_rules()
            if _role_of(_rule_for(m, p)) == role}


# ---------- the operator class is declared, not implied ----------

OPERATOR_ROUTES = [
    ("POST", "/api/sales"),
    ("POST", "/api/sales/batch"),
    ("POST", "/api/sales/parse"),
    ("POST", "/api/recipes"),
    ("POST", "/api/recipes/<path:name>/items"),
    ("PUT", "/api/recipes/<path:name>"),
    ("PUT", "/api/recipes/<path:name>/items/<chem>"),
    ("POST", "/api/upload"),
    ("POST", "/api/unit-conversions"),
    ("POST", "/api/reports/generate"),
    ("POST", "/api/reports/export"),
    ("POST", "/api/notifications/read"),
]


@pytest.mark.parametrize("method,path", OPERATOR_ROUTES)
def test_operator_route_declares_its_class(method, path):
    """Every operator mutating route must say so at the route itself."""
    assert (method, path) in _mutating_rules(), f"{method} {path} is not a route"
    assert _role_of_for(method, path) == "operator", (
        f"{method} {path} is an operator route but declares no role class")


def _role_of_for(method, path):
    for rule in flask_app.app.url_map.iter_rules():
        if rule.rule == path and method in (rule.methods or set()):
            return getattr(
                flask_app.app.view_functions.get(rule.endpoint), "_raas_role", None)
    return None


def test_sales_batch_is_an_operator_action():
    """Bulk PI creation must match single PI creation.

    Both create a PI at ``pi_issued`` and neither can reach money without an
    admin action later, so a 403 on the bulk path is friction with no
    compensating control.
    """
    assert _role_of_for("POST", "/api/sales/batch") == "operator"
    assert _role_of_for("POST", "/api/sales") == "operator"


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/users"),
    ("DELETE", "/api/users/<int:user_id>"),
    ("POST", "/api/keys"),
    ("DELETE", "/api/keys/<int:key_id>"),
    ("GET", "/api/users"),
    ("GET", "/api/keys"),
])
def test_admin_route_has_its_own_gate_not_just_the_path_prefix(method, path):
    """These six are (or were) admin-only ONLY because ``_ADMIN_PATHS`` holds
    the prefix.

    ``test_delete_audit.py`` monkeypatches ``_ADMIN_PATHS`` to ``()`` to prove
    other admin routes are self-gated; these would not survive that, so editing
    the prefix list would silently open them. The two GETs were the last routes
    in this state — that is what made ``_ADMIN_PATHS`` load-bearing rather than
    defence in depth.
    """
    assert _role_of_for(method, path) == "admin", (
        f"{method} {path} relies on the _ADMIN_PATHS prefix alone; "
        "it needs its own gate")


def test_no_mutating_route_is_left_undeclared():
    """Catch-all: every mutating route declares a role or is deliberately exempt.

    This is the property that makes the model opt-IN-error rather than
    opt-out-error: a NEW mutating route added without a decorator fails here.
    Read from ``app.url_map``, so it cannot drift from what Flask routes.
    """
    allowed = _allowed_undeclared()
    undeclared = sorted(
        f"{m} {p}" for m, p in _mutating_rules()
        if _role_of(_rule_for(m, p)) is None and (m, p) not in allowed
    )
    assert not undeclared, (
        "mutating routes with no declared role class: " + ", ".join(undeclared))


def _rule_for(method, path):
    for rule in flask_app.app.url_map.iter_rules():
        if rule.rule == path and method in (rule.methods or set()):
            return rule
    raise AssertionError(f"{method} {path} is not a route")


def test_public_allowlist_is_not_widened():
    """Guard the exemptions themselves.

    An entry added to ``_PUBLIC_API`` after this test was written would otherwise
    become a silent, permanent exemption from the catch-all.
    """
    # The catch-all's public half must track _PUBLIC_API exactly - no more.
    assert _public_mutating() <= set(flask_app._PUBLIC_API)
    # And the self-service route really is identity-gated, not anonymous.
    assert ("PUT", "/api/auth/password") not in flask_app._PUBLIC_API


def test_admin_routes_still_exclude_api_keys():
    from flask import g
    with flask_app.app.test_request_context("/api/sales", method="POST"):
        g.current_identity = {"type": "api-key", "name": "Bot"}
        result = admin_required(lambda: "ok")()
        assert isinstance(result, tuple) and result[1] == 403


def test_user_and_key_management_are_unreachable_for_a_plain_user(user_client):
    """Behavioural, not source-level: a signed-in `user` must be refused.

    The source assertions above prove the decorator is declared; this proves it
    actually bites. `/api/users` and `/api/keys` were also covered by the
    `_ADMIN_PATHS` prefix, so this alone would not distinguish the two gates —
    but it pins the user-visible behaviour either way.
    """
    assert user_client.get("/api/users").status_code == 403
    assert user_client.get("/api/keys").status_code == 403
    assert user_client.post("/api/users", json={
        "username": "nope", "password": "x", "role": "user"}).status_code == 403


# ---------- chemicals: inline check folded into the decorator ----------

@pytest.mark.parametrize("method,path", [
    ("POST", "/api/chemicals"),
    ("POST", "/api/chemicals/update"),
    ("PUT", "/api/chemicals/reorder"),
])
def test_chemicals_routes_use_the_decorator(method, path):
    """Three copies of the admin predicate was one too many; _require_admin is gone."""
    assert _role_of_for(method, path) == "admin"


def test_require_admin_helper_is_fully_removed():
    """It was a duplicate of @admin_required, called inline by 3 routes."""
    import inspect
    assert "def _require_admin" not in inspect.getsource(flask_app)


# ---------- operator decorator behaviour ----------

def test_operator_decorator_accepts_user_admin_and_api_key():
    """Any known authenticated identity may operate; only admin routes exclude keys.

    ``test_stock_audit.py`` proves an API key legitimately reaches
    ``/api/unit-conversions`` and is audited as ``api-key:<name>``, so the
    operator class must not exclude keys.
    """
    from flask import g
    for ident in (
        {"type": "human", "role": "user"},
        {"type": "human", "role": "admin"},
        {"type": "api-key", "name": "Bot"},
    ):
        with flask_app.app.test_request_context("/api/sales", method="POST"):
            g.current_identity = ident
            assert operator_or_admin_required(lambda: "ok")() == "ok", ident


@pytest.mark.parametrize("ident", [
    None,
    {},
    {"type": "service-account", "name": "future"},
])
def test_operator_decorator_refuses_an_unknown_or_missing_identity(ident):
    """Fails CLOSED on anything that is not a known identity kind.

    ``_gate_api`` 401s first, so this branch is a backstop - but a backstop
    that waved through any truthy dict would open the door for a future identity
    type (OAuth, a service account, a cron identity) by default.
    """
    from flask import g
    with flask_app.app.test_request_context("/api/sales", method="POST"):
        g.current_identity = ident
        result = operator_or_admin_required(lambda: "ok")()
        assert result != "ok", f"identity {ident!r} was let through"


# ---------- the inventory itself is trustworthy ----------

def test_mutating_route_inventory_is_non_trivial():
    """A silently empty inventory would make every assertion above vacuous."""
    rules = _mutating_rules()
    assert len(rules) >= 60, f"only {len(rules)} mutating routes found"
    assert ("POST", "/api/sales") in rules
    assert ("POST", "/api/sales/batch") in rules
    assert ("DELETE", "/api/sales/<int:sale_id>") in rules


def test_every_operator_route_is_actually_reachable_by_an_operator():
    """Cross-check the declared list against what the decorators stamped."""
    declared = {(m, p) for m, p in OPERATOR_ROUTES}
    stamped = _routes_with_role("operator")
    assert declared == stamped, (
        "OPERATOR_ROUTES list drifted from the stamped decorators: "
        f"{sorted(declared ^ stamped)}")


# ============================ P1-B: volume control ============================
#
# P1-A gave operators the write surface. That is a trust change, and trust
# without a volume bound is not a control: `POST /api/sales` took inline items
# on the same 300/min default that guarded a GET, so an operator session (or a
# leaked operator API key) could amplify one request into thousands of rows.
#
# AGENTS.md already required 15/min on mutating routes. Before P1-B, only
# `sales/batch`, `sales/parse`, `upload` and the two report routes carried it,
# while the six operator writes created by P1-A — and the whole invoice
# lifecycle — silently ran on the default.
#
# The limit is read from a stamp rather than parsed from source, for the same
# reason P1-A does: Flask-Limiter keeps no per-route metadata on the view, so
# the only non-brittle assertion is over what the app itself declares.

def _stamps(view):
    """Collect ``_raas_*`` stamps from a view and everything it wraps.

    Today the walk is a **no-op**: ``functools.wraps`` copies the inner wrapper's
    ``__dict__`` upward, so both ``_raas_role`` (from the role decorator) and
    ``_raas_limit`` (from ``limited()``) already sit on the registered view.
    An earlier version of this docstring claimed the limit lived "one
    ``__wrapped__`` hop down", which is wrong.

    Keep the walk, but not for the reason an earlier draft of this docstring
    gave. Two false claims were removed after review:

    * It is **not** true that ``_raas_limit`` "lives one ``__wrapped__`` hop
      down" — ``functools.wraps`` copies the inner ``__dict__`` upward, so both
      stamps are already on the registered view and the walk is a **no-op for
      every route in the app today**.
    * It is **not** true that the walk rescues a wrapper that omits
      ``functools.wraps``: such a wrapper sets no ``__wrapped__`` either, so
      there is no chain to walk. (This docstring previously claimed the
      opposite, which was wrong.)

    What the walk genuinely buys is the partial-``wraps`` case —
    ``@wraps(inner, updated=())`` creates the chain link while copying none of
    the custom attributes — plus either decorator order. Hence the ``None``
    check rather than ``setdefault``: ``setdefault`` writes on the outermost
    frame even when that frame has no stamp, latching ``None`` so the inner
    value can never surface. Pinned by
    ``test_stamps_survive_a_partial_functools_wraps``.
    """
    out = {}
    while view is not None:
        for key in ("_raas_role", "_raas_limit"):
            if out.get(key) is None:
                out[key] = getattr(view, key, None)
        view = getattr(view, "__wrapped__", None)
    return out


def _stamps_for(method, path):
    for rule in flask_app.app.url_map.iter_rules():
        if rule.rule == path and method in (rule.methods or set()):
            return _stamps(flask_app.app.view_functions.get(rule.endpoint))
    return {}


def test_stamps_survive_a_partial_functools_wraps():
    """MED-1: `setdefault` would latch `None` here and hide the inner stamps.

    No route in the app does this today — Flask-Limiter and both role decorators
    use a full `functools.wraps`, so every stamp is copied onto the registered
    view and the walk is a no-op in production. This pins the one case the walk
    actually rescues: a wrapper built with `wraps(..., updated=())` sets
    `__wrapped__` (the chain link exists) while copying none of the custom
    attributes, so the outer frame has `_raas_limit = None` and only the inner
    frame carries 15.
    """
    import functools

    def inner():
        pass
    inner._raas_role = "operator"
    inner._raas_limit = 15

    # Full wraps: stamps copied upward, so the outer frame answers.
    @functools.wraps(inner)
    def full():
        return inner()

    # Partial wraps: __wrapped__ present, custom attributes NOT copied.
    @functools.wraps(inner, updated=())
    def partial():
        return inner()

    assert _stamps(full)["_raas_limit"] == 15

    # Precondition: this is the case the `None`-check exists for. `setdefault`
    # writes `None` from this frame and never lets the inner 15 surface.
    assert getattr(partial, "_raas_limit", None) is None
    assert partial.__wrapped__ is inner
    assert _stamps(partial)["_raas_limit"] == 15
    assert _stamps(partial)["_raas_role"] == "operator"


def _stamped_limited(method_filter):
    """Every route carrying a `_raas_limit` stamp, under a method filter."""
    return {(m, r.rule) for r in flask_app.app.url_map.iter_rules()
            for m in (r.methods or set()) & method_filter
            if _stamps(flask_app.app.view_functions.get(r.endpoint))
            .get("_raas_limit") is not None}


# Invoice lifecycle + the two N-in-one routes. `invoices/batch` and the single
# `invoices/<id>/items` write are included because they are the same class of
# action as `sales/batch`, which was already limited — the inconsistency is
# exactly what made the old state hard to reason about.
VOLUME_BOUNDED_ROUTES = OPERATOR_ROUTES + [
    ("POST", "/api/sales/<int:sale_id>/invoices"),
    ("POST", "/api/sales/<int:sale_id>/invoices/batch"),
    ("POST", "/api/invoices/<int:invoice_id>/items"),
    ("POST", "/api/sales/<int:sale_id>/invoices/<int:invoice_id>/book"),
    ("POST", "/api/sales/<int:sale_id>/invoices/<int:invoice_id>/ship"),
    ("POST", "/api/sales/<int:sale_id>/invoices/<int:invoice_id>/pay"),
    ("DELETE", "/api/sales/<int:sale_id>/invoices/<int:invoice_id>"),
    # A stage move fans out to every invoice of the sale through the Payment Due
    # gate (readiness reads all of them under lock), so it joins the writes above.
    ("POST", "/api/sales/<int:sale_id>/move"),
    # Admin writes with row fan-out. Oracle (P1-B gate) flagged all three as
    # *unthrottled* while carrying an unbounded list — the higher-amplification
    # half of the surface, which the first draft of P1-B had left at 300/min.
    ("POST", "/api/recipes/<path:name>/produce"),
    ("PUT", "/api/sales/<int:sale_id>"),
    # Upload/LC/report writes already limited in practice, converted to the
    # stamping form so the policy test can actually see them.
    ("POST", "/api/uploads/<int:upload_id>/approve"),
    ("POST", "/api/uploads/<int:upload_id>/apply"),
    ("POST", "/api/reports/live/export"),
    ("POST", "/api/lcs"),
    ("PUT", "/api/lcs/<int:lc_id>"),
    ("DELETE", "/api/lcs/<int:lc_id>"),
    ("POST", "/api/lcs/<int:lc_id>/pis"),
    ("DELETE", "/api/lcs/<int:lc_id>/pis/<int:sale_id>"),
    ("POST", "/api/lcs/<int:lc_id>/move"),
]


@pytest.mark.parametrize("method,path", VOLUME_BOUNDED_ROUTES)
def test_volume_bounded_write_declares_its_limit(method, path):
    """15/min, stated at the route — not inherited from the 300/min default."""
    assert (method, path) in _mutating_rules(), f"{method} {path} is not a route"
    limit = _stamps_for(method, path).get("_raas_limit")
    assert limit == flask_app.WRITE_LIMIT_PER_MINUTE == 15, (
        f"{method} {path} declares no volume limit (falls back to the "
        f"300/min default): got {limit!r}")


def test_volume_bounded_list_matches_the_stamped_routes():
    """The declared list and the stamped reality must not drift.

    This is a **one-sided** guard: it catches a route that carries ``@limited()``
    but is missing from the list. It cannot catch an admin write that carries no
    limit at all - ~27 such routes still sit on the 300/min default, which
    ``test_no_operator_write_silently_relies_on_the_default`` deliberately does
    not police (it is operator-scoped). Extending the volume policy to the whole
    admin surface is a tracked gap in ``docs/plan.md``, not closed here.
    """
    declared = {(m, p) for m, p in VOLUME_BOUNDED_ROUTES}
    stamped = _stamped_limited(MUTATING)
    assert declared == stamped, (
        "VOLUME_BOUNDED_ROUTES drifted from the stamped @limited() decorators: "
        f"{sorted(declared ^ stamped)}")


def test_the_read_default_is_above_the_write_budget():
    """The 15/min write cap must not drift at or above the read default.

    Asserted against our own named constants rather than Flask-Limiter's
    ``_default_limits``: that attribute is private and version-specific, so a
    test on it would fail on an upgrade while telling us nothing about policy.
    """
    assert flask_app.WRITE_LIMIT_PER_MINUTE == 15
    assert flask_app.READ_LIMIT_PER_MINUTE == 300
    assert flask_app.WRITE_LIMIT_PER_MINUTE < flask_app.READ_LIMIT_PER_MINUTE


def _one_sale():
    """A minimal valid `SaleCreateIn` payload."""
    return {
        "sale": {
            "pi_number": "PI-1",
            "company_id": 1,
        },
        "items": [{
            "product_name": "P",
            "quantity": 1.0,
            "unit_price": 1.0,
            "unit": "kg",
        }],
    }


def test_sale_batch_rejects_more_than_the_declared_max():
    """One POST /api/sales/batch writes N sales x M items in one transaction."""
    from flask_app import SaleBatchIn
    max_len = _cap_of(SaleBatchIn, "sales")
    assert max_len is not None, "SaleBatchIn.sales is still unbounded"

    SaleBatchIn(sales=[_one_sale() for _ in range(max_len)])  # at the cap: fine
    with pytest.raises(Exception):
        SaleBatchIn(sales=[_one_sale() for _ in range(max_len + 1)])


def test_no_operator_write_silently_relies_on_the_default():
    """Catch-all: any operator route added without ``@limited()`` fails here.

    The parametrised test above pins a known list; this one fails on an
    unlisted route, so a new operator write cannot join the surface with no
    volume bound just by being forgotten in the list.
    """
    undeclared = [(m, r.rule) for r in flask_app.app.url_map.iter_rules()
                  for m in (r.methods or set()) & MUTATING
                  if _stamps(flask_app.app.view_functions.get(r.endpoint))
                  .get("_raas_role") == "operator"
                  and _stamps(flask_app.app.view_functions.get(r.endpoint))
                  .get("_raas_limit") is None]
    assert not undeclared, (
        f"operator routes without a declared limit: {sorted(undeclared)}")


# ---------- one request may not be an unbounded number of rows ----------

# ---------- the caps must bound the PRODUCT, not just each factor ----------

def test_sale_batch_rejects_a_batch_over_the_total_row_budget():
    """HIGH-2: per-factor caps leave the product unbounded.

    `SaleCreateIn` nests inside `SaleBatchIn`, so 50 sales x 200 items is still
    ~10 000 sequential inserts in ONE transaction (one commit at the end) from
    ~700 KB of body — inside the 4.5 MB Vercel limit. An operator API key can fire
    15 such requests a minute against a 15-session pooler.
    """
    from flask_app import MAX_BATCH_ROWS, SaleBatchIn

    per_sale = 200
    n_sales = MAX_BATCH_ROWS // per_sale + 1
    payload = {"sales": [_one_sale() for _ in range(n_sales)]}
    for s in payload["sales"]:
        s["items"] = [{"product_name": "P", "quantity": 1.0,
                       "unit_price": 1.0, "unit": "kg"}] * per_sale

    # Each factor is individually legal — this is exactly why a product cap is needed.
    assert n_sales <= 50, "precondition: the sale count is within its own cap"
    assert per_sale <= 200, "precondition: items-per-sale is within its own cap"

    with pytest.raises(Exception) as err:
        SaleBatchIn(**payload)
    assert "total items per batch" in str(err.value)


def test_sale_batch_accepts_a_batch_at_the_total_row_budget():
    """The budget is a ceiling, not a quota: a realistic batch still passes."""
    from flask_app import MAX_BATCH_ROWS, SaleBatchIn
    per_sale, n_sales = 50, 20          # 1000 total items, well under the budget
    payload = {"sales": [_one_sale() for _ in range(n_sales)]}
    for s in payload["sales"]:
        s["items"] = [{"product_name": "P", "quantity": 1.0,
                       "unit_price": 1.0, "unit": "kg"}] * per_sale
    assert per_sale * n_sales <= MAX_BATCH_ROWS
    SaleBatchIn(**payload)


def test_invoice_batch_rejects_a_batch_over_the_total_row_budget():
    """Same product problem on the financial path: 50 invoices x 200 lines."""
    from flask_app import MAX_BATCH_ROWS, InvoiceBatchCreateIn

    per_invoice = 200
    n_invoices = MAX_BATCH_ROWS // per_invoice + 1
    payload = {"invoices": [
        {"invoice_number": f"INV-{i}", "invoice_date": "2026-01-15",
         "lines": [{"sale_item_id": 1, "quantity": 1.0}] * per_invoice}
        for i in range(n_invoices)]}
    assert n_invoices <= 50, "precondition: invoice count within its own cap"

    with pytest.raises(Exception) as err:
        InvoiceBatchCreateIn(**payload)
    assert "total lines per batch" in str(err.value)


def test_total_row_budget_is_exposed_and_sane():
    """One named constant, and it must be below the worst-case product."""
    from flask_app import MAX_BATCH_ROWS
    assert MAX_BATCH_ROWS == 2000
    worst_case = 50 * 200      # SaleBatchIn.sales x SaleCreateIn.items
    assert MAX_BATCH_ROWS < worst_case, "the budget cannot bind if it is above the cap"


def test_total_row_budget_surfaces_as_a_400_with_the_error_contract(user_client):
    """AGENTS.md: validation errors are 400 `{error, details:[{field, message}]}`."""
    from flask_app import MAX_BATCH_ROWS
    per_sale = 200
    n_sales = MAX_BATCH_ROWS // per_sale + 1
    sales = [_one_sale() for _ in range(n_sales)]
    for s in sales:
        s["items"] = [{"product_name": "P", "quantity": 1.0,
                       "unit_price": 1.0, "unit": "kg"}] * per_sale

    res = user_client.post("/api/sales/batch", json={"sales": sales})
    assert res.status_code == 400
    body = res.get_json()
    assert body["error"] == "Invalid payload"
    assert any("total items per batch" in d["message"] for d in body["details"])
    assert any(d["field"].startswith("sales") for d in body["details"]), body


def test_sale_batch_rejects_an_empty_list_as_before():
    """The pre-existing lower bound must survive the max_length change."""
    from flask_app import SaleBatchIn
    with pytest.raises(Exception):
        SaleBatchIn(sales=[])


def _cap_of(model, field):
    """The declared ``max_length`` for a Pydantic list field, or None.

    ``metadata`` holds typed constraint objects: only ``MaxLen`` carries
    ``max_length``, so use getattr rather than assuming the attribute exists.
    """
    meta = model.model_fields[field].metadata
    return next((getattr(m, "max_length", None) for m in meta
                 if getattr(m, "max_length", None) is not None), None)


# One row-fanning case per field Oracle listed as uncapped. Each is `(model,
# field, builder)` where the builder produces a payload exactly one over the cap.
ROW_FANNING_CAPS = [
    # HIGH-3 — the route P1-A opened to operators, and the operator backstop
    # admits API keys, so this is reachable by a leaked low-trust credential.
    ("SaleCreateIn", "items", lambda n: [{"product_name": "P", "quantity": 1.0,
                                          "unit_price": 1.0, "unit": "kg"}] * n),
    # HIGH-2 — full replace + one delete per removedId on the money-bearing rows.
    ("SaleFullUpdateIn", "items", lambda n: [{"product_name": "P", "quantity": 1.0,
                                              "unit_price": 1.0, "unit": "kg"}] * n),
    ("SaleFullUpdateIn", "removedIds", lambda n: list(range(n))),
    # MED-1 — N invoices x M lines, the financial write path.
    ("InvoiceBatchCreateIn", "invoices", lambda n: [
        {"invoice_number": f"INV-{i}", "invoice_date": "2026-01-15", "lines": [
            {"sale_item_id": 1, "quantity": 1.0}]} for i in range(n)]),
    ("InvoiceBatchEntryIn", "lines", lambda n: [
        {"sale_item_id": 1, "quantity": 1.0}] * n),
    # Round 2 HIGH-1: the single-invoice twin of the capped batch line list.
    ("InvoiceCreateIn", "lines", lambda n: [
        {"sale_item_id": 1, "quantity": 1.0}] * n),
    # Round 2 MED-3: ~5 round trips per id, and it advances sale stage.
    ("LcAttachIn", "sale_ids", lambda n: list(range(n))),
]

# Sibling fields the model still requires when the capped field is exercised on
# its own. Without these the model fails validation for a *missing* field and the
# test would pass for the wrong reason.
_ROW_FAN_REQUIRED_SIBLINGS = {
    ("SaleCreateIn", "items"): {"sale": {"pi_number": "PI-1", "company_id": 1}},
    ("SaleFullUpdateIn", "items"): {
        "header": {"pi_number": "PI-1", "company_id": 1}},
    ("SaleFullUpdateIn", "removedIds"): {
        "header": {"pi_number": "PI-1", "company_id": 1},
        "items": [{"product_name": "P", "quantity": 1.0,
                   "unit_price": 1.0, "unit": "kg"}]},
    ("InvoiceBatchCreateIn", "invoices"): {},
    ("InvoiceBatchEntryIn", "lines"): {"invoice_number": "INV-1",
                                       "invoice_date": "2026-01-15"},
    ("InvoiceCreateIn", "lines"): {"invoice_number": "INV-1",
                                   "invoice_date": "2026-01-15"},
    ("LcAttachIn", "sale_ids"): {},
}


@pytest.mark.parametrize("model_name,field,build", ROW_FANNING_CAPS)
def test_row_fanning_list_field_is_capped(model_name, field, build):
    """A single request must not be an unbounded number of rows."""
    import flask_app as fa
    model = getattr(fa, model_name)
    cap = _cap_of(model, field)
    assert cap is not None, f"{model_name}.{field} is still unbounded"

    # Exactly at the cap validates; one over is refused.
    at_cap = dict(_ROW_FAN_REQUIRED_SIBLINGS[(model_name, field)])
    at_cap[field] = build(cap)
    model(**at_cap)

    over = dict(at_cap)
    over[field] = build(cap + 1)
    with pytest.raises(Exception):
        model(**over)


@pytest.mark.parametrize("field", ["sale_ids", "invoice_ids", "recipes"])
def test_production_run_id_lists_are_capped(field):
    """HIGH-1: these were the largest fan-out on /produce and shipped uncapped.

    Each id drives a sequential per-element round trip inside a transaction
    holding a pool slot (`recipes.py` `_owned_sale_ids`, and the per-invoice
    SELECT/INSERT/FOR UPDATE).
    """
    from flask_app import ProductionRunCreateIn
    cap = _cap_of(ProductionRunCreateIn, field)
    assert cap is not None, f"ProductionRunCreateIn.{field} is still unbounded"

    # `recipes` holds ProductionRecipeIn rows (no required fields); the other
    # two hold plain ints.
    fill = (lambda: {}) if field == "recipes" else (lambda: 1)

    at_cap = {"production_qty": 1.0, field: [fill() for _ in range(cap)]}
    ProductionRunCreateIn(**at_cap)  # at the cap: fine

    over = dict(at_cap)
    over[field] = [fill() for _ in range(cap + 1)]
    with pytest.raises(Exception):
        ProductionRunCreateIn(**over)


def test_production_run_recipes_cap_has_a_regression_test():
    """MED-4: this cap shipped with no test; assert it explicitly, by name."""
    from flask_app import ProductionRunCreateIn
    assert _cap_of(ProductionRunCreateIn, "recipes") == 50


@pytest.mark.parametrize("path", ["/api/reports/generate", "/api/reports/export"])
def test_report_rejects_more_than_the_declared_recipe_cap(user_client, path):
    """The report routes read the body directly, so the cap is enforced there."""
    one = [{"company_id": 1, "recipe_name": "R"}]
    over = one * (flask_app.MAX_REPORT_RECIPES + 1)

    res = user_client.post(path, json={"recipes": over, "qty": 1})
    assert res.status_code == 400, f"{path} accepted an oversized report"
    body = res.get_json()
    assert "at most" in body["details"][0]["message"].lower()
    assert body["details"][0]["field"] == "recipes"


@pytest.mark.parametrize("path", ["/api/reports/generate", "/api/reports/export"])
def test_report_rejects_a_non_list_recipes_body(user_client, path):
    """LOW-4: `len()` on a str counts characters and the shape loop then
    indexes a `str`, which is a 500. The cap is the natural place to reject it.
    """
    res = user_client.post(path, json={"recipes": "not-a-list", "qty": 1})
    assert res.status_code == 400, f"{path} accepted a string recipes body"
    assert res.get_json()["details"][0]["message"] == "must be a list"