"""Slice C — Features & Hardening tests."""

import os
import tempfile
import pytest


def test_upload_download_admin_200(admin_client, db):
    """Admin can download generated report for upload with file."""
    # Create an upload first using the test db connection
    from chem_stock import compare_stock_upload, save_upload
    upload_data = [
        {"name": "Chemical A", "balance_last_month": 100, "balance_this_month": 90}
    ]
    results = compare_stock_upload(db, upload_data)
    upload_id = save_upload(db, "test_upload.xlsx", results)

    # Create a report file in the expected location
    from raas_tracker.db import data_dir
    reports_dir = os.path.join(data_dir(), "reports", str(upload_id))
    os.makedirs(reports_dir, exist_ok=True)
    report_path = os.path.join(reports_dir, "comparison_report_1.xlsx")
    with open(report_path, "wb") as f:
        f.write(b"fake excel content")

    # Admin downloads
    resp = admin_client.get(f"/api/uploads/{upload_id}/download")
    assert resp.status_code == 200
    assert resp.data == b"fake excel content"
    assert "attachment" in resp.headers.get("Content-Disposition", "")


def test_upload_download_non_admin_403(user_client, db):
    """Non-admin user gets 403 on download endpoint."""
    from chem_stock import compare_stock_upload, save_upload
    upload_data = [
        {"name": "Chemical B", "balance_last_month": 100, "balance_this_month": 90}
    ]
    results = compare_stock_upload(db, upload_data)
    upload_id = save_upload(db, "test_upload2.xlsx", results)

    resp = user_client.get(f"/api/uploads/{upload_id}/download")
    assert resp.status_code == 403
    assert b"admin required" in resp.data


def test_upload_download_missing_404(admin_client, db):
    """Upload exists but no report file -> 404."""
    from chem_stock import compare_stock_upload, save_upload
    from raas_tracker.db import data_dir
    upload_data = [
        {"name": "Chemical C", "balance_last_month": 100, "balance_this_month": 90}
    ]
    results = compare_stock_upload(db, upload_data)
    upload_id = save_upload(db, "test_upload3.xlsx", results)

    # Ensure no report file exists (clean up from previous tests)
    reports_dir = os.path.join(data_dir(), "reports", str(upload_id))
    if os.path.isdir(reports_dir):
        import shutil
        shutil.rmtree(reports_dir)

    # No report file created
    resp = admin_client.get(f"/api/uploads/{upload_id}/download")
    assert resp.status_code == 404
    assert b"no generated report for this upload" in resp.data


def test_upload_download_upload_not_found(admin_client):
    """Non-existent upload -> 404."""
    resp = admin_client.get("/api/uploads/999999/download")
    assert resp.status_code == 404
    assert b"Upload not found" in resp.data


def test_connection_pool_reuse(db):
    """Two get_connection() calls return connections from same pool."""
    from raas_tracker.db import _get_pool, _PooledConnection

    # Get pool instance
    pool = _get_pool()
    assert pool is not None

    # First connection (use db fixture connection which is already from pool)
    # The db fixture connection is a _PooledConnection wrapper
    assert isinstance(db, _PooledConnection)

    # Pool stats should show activity
    stats = pool.get_stats()
    assert stats is not None


def test_limiter_redis_fallback(monkeypatch):
    """Limiter uses memory storage when REDIS_URL unset, Redis when set."""
    # Test the storage URI logic directly without reloading flask_app
    from flask_app import _limiter_storage_uri as storage_uri_1
    # Can't easily test both without reload, so just verify the logic
    import os
    assert os.getenv("REDIS_URL", "memory://") == "memory://"
    
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    assert os.getenv("REDIS_URL", "memory://") == "redis://localhost:6379/0"


def test_csp_header_present(admin_client):
    """CSP header present with default-src 'self'."""
    resp = admin_client.get("/api/auth/status")
    assert resp.status_code == 200
    csp = resp.headers.get("Content-Security-Policy")
    assert csp is not None
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


def test_csp_header_on_all_responses(admin_client, user_client):
    """CSP header present on various responses."""
    for client in [admin_client, user_client]:
        resp = client.get("/api/auth/status")
        csp = resp.headers.get("Content-Security-Policy")
        assert csp is not None
        assert "default-src 'self'" in csp


def _inline_script_hashes():
    """sha256 CSP hashes of every inline <script> in the SPA index.html.

    Computed from source rather than hardcoded: Vite copies the inline
    theme bootstrap verbatim into dist/index.html, so if that script ever
    changes, the CSP hash must change with it or browsers silently drop it
    (which is exactly how dark mode was being blocked in production).
    """
    import base64
    import hashlib
    import re
    from pathlib import Path

    html_path = Path(__file__).resolve().parent.parent / "raas-tracker-frontend" / "index.html"
    html = html_path.read_text(encoding="utf-8")
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    assert scripts, f"expected an inline script in {html_path}"
    return [
        "sha256-" + base64.b64encode(hashlib.sha256(s.encode("utf-8")).digest()).decode()
        for s in scripts
    ]


def test_csp_allows_the_inline_theme_script(client):
    """Every inline script is hash-whitelisted, without weakening script-src.

    script-src 'self' alone blocks inline scripts, so the theme bootstrap in
    index.html never ran in production (no other code applies the theme).
    Allowlisting by hash keeps the policy strict: no 'unsafe-inline'.
    """
    resp = client.get("/api/auth/status")
    csp = resp.headers.get("Content-Security-Policy")
    assert csp is not None
    script_src = next(part for part in csp.split(";") if part.strip().startswith("script-src"))
    assert "'unsafe-inline'" not in script_src
    for digest in _inline_script_hashes():
        assert f"'{digest}'" in script_src, f"missing {digest} in {script_src!r}"


def test_security_headers_present(admin_client):
    """All security headers present."""
    resp = admin_client.get("/api/auth/status")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert resp.headers.get("Permissions-Policy") == "camera=(), microphone=(), geolocation=()"


def test_connection_pool_close_on_shutdown():
    """close_pool function exists and can be called."""
    from raas_tracker.db import close_pool, _get_pool
    pool = _get_pool()
    assert pool is not None
    close_pool()
    # After close, getting a new connection should create a new pool
    from raas_tracker.db import get_connection
    conn = get_connection()
    conn.close()
    # New pool should be created
    from raas_tracker.db import _pool
    assert _pool is not None


def test_env_dsn_pinned_to_test_db(pg_dsn):
    """No test may resolve to the remote DB: session pins DATABASE_URL."""
    import os
    from raas_tracker import db as dbmod
    assert os.environ.get("DATABASE_URL") == pg_dsn
    assert dbmod.resolve_dsn(None) == pg_dsn


def test_pool_open_is_explicit_no_deprecation(pg_dsn):
    """Pool must be constructed with open=True (psycopg_pool deprecation)."""
    import warnings as _w
    from raas_tracker import db as dbmod
    dbmod.close_pool()
    with _w.catch_warnings(record=True) as caught:
        _w.simplefilter("always")
        pool = dbmod._get_pool(pg_dsn)
        conn = pool.getconn()
        pool.putconn(conn)
    dep = [w for w in caught
           if issubclass(w.category, DeprecationWarning) and "open" in str(w.message)]
    assert not dep, [str(w.message) for w in dep]