"""P1-7: `X-Forwarded-For` is trusted only when a proxy is declared.

`ProxyFix(x_for=1)` rewrote `request.remote_addr` from the rightmost
X-Forwarded-For entry *unconditionally*, so any client able to reach the app
directly chose its own IP. That value gates three controls:

- the **API-key IP allowlist** (`auth.py:_ip_allowed`) — an authorisation bypass
- **login lockout by IP** (`record_login_attempt` / `is_login_blocked`)
- **Flask-Limiter's IP-keyed buckets** for callers with no resolved identity
  (`flask_app._limit_key` falls back to `get_remote_address()`)

`x_for` is now gated on `TRUSTED_PROXY=1`. `x_proto`, `x_host` and `x_prefix`
stay unconditional on purpose: they drive the HTTPS redirect and URL building,
where over-trust is a correctness problem rather than an authz bypass — and
over-gating them would break deployments rather than secure them.
"""


def _cidr_key(db, allowed_ips):
    """An API key restricted to an allowlist, as an admin would create it."""
    from raas_tracker.auth import create_api_key
    uid = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    return create_api_key(db, "CidrKey", created_by=uid, allowed_ips=allowed_ips)


def _last_company_ip(db):
    return db.execute(
        "SELECT ip_address FROM audit_logs WHERE action = 'COMPANY_CREATE' "
        "ORDER BY id DESC LIMIT 1").fetchone()[0]


# ==================== the authorisation control ====================

def test_forged_forwarded_for_cannot_satisfy_the_api_key_allowlist(
        client, db, proxy_trust):
    """The bypass this closes: an out-of-range client must not be able to claim
    an allowed CIDR by sending the header itself."""
    proxy_trust(None)
    raw = _cidr_key(db, "10.0.0.0/8")
    r = client.get("/api/chemicals", headers={
        "X-API-Key": raw, "X-Forwarded-For": "10.1.2.3"})
    assert r.status_code == 401, \
        "a client-supplied X-Forwarded-For satisfied the IP allowlist"


def test_declared_proxy_is_honoured_for_the_api_key_allowlist(client, db, proxy_trust):
    """With a proxy declared the same request must succeed — real deployments
    depend on this, so the gate must be a switch and not a blanket disable."""
    proxy_trust("1")
    raw = _cidr_key(db, "10.0.0.0/8")
    r = client.get("/api/chemicals", headers={
        "X-API-Key": raw, "X-Forwarded-For": "10.1.2.3"})
    assert r.status_code == 200, r.get_json()


def test_key_without_an_allowlist_is_unaffected(client, db, proxy_trust):
    """An empty allowlist means "any IP" and must keep working either way."""
    proxy_trust(None)
    raw = _cidr_key(db, "")
    assert client.get("/api/chemicals",
                      headers={"X-API-Key": raw}).status_code == 200


# ==================== what the audit trail records ====================

def test_undeclared_proxy_ignores_a_forged_forwarded_header(admin_client, db, proxy_trust):
    """The audit path must ignore a client-supplied header.

    The header is what makes this non-vacuous: without it the test client is
    127.0.0.1 regardless of the gate, so the test would pass either way.
    """
    proxy_trust(None)
    assert admin_client.post(
        "/api/companies", json={"name": "TrustCo"},
        headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 201
    ip = _last_company_ip(db)
    assert ip == "127.0.0.1", f"a client-supplied header was trusted: {ip!r}"


def test_forged_forwarded_for_cannot_reset_the_login_lockout(client, db, proxy_trust):
    """`is_login_backed_off` matches `username = %s OR ip_address = %s`.

    Pre-fix, a fresh header per attempt minted a new IP bucket every time, so
    only the per-username half of the lockout (5 fails / 10 min) stood between
    an attacker and unlimited guesses from a rotating address.

    Each attempt uses a DIFFERENT username on purpose: with a shared username
    the per-username bucket trips regardless of the gate and the test would pass
    either way, proving nothing about the IP half.
    """
    proxy_trust(None)
    codes = [
        client.post("/api/auth/login",
                    json={"username": f"ghost{i}", "password": "nope-123"},
                    headers={"X-Forwarded-For": f"198.51.100.{i}"}).status_code
        for i in range(6)
    ]
    assert codes[-1] == 429, f"per-IP lockout bypassed by rotating the header: {codes}"


def test_reapplying_proxy_fix_does_not_stack_the_middleware(proxy_trust):
    """A regression to `ProxyFix(app.wsgi_app, ...)` would double-apply headers
    while passing every behavioural test, so assert the invariant directly."""
    from werkzeug.middleware.proxy_fix import ProxyFix
    import flask_app
    proxy_trust("1")
    proxy_trust(None)
    proxy_trust("1")
    node, depth = flask_app.app.wsgi_app, 0
    while isinstance(node, ProxyFix):
        depth += 1
        node = node.app
    assert depth == 1, f"ProxyFix applied {depth} times"
    assert node is flask_app._BASE_WSGI_APP, "wrapping app.wsgi_app instead of the base"


def test_declared_proxy_records_the_forwarded_address(admin_client, db, proxy_trust):
    proxy_trust("1")
    assert admin_client.post("/api/companies", json={"name": "TrustCo2"},
                             headers={"X-Forwarded-For": "203.0.113.9"}
                             ).status_code == 201
    assert _last_company_ip(db) == "203.0.113.9"


# ==================== the gate must not over-reach ====================

def test_forwarded_proto_survives_without_a_declared_proxy(proxy_trust, monkeypatch):
    """Over-gating x_proto would break the HTTPS redirect, not secure it."""
    proxy_trust(None)
    monkeypatch.setenv("FORCE_HTTPS", "1")
    import flask_app
    r = flask_app.app.test_client().get(
        "/api/auth/status", headers={"X-Forwarded-Proto": "http"})
    assert r.status_code == 301
    assert (r.headers.get("Location") or "").startswith("https://")


def test_proxyfix_stays_installed_and_keeps_the_other_hops():
    """Gating must disable header trust, never remove the middleware."""
    from werkzeug.middleware.proxy_fix import ProxyFix
    import flask_app
    node = flask_app.app.wsgi_app
    while hasattr(node, "app"):
        if isinstance(node, ProxyFix):
            assert (node.x_proto, node.x_host, node.x_prefix) == (1, 1, 1)
            return
        node = node.app
    raise AssertionError("ProxyFix middleware not found in the WSGI chain")


def test_x_for_reflects_the_trust_flag(proxy_trust):
    """Direct assertion on the middleware, so the contract is explicit."""
    from werkzeug.middleware.proxy_fix import ProxyFix
    import flask_app
    proxy_trust(None)
    assert flask_app.app.wsgi_app.x_for == 0
    proxy_trust("1")
    assert flask_app.app.wsgi_app.x_for == 1