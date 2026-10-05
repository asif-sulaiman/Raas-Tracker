"""P0 audit hardening: deterministic per-request actor + real client IP.

The actor used to be a bare ``threading.local`` written only on the success
path of ``_gate_api``, so under waitress's thread pool a public or rejected
request inherited the *previous* request's username. That mis-attributed
security-relevant events (password reset, rejected legacy token) to an
unrelated user. These tests pin the deterministic behaviour:

- every ``/api`` request starts from the "anonymous" label and is reset to
  the verified identity only after authentication succeeds
- the actor is dropped at teardown, so a pooled thread carries nothing over
- audit rows carry the real client IP as ProxyFix reports it
- a caller-supplied ``ip_address`` is never clobbered by that default
"""
import pytest


def _uid(db, username):
    return db.execute("SELECT id FROM users WHERE username = %s", (username,)).fetchone()[0]


def _last(db, action):
    return db.execute(
        "SELECT user_id, entity_id, ip_address FROM audit_logs "
        "WHERE action = %s ORDER BY id DESC LIMIT 1", (action,)).fetchone()


def _arm_stale_actor(client):
    """Serve an authenticated admin request on this thread.

    Pre-fix the thread-local keeps "admin" afterwards; post-fix teardown
    leaves nothing behind. Either way the *next* request must not inherit it.
    """
    assert client.post("/api/auth/login",
                       json={"username": "admin", "password": "admin-pass-123"}).status_code == 200
    assert client.get("/api/auth/me").status_code == 200


# ==================== actor must not leak across requests ====================

def test_forgot_password_not_attributed_to_previous_user(client, db):
    """Public forgot-password must not inherit the previous request's actor."""
    uid = _uid(db, "user")
    _arm_stale_actor(client)
    assert client.post("/api/auth/forgot-password", json={"username": "user"}).status_code == 200
    user_id, entity_id, _ = _last(db, "PASSWORD_RESET_REQUESTED")
    assert entity_id == uid
    assert user_id == "anonymous", f"leaked previous actor: {user_id!r}"


def test_password_reset_success_not_attributed_to_previous_user(client, db):
    """Public reset-password (token + new password) must log the anonymous actor."""
    from raas_tracker.auth import issue_reset_token
    uid = _uid(db, "user")
    raw = issue_reset_token(db, uid)
    _arm_stale_actor(client)
    r = client.post("/api/auth/reset-password",
                    json={"token": raw, "new_password": "brand-new-pass-1"})
    assert r.status_code == 200, r.get_json()
    user_id, entity_id, _ = _last(db, "PASSWORD_RESET_SUCCESS")
    assert entity_id == uid
    assert user_id == "anonymous", f"leaked previous actor: {user_id!r}"
    # The password change logged inside the same public request too.
    assert _last(db, "PASSWORD_CHANGED")[0] == "anonymous"


def test_legacy_token_rejection_not_attributed_to_previous_user(client, db):
    """A rejected legacy token is audited before identity resolution."""
    _arm_stale_actor(client)
    r = client.get("/api/auth/me", headers={"X-API-Token": "anything"})
    assert r.status_code == 401
    user_id, _, _ = _last(db, "LEGACY_TOKEN_USED")
    assert user_id == "anonymous", f"leaked previous actor: {user_id!r}"


def test_actor_cleared_after_request(client):
    from raas_tracker.audit import get_audit_actor
    _arm_stale_actor(client)
    assert get_audit_actor() == "system", "actor survived request teardown"


def test_cron_run_is_audited_as_cron(client, db, monkeypatch):
    """The maturity job records a row and attributes it to "cron".

    Goes through the route (not the resolver) so this fails if the route ever
    stops auditing — the label is only meaningful if something is written.
    """
    monkeypatch.setenv("CRON_SECRET", "test-secret")
    r = client.post("/api/cron/maturity-check",
                    headers={"Authorization": "Bearer test-secret"})
    assert r.status_code == 200, r.get_json()
    row = db.execute(
        "SELECT user_id, entity_type, new_value FROM audit_logs "
        "WHERE action = 'CRON_MATURITY_CHECK' ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None, "the maturity job wrote no audit row"
    assert (row[0], row[1]) == ("cron", "cron")
    assert row[2].startswith("checked=")


def test_cron_unauthorized_is_anonymous(client, db, monkeypatch):
    """A rejected cron call must not inherit the previous request's actor."""
    monkeypatch.setenv("CRON_SECRET", "test-secret")
    _arm_stale_actor(client)
    r = client.post("/api/cron/maturity-check",
                    headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    assert _last(db, "CRON_MATURITY_CHECK") is None


def test_explicit_actor_argument_wins(db):
    from raas_tracker.audit import log_audit_action, set_audit_actor
    set_audit_actor("someone-else")
    try:
        log_audit_action(db, "EXPLICIT_PROBE", user_id="admin")
        assert _last(db, "EXPLICIT_PROBE")[0] == "admin"
    finally:
        set_audit_actor(None)


# ==================== client IP capture ====================

def test_mutation_records_client_ip(admin_client, db):
    assert admin_client.post("/api/companies", json={"name": "IpCo"}).status_code == 201
    assert _last(db, "COMPANY_CREATE")[2] == "127.0.0.1"


def test_audit_ip_follows_proxy_fix_forwarded_for(admin_client, db):
    """The recorded IP is ProxyFix's remote_addr (one trusted hop).

    ``ProxyFix(x_for=1)`` rewrites remote_addr from the *rightmost*
    X-Forwarded-For entry — the hop the proxy itself appended. Pinned so the
    IP behaviour is explicit rather than incidental, and so any change to
    ProxyFix's trust is a deliberate diff here.
    """
    assert admin_client.post(
        "/api/companies", json={"name": "ProxyCo"},
        headers={"X-Forwarded-For": "203.0.113.9, 70.41.3.18"}).status_code == 201
    assert _last(db, "COMPANY_CREATE")[2] == "70.41.3.18"


def test_explicit_ip_argument_is_not_overridden(admin_client, db):
    """ADJUST_STOCK passes the operator's free-text reason as ip_address.

    The new IP default must not overwrite it (that reason is contractual and
    asserted by test_chemicals_api.py::..._persists_reason_in_audit).
    """
    assert admin_client.post("/api/chemicals", json={"name": "ReasonKeep", "qty": 10}).status_code in (200, 201)
    assert admin_client.post("/api/chemicals/update", json={
        "name": "ReasonKeep", "delta": 5, "reason": "Supplier delivery"}).status_code == 200
    row = db.execute("SELECT old_value, new_value, ip_address FROM audit_logs "
                     "WHERE action = 'ADJUST_STOCK' ORDER BY id DESC LIMIT 1").fetchone()
    assert (row[0], row[1], row[2]) == ("10.0", "15.0", "Supplier delivery")


def test_ip_is_none_outside_request_context(db):
    from raas_tracker.audit import log_audit_action, request_ip
    assert request_ip() is None
    log_audit_action(db, "NO_REQUEST_PROBE")
    assert _last(db, "NO_REQUEST_PROBE")[2] is None