"""Password reset/change system (M10): change, forgot, redeem, admin reset, gate."""
import time

import pytest


def _uid(db, username):
    return db.execute("SELECT id FROM users WHERE username = %s", (username,)).fetchone()[0]


def _audit_count(db, action, entity_id):
    return db.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE action = %s AND entity_id = %s",
        (action, entity_id),
    ).fetchone()[0]


# ==================== PUT /api/auth/password ====================

def test_change_401_wrong_current(user_client):
    r = user_client.put("/api/auth/password",
                        json={"current_password": "not-the-password", "new_password": "brand-new-pass-1"})
    assert r.status_code == 401


def test_change_400_weak_new(user_client):
    r = user_client.put("/api/auth/password",
                        json={"current_password": "user-pass-123", "new_password": "short"})
    assert r.status_code == 400


def test_change_200_success_returns_fresh_user(user_client):
    r = user_client.put("/api/auth/password",
                        json={"current_password": "user-pass-123", "new_password": "brand-new-pass-1"})
    assert r.status_code == 200
    user = r.get_json()["user"]
    assert user["username"] == "user"
    assert user["must_change_password"] is False


def test_change_old_password_dead_new_works(user_client, client):
    assert user_client.put("/api/auth/password",
                           json={"current_password": "user-pass-123",
                                 "new_password": "brand-new-pass-1"}).status_code == 200
    assert client.post("/api/auth/login",
                       json={"username": "user", "password": "user-pass-123"}).status_code == 401
    assert client.post("/api/auth/login",
                       json={"username": "user", "password": "brand-new-pass-1"}).status_code == 200


def test_change_revokes_other_sessions_keeps_current(user_client, db):
    import flask_app
    other = flask_app.app.test_client()
    assert other.post("/api/auth/login",
                      json={"username": "user", "password": "user-pass-123"}).status_code == 200
    assert other.get("/api/chemicals").status_code == 200
    assert user_client.put("/api/auth/password",
                           json={"current_password": "user-pass-123",
                                 "new_password": "brand-new-pass-1"}).status_code == 200
    # Changer's own session survives.
    assert user_client.get("/api/chemicals").status_code == 200
    # The second session is dead.
    assert other.get("/api/chemicals").status_code == 401


def test_change_audit_logged(user_client, db):
    uid = _uid(db, "user")
    assert _audit_count(db, "PASSWORD_CHANGED", uid) == 0
    assert user_client.put("/api/auth/password",
                           json={"current_password": "user-pass-123",
                                 "new_password": "brand-new-pass-1"}).status_code == 200
    assert _audit_count(db, "PASSWORD_CHANGED", uid) == 1


# ==================== POST /api/auth/forgot-password ====================

def test_forgot_known_200_generic_shape(client, db):
    r = client.post("/api/auth/forgot-password", json={"username": "user"})
    assert r.status_code == 200
    body = r.get_json()
    assert set(body.keys()) == {"message"}
    assert "exists" in body["message"]
    uid = _uid(db, "user")
    row = db.execute("SELECT reset_token_hash FROM users WHERE id = %s", (uid,)).fetchone()
    assert row[0]  # token stored (hash only)
    assert _audit_count(db, "PASSWORD_RESET_REQUESTED", uid) == 1


def test_forgot_unknown_200_identical_shape(client, db):
    known = client.post("/api/auth/forgot-password", json={"username": "user"}).get_json()
    unknown = client.post("/api/auth/forgot-password", json={"username": "ghost-nobody"}).get_json()
    assert unknown == known
    assert set(unknown.keys()) == {"message"}


def test_forgot_no_token_for_unknown(client, db):
    n_before = db.execute("SELECT COUNT(*) FROM users WHERE reset_token_hash IS NOT NULL").fetchone()[0]
    assert client.post("/api/auth/forgot-password", json={"username": "ghost-nobody"}).status_code == 200
    n_after = db.execute("SELECT COUNT(*) FROM users WHERE reset_token_hash IS NOT NULL").fetchone()[0]
    assert n_after == n_before


def test_forgot_delay_floor_both_paths(client):
    t0 = time.monotonic()
    client.post("/api/auth/forgot-password", json={"username": "user"})
    known_dt = time.monotonic() - t0
    t0 = time.monotonic()
    client.post("/api/auth/forgot-password", json={"username": "ghost-nobody"})
    unknown_dt = time.monotonic() - t0
    assert known_dt >= 0.15
    assert unknown_dt >= 0.15


def test_forgot_rate_limit_5_per_minute(client, monkeypatch):
    monkeypatch.delenv("RAAS_RATE_LIMITS", raising=False)
    codes = [client.post("/api/auth/forgot-password", json={"username": "user"}).status_code
             for _ in range(6)]
    assert codes[:5] == [200] * 5
    assert codes[5] == 429


# ==================== POST /api/auth/reset-password ====================

def test_redeem_400_bad_token(client):
    r = client.post("/api/auth/reset-password",
                    json={"token": "bogus-token", "new_password": "brand-new-pass-1"})
    assert r.status_code == 400
    assert r.get_json() == {"error": "invalid or expired reset token"}


def test_redeem_400_expired_token(client, db):
    from raas_tracker.auth import issue_reset_token
    uid = _uid(db, "user")
    raw = issue_reset_token(db, uid, ttl_min=-5)
    r = client.post("/api/auth/reset-password",
                    json={"token": raw, "new_password": "brand-new-pass-1"})
    assert r.status_code == 400
    assert r.get_json() == {"error": "invalid or expired reset token"}


def test_redeem_400_double_use(client, db):
    from raas_tracker.auth import issue_reset_token
    uid = _uid(db, "user")
    raw = issue_reset_token(db, uid)
    assert client.post("/api/auth/reset-password",
                       json={"token": raw, "new_password": "brand-new-pass-1"}).status_code == 200
    r = client.post("/api/auth/reset-password",
                    json={"token": raw, "new_password": "another-new-pass-2"})
    assert r.status_code == 400


def test_redeem_200_clears_flag_revokes_sessions_audits(client, db):
    import flask_app
    from raas_tracker.auth import issue_reset_token
    uid = _uid(db, "user")
    victim = flask_app.app.test_client()
    assert victim.post("/api/auth/login",
                       json={"username": "user", "password": "user-pass-123"}).status_code == 200
    raw = issue_reset_token(db, uid)
    r = client.post("/api/auth/reset-password",
                    json={"token": raw, "new_password": "brand-new-pass-1"})
    assert r.status_code == 200
    row = db.execute("SELECT must_change_password, reset_token_hash FROM users WHERE id = %s",
                     (uid,)).fetchone()
    assert row[0] == 0
    assert row[1] is None
    assert victim.get("/api/chemicals").status_code == 401
    assert _audit_count(db, "PASSWORD_RESET_SUCCESS", uid) == 1
    assert _audit_count(db, "PASSWORD_CHANGED", uid) == 1
    assert client.post("/api/auth/login",
                       json={"username": "user", "password": "brand-new-pass-1"}).status_code == 200


def test_reset_rate_limit_10_per_minute(client, monkeypatch):
    monkeypatch.delenv("RAAS_RATE_LIMITS", raising=False)
    codes = [client.post("/api/auth/reset-password",
                         json={"token": "bogus", "new_password": "brand-new-pass-1"}).status_code
             for _ in range(11)]
    assert codes[:10] == [400] * 10
    assert codes[10] == 429


# ==================== Admin: set password + issue token ====================

def test_admin_set_401_logged_out(client, db):
    uid = _uid(db, "user")
    assert client.post(f"/api/users/{uid}/password", json={}).status_code == 401


def test_admin_set_403_non_admin(user_client, db):
    uid = _uid(db, "user")
    assert user_client.post(f"/api/users/{uid}/password", json={}).status_code == 403


def test_admin_set_200_temp_once_flag_sessions_revoked(admin_client, db):
    import flask_app
    uid = _uid(db, "user")
    victim = flask_app.app.test_client()
    assert victim.post("/api/auth/login",
                       json={"username": "user", "password": "user-pass-123"}).status_code == 200
    r = admin_client.post(f"/api/users/{uid}/password", json={})
    assert r.status_code == 200
    temp = r.get_json()["temp_password"]
    assert temp and isinstance(temp, str)
    # Hash stored, never the raw secret.
    stored = db.execute("SELECT password_hash, must_change_password FROM users WHERE id = %s",
                        (uid,)).fetchone()
    assert stored[0] != temp
    assert stored[1] == 1
    # Target sessions revoked, temp works.
    assert victim.get("/api/chemicals").status_code == 401
    assert _audit_count(db, "ADMIN_FORCE_PASSWORD_RESET", uid) == 1
    assert _audit_count(db, "PASSWORD_CHANGED", uid) == 1
    import flask_app as _fa
    fresh = _fa.app.test_client()
    assert fresh.post("/api/auth/login",
                      json={"username": "user", "password": temp}).status_code == 200


def test_admin_set_explicit_temp(admin_client, db):
    uid = _uid(db, "user")
    r = admin_client.post(f"/api/users/{uid}/password", json={"temp_password": "explicit-temp-1"})
    assert r.status_code == 200
    assert r.get_json()["temp_password"] == "explicit-temp-1"


def test_admin_reset_token_200_once_and_flag(admin_client, db):
    uid = _uid(db, "user")
    r = admin_client.post(f"/api/users/{uid}/reset-token")
    assert r.status_code == 200
    body = r.get_json()
    assert body["token"] and body["link"].endswith(body["token"])
    row = db.execute("SELECT reset_token_hash, must_change_password FROM users WHERE id = %s",
                     (uid,)).fetchone()
    assert row[0]  # hash stored, raw shown once
    assert row[0] != body["token"]
    assert row[1] == 1


def test_admin_password_404_unknown_user(admin_client):
    assert admin_client.post("/api/users/99999/password", json={}).status_code == 404
    assert admin_client.post("/api/users/99999/reset-token").status_code == 404


# ==================== must_change gate ====================

def test_gate_403_on_flag_with_allowlist(admin_client, db):
    import flask_app as _fa
    uid = _uid(db, "user")
    temp = _login_temp(admin_client, db, uid)
    victim = _fa.app.test_client()
    assert victim.post("/api/auth/login",
                       json={"username": "user", "password": temp}).status_code == 200
    assert victim.get("/api/chemicals").status_code == 403
    assert victim.get("/api/chemicals").get_json() == {"error": "password change required"}
    assert victim.get("/api/auth/me").status_code == 200
    assert victim.post("/api/auth/logout").status_code == 200


def _login_temp(admin_client, db, uid):
    """Issue a fresh temp password and return it (helper for the gate test)."""
    r = admin_client.post(f"/api/users/{uid}/password", json={"temp_password": "gate-temp-pass-1"})
    assert r.status_code == 200
    return r.get_json()["temp_password"]


def test_gate_change_clears_flag(admin_client, db):
    import flask_app as _fa
    uid = _uid(db, "user")
    temp = _login_temp(admin_client, db, uid)
    victim = _fa.app.test_client()
    assert victim.post("/api/auth/login",
                       json={"username": "user", "password": temp}).status_code == 200
    assert victim.get("/api/chemicals").status_code == 403
    r = victim.put("/api/auth/password",
                   json={"current_password": temp, "new_password": "final-pass-word-1"})
    assert r.status_code == 200
    assert r.get_json()["user"]["must_change_password"] is False
    assert victim.get("/api/chemicals").status_code == 200


def test_gate_api_key_exempt(admin_client, db):
    import flask_app as _fa
    from chem_stock import create_api_key
    uid = _uid(db, "admin")
    raw = create_api_key(db, "gate-key", created_by=uid)
    db.execute("UPDATE users SET must_change_password = 1 WHERE id = %s", (uid,))
    db.commit()
    # Human session blocked...
    assert admin_client.get("/api/chemicals").status_code == 403
    # ...API key exempt (fresh client carries no session cookie).
    key_client = _fa.app.test_client()
    assert key_client.get("/api/chemicals", headers={"X-API-Key": raw}).status_code == 200


# ==================== Login / me / users surfacing ====================

def test_login_and_me_surface_flags(client, db):
    from raas_tracker.auth import issue_reset_token
    uid = _uid(db, "user")
    issue_reset_token(db, uid)
    db.execute("UPDATE users SET must_change_password = 1 WHERE id = %s", (uid,))
    db.commit()
    r = client.post("/api/auth/login", json={"username": "user", "password": "user-pass-123"})
    assert r.status_code == 200
    assert r.get_json()["user"]["must_change_password"] is True
    me = client.get("/api/auth/me").get_json()
    assert me["must_change_password"] is True
    assert me["has_pending_reset"] is True


def test_users_list_surfaces_flags(admin_client, db):
    from raas_tracker.auth import issue_reset_token
    uid = _uid(db, "user")
    issue_reset_token(db, uid)
    rows = {u["username"]: u for u in admin_client.get("/api/users").get_json()}
    assert rows["user"]["must_change_password"] is False
    assert rows["user"]["has_pending_reset"] is True
    assert rows["admin"]["has_pending_reset"] is False
