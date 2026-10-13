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