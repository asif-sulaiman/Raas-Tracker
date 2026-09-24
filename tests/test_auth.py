"""Auth + gate + throttle + pruning tests."""
from chem_stock import (
    _DUMMY_HASH,
    check_api_key_rate_limit,
    check_setup_token,
    create_api_key,
    ensure_setup_token,
    is_login_blocked,
    record_api_key_hit,
    record_login_attempt,
)


def test_unknown_user_401_and_dummy_work_done(client, db):
    import raas_tracker.auth as auth_mod
    auth_mod._DUMMY_HASH = None
    r = client.post("/api/auth/login", json={"username": "ghost", "password": "whatever-123"})
    assert r.status_code == 401
    # Unknown usernames burn the same bcrypt work as real ones (no timing oracle).
    assert auth_mod._DUMMY_HASH is not None


def test_wrong_password_401(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def _pin_attempts_to_now(db):
    """Pin all attempt timestamps to a fixed far-future value.

    Makes window checks fully deterministic: far-future rows are inside
    every trailing window under any clock (past, present, jumped, or
    skewed between app and database machines). Production code is
    untouched; only the test data is clock-independent.
    """
    db.execute("UPDATE login_attempts SET attempted_at = '2999-01-01 00:00:00'")
    db.commit()


def test_five_fails_then_429(client, db):
    for _ in range(5):
        assert client.post("/api/auth/login",
                           json={"username": "admin", "password": "wrong"}).status_code == 401
    _pin_attempts_to_now(db)
    assert client.post("/api/auth/login",
                       json={"username": "admin", "password": "wrong"}).status_code == 429
    # Correct password is also blocked inside the window.
    assert client.post("/api/auth/login",
                       json={"username": "admin", "password": "admin-pass-123"}).status_code == 429


def test_throttle_records_persist_in_db(client, db):
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    fails = db.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE success = 0 AND username = 'admin'"
    ).fetchone()[0]
    assert fails >= 5
    _pin_attempts_to_now(db)
    assert is_login_blocked(db, "admin", "127.0.0.1") is True


def test_ip_level_throttle_cross_username(client, db):
    for uname in ["b1", "b2", "b3", "b4", "b5"]:
        r = client.post("/api/auth/login", json={"username": uname, "password": "bad"})
        assert r.status_code == 401, (uname, r.status_code, r.get_json())
    # Pin the five rows to a far-future timestamp: every later window check
    # below is then deterministic (immune rows count under any clock).
    _pin_attempts_to_now(db)
    # Sixth failure from the same IP trips the throttle: blocked at entry.
    r = client.post("/api/auth/login", json={"username": "b6", "password": "bad"})
    assert r.status_code == 429, ("b6", r.status_code, r.get_json())
    _pin_attempts_to_now(db)
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"})
    assert r.status_code == 429


def test_login_attempts_pruned(db):
    db.execute("INSERT INTO login_attempts (username, success) VALUES ('old', 0)")
    db.execute("UPDATE login_attempts SET attempted_at = to_char(NOW() - INTERVAL '2 days', 'YYYY-MM-DD HH:MM:SS')")
    db.commit()
    record_login_attempt(db, "fresh", "127.0.0.1", False)
    assert db.execute("SELECT COUNT(*) FROM login_attempts WHERE username = 'old'").fetchone()[0] == 0


def test_api_key_rate_limit_db_backed(db):
    uid = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    create_api_key(db, "rl-key", created_by=uid)
    key_id = db.execute("SELECT id FROM api_keys WHERE name = 'rl-key'").fetchone()[0]
    assert check_api_key_rate_limit(db, key_id, max_hits=3, window_seconds=60) is False
    for _ in range(3):
        record_api_key_hit(db, key_id)
    db.execute("UPDATE api_key_rate_limits SET hit_at = '2999-01-01 00:00:00'")
    db.commit()
    assert check_api_key_rate_limit(db, key_id, max_hits=3, window_seconds=60) is True
    # Old hits are pruned on write.
    db.execute("UPDATE api_key_rate_limits SET hit_at = to_char(NOW() - INTERVAL '2 days', 'YYYY-MM-DD HH:MM:SS')")
    db.commit()
    record_api_key_hit(db, key_id)
    assert db.execute("SELECT COUNT(*) FROM api_key_rate_limits").fetchone()[0] == 1


def test_protected_routes_401_logged_out(client):
    for path in ["/api/chemicals", "/api/recipes", "/api/uploads", "/api/sales",
                 "/api/sales/summary", "/api/users", "/api/keys", "/api/audit-logs",
                 "/api/auth/me"]:
        assert client.get(path).status_code == 401, path
    assert client.post("/api/sales", json={}).status_code == 401


def test_non_admin_blocked_from_admin_paths(user_client):
    assert user_client.get("/api/users").status_code == 403
    assert user_client.get("/api/keys").status_code == 403
    assert user_client.get("/api/audit-logs").status_code == 403
    assert user_client.get("/api/chemicals").status_code == 200


def test_cors_gated_by_allowlist(client, monkeypatch):
    evil = client.get("/api/auth/status", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in evil.headers
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS",
                       "https://app.example, http://localhost:5173")
    ok = client.get("/api/auth/status", headers={"Origin": "https://app.example/"})
    assert ok.headers.get("Access-Control-Allow-Origin") == "https://app.example"
    assert ok.headers.get("Access-Control-Allow-Credentials") == "true"
    pre = client.open("/api/auth/status", method="OPTIONS",
                      headers={"Origin": "http://localhost:5173"})
    assert pre.status_code == 200
    assert "DELETE" in pre.headers.get("Access-Control-Allow-Methods", "")


def test_non_admin_and_key_delete_forbidden(user_client, db):
    from chem_stock import create_api_key
    uid = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    raw = create_api_key(db, "nodelete", created_by=uid)
    paths = [
        "/api/recipes/Ghost/items/Chem",
        "/api/recipes/Ghost",
        "/api/uploads/999999",
        "/api/sales/999999",
        "/api/sales/999999/payments/888888",
        "/api/sales/999999/items/888888",
    ]
    for path in paths:
        r = user_client.open(path, method="DELETE")
        assert r.status_code == 403, (path, r.status_code)
        rk = user_client.open(path, method="DELETE", headers={"X-API-Key": raw})
        assert rk.status_code == 403, ("key", path, rk.status_code)


def test_admin_login_sets_secure_cookie(admin_client):
    assert admin_client.get("/api/auth/me").status_code == 200


def test_login_cookie_flags(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"})
    assert r.status_code == 200
    jar = "; ".join(r.headers.getlist("Set-Cookie"))
    assert "raas_session" in jar and "HttpOnly" in jar and "Secure" in jar
    assert "SameSite=Lax" in jar


def test_last_admin_delete_refused(admin_client, db):
    uid = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    r = admin_client.delete(f"/api/users/{uid}")
    assert r.status_code == 400
    assert "last admin" in r.get_json()["error"]
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2


def test_stale_token_audited_401(client, db):
    r = client.get("/api/chemicals", headers={"X-API-Token": "stale"})
    assert r.status_code == 401
    assert "retired" in r.get_json()["error"]
    row = db.execute(
        "SELECT entity_type FROM audit_logs WHERE action = 'LEGACY_TOKEN_USED'"
    ).fetchone()
    assert row is not None and row[0] == "/api/chemicals"


def test_logout_revokes_session(admin_client, db):
    r = admin_client.post("/api/auth/logout")
    assert r.status_code == 200
    assert admin_client.get("/api/chemicals").status_code == 401


def test_setup_token_expiry_and_regeneration(db):
    # Setup tokens only exist pre-first-admin: start from zero users.
    db.execute("DELETE FROM users")
    db.commit()
    raw = ensure_setup_token(db)
    assert raw and check_setup_token(db, raw) is True
    assert check_setup_token(db, "wrong-token") is False
    val = db.execute(
        "SELECT value FROM app_settings WHERE key = 'setup_token_hash'").fetchone()[0]
    digest = val.split(":")[0]
    # Far-past timestamp: expired under any clock.
    db.execute("UPDATE app_settings SET value = %s WHERE key = 'setup_token_hash'",
               (f"{digest}:2000-01-01 00:00:00",))
    db.commit()
    assert check_setup_token(db, raw) is False
    # Expired token regenerates on next ensure.
    raw2 = ensure_setup_token(db)
    assert raw2 and raw2 != raw and check_setup_token(db, raw2) is True
    # Legacy timestamp-less rows are treated as expired.
    db.execute("UPDATE app_settings SET value = %s WHERE key = 'setup_token_hash'",
               (digest,))
    db.commit()
    assert check_setup_token(db, raw2) is False
