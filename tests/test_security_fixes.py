"""Slice A — Critical Security Fixes (P0) tests.

Three independent fixes:
- A1: _enforce_https returns proper 301 redirect with Location header
- A2: @admin_required on upload approve/apply endpoints
- A3: Raw reset tokens not logged (only hash prefix)
"""
import os
import pytest


# ==================== A1: _enforce_https tests ====================

def test_enforce_https_redirect_http(client, monkeypatch):
    """FORCE_HTTPS=1, request with X-Forwarded-Proto: http → 301 + Location: https://..."""
    monkeypatch.setenv("FORCE_HTTPS", "1")
    # Use a public endpoint to avoid auth gate
    r = client.get("/api/auth/status", headers={"X-Forwarded-Proto": "http"})
    assert r.status_code == 301, f"Expected 301, got {r.status_code}: {r.get_json()}"
    loc = r.headers.get("Location")
    assert loc is not None, "Missing Location header on 301 redirect"
    assert loc.startswith("https://"), f"Location should be https://, got {loc}"
    # Should point to same path
    assert "/api/auth/status" in loc


def test_enforce_https_no_redirect_https(client, monkeypatch):
    """Request with X-Forwarded-Proto: https → no redirect (handler proceeds)."""
    monkeypatch.setenv("FORCE_HTTPS", "1")
    r = client.get("/api/auth/status", headers={"X-Forwarded-Proto": "https"})
    # auth/status is a public endpoint, should return 200
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.get_json()}"
    assert r.headers.get("Location") is None, "Should not redirect on https"


def test_enforce_https_localhost_exempt(client, monkeypatch):
    """remote_addr=127.0.0.1, no proto → no redirect."""
    monkeypatch.setenv("FORCE_HTTPS", "1")
    # Flask test client defaults to 127.0.0.1 for remote_addr
    r = client.get("/api/auth/status")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.get_json()}"
    assert r.headers.get("Location") is None, "Localhost should be exempt from HTTPS redirect"


# ==================== A2: @admin_required on upload approve/apply ====================

def _create_mock_upload(admin_client, db):
    """Create a mock upload by directly inserting into uploads table.
    Returns upload_id."""
    from chem_stock import save_upload
    results = {
        "stats": {"total": 1, "matched": 1, "last_month_mismatches": 0,
                  "this_month_mismatches": 0, "both_mismatches": 0,
                  "not_in_db": 0, "not_in_upload": 0, "match_percentage": 100.0},
        "matches": [], "last_month_mismatches": [], "this_month_mismatches": [],
        "both_mismatches": [], "not_in_db": [], "not_in_upload": []
    }
    upload_id = save_upload(db, "test.xlsx", results)
    db.commit()
    return upload_id


def test_upload_approve_non_admin_403(user_client, admin_client, db):
    """POST /api/uploads/<id>/approve — non-admin gets 403."""
    upload_id = _create_mock_upload(admin_client, db)
    r = user_client.post(f"/api/uploads/{upload_id}/approve")
    assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.get_json()}"
    assert r.get_json().get("error") == "admin required"


def test_upload_apply_non_admin_403(user_client, admin_client, db):
    """POST /api/uploads/<id>/apply — non-admin gets 403."""
    upload_id = _create_mock_upload(admin_client, db)
    # First approve as admin so apply can proceed
    r = admin_client.post(f"/api/uploads/{upload_id}/approve")
    assert r.status_code == 200
    # Now try apply as non-admin
    r = user_client.post(f"/api/uploads/{upload_id}/apply")
    assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.get_json()}"
    assert r.get_json().get("error") == "admin required"


def test_upload_approve_admin_200(admin_client, db):
    """POST /api/uploads/<id>/approve — admin gets 200 (mock upload exists)."""
    upload_id = _create_mock_upload(admin_client, db)
    r = admin_client.post(f"/api/uploads/{upload_id}/approve")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.get_json()}"
    assert r.get_json().get("approved") is True


def test_upload_apply_admin_200(admin_client, db):
    """POST /api/uploads/<id>/apply — admin gets 200 (mock upload approved)."""
    upload_id = _create_mock_upload(admin_client, db)
    # Approve first
    r = admin_client.post(f"/api/uploads/{upload_id}/approve")
    assert r.status_code == 200
    # Then apply
    r = admin_client.post(f"/api/uploads/{upload_id}/apply")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.get_json()}"
    assert r.get_json().get("adjusted") is True


# ==================== A3: Reset token not logged raw ====================

def test_forgot_password_token_not_logged_raw(admin_client, caplog):
    """Capture log output; assert raw token NOT present; assert user_id + 8-char hash prefix present."""
    import logging
    caplog.set_level(logging.INFO, logger="flask_app")
    
    r = admin_client.post("/api/auth/forgot-password", json={"username": "admin"})
    assert r.status_code == 200
    
    # Check logs
    log_output = caplog.text
    # Should NOT contain raw token in the reset link format
    assert "link=/reset?token=" not in log_output, "Raw token found in logs!"
    # Should contain user_id
    assert "user_id=" in log_output, f"user_id not found in logs: {log_output}"
    # Should contain token_hash= with 8-char prefix
    import re
    hash_match = re.search(r"token_hash=([a-f0-9]{8})", log_output)
    assert hash_match, f"8-char token_hash prefix not found in logs: {log_output}"
    # Ensure it's exactly 8 chars
    assert len(hash_match.group(1)) == 8, f"token_hash should be 8 chars, got {len(hash_match.group(1))}"


def test_admin_reset_token_not_logged_raw(admin_client, caplog):
    """Admin reset token endpoint should log hash prefix, not raw token."""
    import logging
    caplog.set_level(logging.INFO, logger="flask_app")
    
    r = admin_client.post("/api/users/2/reset-token")
    assert r.status_code == 200
    
    # Check logs
    log_output = caplog.text
    # Should NOT contain raw token
    assert "link=/reset?token=" not in log_output, "Raw token found in admin reset token logs!"
    # Should contain user_id and token_hash
    assert "user_id=2" in log_output or "user_id=" in log_output, f"user_id not found in logs: {log_output}"
    import re
    hash_match = re.search(r"token_hash=([a-f0-9]{8})", log_output)
    assert hash_match, f"8-char token_hash prefix not found in logs: {log_output}"
    assert len(hash_match.group(1)) == 8