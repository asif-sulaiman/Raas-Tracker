"""Auth + gate + throttle + pruning tests."""
from chem_stock import (
    _DUMMY_HASH,
    check_api_key_rate_limit,
    create_api_key,
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


def test_five_fails_then_429(client, db):
    for _ in range(5):
        assert client.post("/api/auth/login",
                           json={"username": "admin", "password": "wrong"}).status_code == 401
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
    assert is_login_blocked(db, "admin", "127.0.0.1") is True


def test_ip_level_throttle_cross_username(client, db):
    for uname in ["b1", "b2", "b3", "b4", "b5", "b6"]:
        client.post("/api/auth/login", json={"username": uname, "password": "bad"})
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"})
    assert r.status_code == 429


def test_login_attempts_pruned(db):
    db.execute("INSERT INTO login_attempts (username, success) VALUES ('old', 0)")
    db.execute("UPDATE login_attempts SET attempted_at = datetime('now', '-2 days')")
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
    assert check_api_key_rate_limit(db, key_id, max_hits=3, window_seconds=60) is True
    # Old hits are pruned on write.
    db.execute("UPDATE api_key_rate_limits SET hit_at = datetime('now', '-2 days')")
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
