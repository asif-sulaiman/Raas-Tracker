"""RAAS Tracker - Flask API Backend + React CRM Frontend"""

import os
import sys
import io
import hashlib
from flask import Flask, request, jsonify, send_from_directory, g, redirect
from datetime import date, timedelta
from functools import wraps

sys.path.insert(0, os.path.dirname(__file__))

# ---- Environment validation (fail early in production) ----
IS_PRODUCTION = os.getenv("FLASK_ENV") == "production" or os.getenv("PRODUCTION") == "1"
_secret = os.getenv("RAAS_SECRET")
if IS_PRODUCTION and not _secret:
    print("FATAL: RAAS_SECRET must be set in production. Generate with:", file=sys.stderr)
    print("  python -c \"import secrets; print(secrets.token_urlsafe(64))\"", file=sys.stderr)
    sys.exit(1)
if not _secret:
    import secrets as _sec
    _secret = _sec.token_urlsafe(64)
    print("WARNING: RAAS_SECRET not set. Using random key (sessions reset on restart).",
          file=sys.stderr)
    print("  Set RAAS_SECRET env var for production.", file=sys.stderr)

app = Flask(__name__)
app.secret_key = _secret
# Hard cap on request bodies (client-side 50MB check is bypassable)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
REACT_BUILD_DIR = os.path.join(os.path.dirname(__file__), "react_frontend")

# ProxyFix for correct remote_addr behind proxy (e.g., nginx, Cloudflare)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

from chem_stock import (
    get_connection, get_all_chemicals, update_stock, add_chemical, set_reorder_level,
    add_recipe, get_recipe_by_name, add_recipe_item, list_recipes, find_recipe,
    list_recipe_items, update_recipe, update_recipe_item, delete_recipe_item,
    delete_recipe, generate_report, generate_multi_recipe_report, export_report_to_csv,
    compare_stock_upload, save_upload, get_upload_history, get_upload_results,
    get_unit_conversion, convert_quantity, get_all_unit_conversions, add_unit_conversion,
    validate_expiry_date, get_all_reason_codes, get_pending_approvals, get_audit_logs,
    add_sale, get_all_sales, get_sale_by_id, move_sale_to_stage, advance_sale,
    update_sale_lc, update_sale_payment, add_sale_item, update_sale_item,
    delete_sale_item, delete_sale, get_sales_summary, record_sale_payment,
    update_sale_payment_record, delete_sale_payment_record, update_sale_full,
    get_stock_movements, list_companies, create_company, update_company,
    delete_company, get_company, add_shipment, list_shipments, delete_shipment,
    create_production_run, get_register_products, get_commercial_report,
    get_commercial_report_summary
)

# ---- AuthN/Z: sessions (humans) OR api_keys (scripts) ----
# The legacy RAAS_TOKEN env gate is retired: per-key api_keys provide
# expiry, revocation, IP allowlists, and audit identity instead.
from flask import g
from chem_stock import (get_session_user, validate_api_key,
                        SESSION_TTL_HOURS, count_users, create_user, verify_user,
                        list_users, delete_user, create_session, revoke_session,
                        revoke_user_sessions, cleanup_expired_sessions,
                        record_login_attempt, is_login_blocked, create_api_key,
                        list_api_keys, revoke_api_key, set_audit_actor,
                        create_first_admin, ensure_setup_token, check_setup_token,
                        log_audit_action, check_api_key_rate_limit, record_api_key_hit,
                        set_password, issue_reset_token, redeem_reset_token)
from raas_tracker.notifications import (
    list_notifications_for, unread_count, mark_read, mark_read_all_for,
    notify_reorder_status, notify_maturity_initial, notify_maturity_escalation
)

SESSION_COOKIE = "raas_session"
API_KEY_HEADER = "X-API-Key"
KEY_RATE_LIMIT = 300          # API key requests per minute (DB-backed)
KEY_RATE_WINDOW = 60

# Public API endpoints (no identity required).
_PUBLIC_API = {
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/setup"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/auth/status"),
    ("POST", "/api/cron/maturity-check"),
    ("POST", "/api/auth/forgot-password"),
    ("POST", "/api/auth/reset-password"),
}

# Human-admin-only paths. API keys never pass these (scripts can't manage users).
_ADMIN_PATHS = ("/api/users", "/api/keys")


def _require_admin():
    """403 unless the caller is a human admin. Returns None when allowed."""
    ident = getattr(g, "current_identity", None) or {}
    if ident.get("type") != "human" or ident.get("role") != "admin":
        return jsonify({"error": "admin required"}), 403
    return None


def _key_rate_ok(key_id: int) -> bool:
    """DB-backed per-key throttle (survives restarts, works across processes)."""
    conn = get_db()
    try:
        if check_api_key_rate_limit(conn, key_id, KEY_RATE_LIMIT, KEY_RATE_WINDOW):
            return False
        record_api_key_hit(conn, key_id)
        return True
    finally:
        conn.close()


def _resolve_identity():
    """Return the request identity or None. Session cookie first, then API key."""
    conn = get_db()
    try:
        user = get_session_user(conn, request.cookies.get(SESSION_COOKIE))
        if user:
            return {"type": "human", "id": user["id"],
                    "username": user["username"], "role": user["role"],
                    "must_change_password": bool(user.get("must_change_password"))}
        key = validate_api_key(conn, request.headers.get(API_KEY_HEADER),
                               request.remote_addr)
        if key:
            return {"type": "api-key", "id": key["id"], "name": key["name"]}
        return None
    finally:
        conn.close()


def _actor() -> str:
    """Audit actor string: username for humans, api-key:<name> for scripts."""
    ident = getattr(g, "current_identity", None) or {}
    if ident.get("type") == "api-key":
        return f"api-key:{ident.get('name')}"
    return ident.get("username", "system")


@app.before_request
def _gate_api():
    if not request.path.startswith("/api"):
        return None
    if request.method == "OPTIONS":
        # CORS preflight carries no credentials by design; the route's
        # automatic OPTIONS response plus after_request ACA headers
        # complete it for allowed origins.
        return None
    # Audited rejection of retired X-API-Token header — checked before everything.
    if request.headers.get("X-API-Token"):
        try:
            conn = get_db()
            log_audit_action(conn, "LEGACY_TOKEN_USED", request.path,
                             new_value=f"ip={request.remote_addr}")
            conn.close()
        except Exception:
            pass
        return jsonify({
            "error": "X-API-Token is retired — use X-API-Key instead",
            "docs": "POST /api/keys to create a key; pass it as X-API-Key header",
        }), 401
    if (request.method, request.path) in _PUBLIC_API:
        try:
            g.current_identity = _resolve_identity()
        except Exception:
            g.current_identity = None
        return None
    ident = _resolve_identity()
    if not ident:
        return jsonify({"error": "authentication required"}), 401
    if ident["type"] == "api-key":
        if not _key_rate_ok(ident["id"]):
            resp = jsonify({"error": "rate limit exceeded"})
            resp.headers["Retry-After"] = str(KEY_RATE_WINDOW)
            resp.status_code = 429
            return resp
    if any(request.path == p or request.path.startswith(p + "/") for p in _ADMIN_PATHS):
        if ident.get("type") != "human" or ident.get("role") != "admin":
            return jsonify({"error": "admin required"}), 403
    # M10: forced-password-change enforcement. Human sessions flagged
    # must_change_password may only change password, check identity, or log
    # out. API-key identities are exempt (scripts can't change passwords).
    if ident.get("type") == "human" and ident.get("must_change_password"):
        if (request.method, request.path) not in {
            ("PUT", "/api/auth/password"),
            ("GET", "/api/auth/me"),
            ("POST", "/api/auth/logout"),
        }:
            return jsonify({"error": "password change required"}), 403
    g.current_identity = ident
    # U1.7: thread-local audit actor for this request's thread.
    try:
        set_audit_actor(_actor())
    except Exception:
        pass
    return None


# ==================== SECURITY: HEADERS, CORS, HTTPS ====================
def _cors_allowed_origins():
    """Allowed CORS origins from CORS_ALLOWED_ORIGINS (comma-separated).

    Empty by default: the SPA is served same-origin by Flask, so browsers
    never need CORS. Add origins only for separate frontends or key-authed
    cross-origin clients.
    """
    raw = os.getenv("CORS_ALLOWED_ORIGINS", "")
    return {o.strip().rstrip("/") for o in raw.split(",") if o.strip()}


@app.after_request
def _set_security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-XSS-Protection"] = "0"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # Content Security Policy
    csp = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    resp.headers["Content-Security-Policy"] = csp
    if os.getenv("FORCE_HTTPS") == "1":
        resp.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    origin = (request.headers.get("Origin") or "").rstrip("/")
    if origin and origin in _cors_allowed_origins():
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Vary"] = "Origin"
        if request.method == "OPTIONS":
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-API-Key"
            resp.headers["Access-Control-Max-Age"] = "86400"
    return resp


@app.before_request
def _enforce_https():
    if os.getenv("FORCE_HTTPS") == "1":
        # When behind a reverse proxy, X-Forwarded-Proto is the canonical check.
        # If the proxy says HTTP, redirect to HTTPS regardless of remote_addr.
        proto = request.headers.get("X-Forwarded-Proto")
        if proto == "http":
            https_url = request.url.replace("http://", "https://", 1)
            return redirect(https_url, code=301)
        # Without a proxy header, only redirect non-localhost direct requests.
        if proto is None and request.scheme != "https" and request.remote_addr not in ("127.0.0.1", "::1"):
            https_url = request.url.replace("http://", "https://", 1)
            return redirect(https_url, code=301)


@app.errorhandler(404)
def _not_found(_e):
    if request.path.startswith("/api"):
        return jsonify({"error": "not found"}), 404
    # Missing static asset (e.g. /static/*): fail loudly with 404.
    # Serving index.html as JS/CSS leaves the SPA permanently blank.
    if "." in os.path.basename(request.path or ""):
        return jsonify({"error": "not found"}), 404
    return send_from_directory(REACT_BUILD_DIR, "index.html")


# ==================== RATE LIMITS (tiers 2-3) ====================
# Tier 1 (login throttle 5 fails/10 min, API-key 300/min) stays DB-backed.
# These in-process limits are loop/DoS protection: approximate under
# multiple workers, disabled in tests (RAAS_RATE_LIMITS=off).
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address


def _limit_key():
    ident = getattr(g, "current_identity", None) or {}
    if ident.get("type") == "human" and ident.get("id") is not None:
        return f"user:{ident['id']}"
    if ident.get("type") == "api-key" and ident.get("id") is not None:
        return f"key:{ident['id']}"
    return get_remote_address()


# Redis-backed limiter fallback: uses REDIS_URL if set, else in-memory
_limiter_storage_uri = os.getenv("REDIS_URL", "memory://")

limiter = Limiter(
    _limit_key,
    app=app,
    default_limits=["300 per minute"],
    storage_uri=_limiter_storage_uri,
    headers_enabled=True,
)


@limiter.request_filter
def _rate_limit_test_bypass():
    """Exempt every request when tests disable limits via env.

    Evaluated per request (unlike the init-time `enabled` flag, which 4.x
    bakes in at startup and cannot be toggled later).
    """
    return os.getenv("RAAS_RATE_LIMITS", "on") == "off"


@app.errorhandler(429)
def _rate_limit_exceeded(e):
    retry = None
    try:
        retry = dict(e.get_headers()).get("Retry-After")
    except Exception:
        pass
    resp = jsonify({"error": "rate limit exceeded, slow down"})
    resp.status_code = 429
    if retry:
        resp.headers["Retry-After"] = retry
    return resp


@app.errorhandler(500)
def _server_error(_e):
    app.logger.exception("unhandled server error")
    return jsonify({"error": "internal server error"}), 500


@app.errorhandler(Exception)
def _handle_exception(e):
    app.logger.exception("unhandled exception")
    return jsonify({"error": "internal server error"}), 500


def admin_required(f):
    """Decorator: only human admins may call this endpoint."""
    @wraps(f)
    def decorated(*args, **kwargs):
        ident = getattr(g, "current_identity", None) or {}
        if ident.get("type") != "human" or ident.get("role") != "admin":
            return jsonify({"error": "admin required"}), 403
        return f(*args, **kwargs)
    return decorated


def _notify_login_failures(conn, username, ip) -> None:
    """Admin-only security alert when the failed-login threshold is reached."""
    try:
        from raas_tracker.notifications import notify
        who = (username or "").strip() or "unknown"
        from datetime import datetime as _dt, timezone
        bucket = _dt.now(timezone.utc).strftime("%Y%m%d%H")
        notify(conn, type="login_failures",
               title=f"Repeated failed logins: {who}",
               body=f"Failed-login threshold reached for '{who}' from {ip} within 10 minutes.",
               severity="critical", role_scope="admin", entity_type="user",
               dedupe_key=f"logfail:{who}:{ip}:{bucket}")
    except Exception:
        app.logger.exception("login-failure notification failed")


# ==================== API: AUTH ====================
def _set_session_cookie(resp, token):
    resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL_HOURS * 3600,
                    httponly=True, samesite="Lax",
                    secure=os.getenv("COOKIE_SECURE") != "0", path="/")
    return resp


@app.route("/api/auth/status")
def api_auth_status():
    conn = get_db()
    empty = count_users(conn) == 0
    if empty and os.getenv("DISABLE_SETUP") != "true":
        ensure_setup_token(conn)  # generates + prints to console once
    conn.close()
    ident = getattr(g, "current_identity", None) or {}
    user = None
    if ident.get("type") == "human":
        user = {"id": ident["id"], "username": ident["username"], "role": ident["role"]}
    elif ident.get("type") == "api-key":
        user = {"type": "api-key", "name": ident.get("name")}
    return jsonify({"setup_needed": empty, "user": user})


@app.route("/api/auth/setup", methods=["POST"])
def api_auth_setup():
    """One-time first-admin creation.

    First-identity lock: the very first statement rejects when any user
    exists (403, generic body — the route is effectively unbound afterwards).
    Creation itself runs in a single implicit transaction so simultaneous
    submits cannot both succeed. Layers: DISABLE_SETUP kill switch,
    first-identity lock, single-use console token.
    """
    if os.getenv("DISABLE_SETUP") == "true":
        return jsonify({"error": "not found"}), 404
    data = request.get_json() or {}
    presented = request.headers.get("X-Setup-Token") or request.args.get("token")
    conn = get_db()
    if count_users(conn) > 0:
        conn.close()
        return jsonify({"error": "not found"}), 403
    if not check_setup_token(conn, presented):
        conn.close()
        return jsonify({"error": "not found"}), 403
    try:
        # Role is forced: the first account is always an admin.
        uid = create_first_admin(conn, data.get("username", ""), data.get("password", ""))
    except Exception:
        conn.close()
        return jsonify({"error": "not found"}), 403
    # Single-use: burn the token in the same flow.
    conn.execute("DELETE FROM app_settings WHERE key = 'setup_token_hash'")
    conn.commit()
    user = {"id": uid, "username": data.get("username", "").strip(), "role": "admin"}
    token = create_session(conn, uid)
    conn.close()
    return _set_session_cookie(jsonify({"user": user}), token), 201


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    data = request.get_json() or {}
    username = data.get("username", "")
    password = data.get("password", "")
    ip = request.remote_addr
    conn = get_db()
    if is_login_blocked(conn, username, ip):
        conn.close()
        resp = jsonify({"error": "too many failed attempts, try again later"})
        resp.status_code = 429
        resp.headers["Retry-After"] = "600"
        return resp
    user = verify_user(conn, username, password)
    if not user:
        record_login_attempt(conn, username, ip, False)
        if is_login_blocked(conn, username, ip):
            _notify_login_failures(conn, username, ip)
        conn.close()
        return jsonify({"error": "invalid credentials"}), 401
    record_login_attempt(conn, username, ip, True)
    try:
        cleanup_expired_sessions(conn)
    except Exception:
        pass
    token = create_session(conn, user["id"])
    conn.close()
    return _set_session_cookie(jsonify({"user": user}), token)


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    conn = get_db()
    revoke_session(conn, request.cookies.get(SESSION_COOKIE))
    conn.close()
    resp = jsonify({"message": "logged out"})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@app.route("/api/auth/me")
def api_auth_me():
    ident = getattr(g, "current_identity", None) or {}
    if not ident:
        return jsonify({"error": "authentication required"}), 401
    if ident.get("type") == "api-key":
        return jsonify({"type": "api-key", "name": ident.get("name")})
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT must_change_password, reset_token_hash, reset_token_expires_at "
            "FROM users WHERE id = %s", (ident["id"],)).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "authentication required"}), 401
    return jsonify({"id": ident["id"], "username": ident["username"], "role": ident["role"],
                    "must_change_password": bool(row[0]),
                    "has_pending_reset": bool(row[1]) and not _reset_expired(row[2])})


def _reset_expired(expires_at) -> bool:
    """Stored UTC expiry text missing/unparseable/past → treated as expired."""
    from datetime import datetime as _dt, timezone as _tz
    try:
        exp = _dt.strptime((expires_at or "")[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_tz.utc)
    except ValueError:
        return True
    return _dt.now(_tz.utc) >= exp


@app.route("/api/auth/password", methods=["PUT"])
def api_change_password():
    """Voluntary change: verify current password, keep this session, kill others."""
    ident = _human_identity()
    if not ident:
        return jsonify({"error": "authentication required"}), 401
    try:
        payload = PasswordChangeIn.model_validate(request.get_json() or {})
    except ValidationError as e:
        return _validation_error_response(e)
    import hashlib as _hl
    conn = get_db()
    try:
        row = conn.execute("SELECT password_hash FROM users WHERE id = %s",
                           (ident["id"],)).fetchone()
        if not row:
            return jsonify({"error": "authentication required"}), 401
        from chem_stock import _check_password
        if not _check_password(payload.current_password, row[0]):
            return jsonify({"error": "invalid credentials"}), 401
        current_hash = _hl.sha256(
            (request.cookies.get(SESSION_COOKIE) or "").encode("utf-8")).hexdigest()
        try:
            set_password(conn, ident["id"], payload.new_password,
                         must_change=False, except_token_hash=current_hash)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        fresh = conn.execute("SELECT id, username, role, must_change_password FROM users "
                             "WHERE id = %s", (ident["id"],)).fetchone()
    finally:
        conn.close()
    return jsonify({"user": {"id": fresh[0], "username": fresh[1], "role": fresh[2],
                             "must_change_password": bool(fresh[3])}})


@app.route("/api/auth/forgot-password", methods=["POST"])
@limiter.limit("5 per minute")
def api_forgot_password():
    """Public reset request. Generic 200 either way (no account oracle).

    Both paths do one cheap lookup; a ~200ms delay floor masks residual
    timing. The raw token is server-logged (setup-token precedent), never
    returned.
    """
    import time as _time
    try:
        payload = ForgotPasswordIn.model_validate(request.get_json() or {})
    except ValidationError as e:
        return _validation_error_response(e)
    start = _time.monotonic()
    conn = get_db()
    try:
        row = conn.execute("SELECT id, username FROM users WHERE username = %s",
                           (payload.username.strip(),)).fetchone()
        if row:
            raw = issue_reset_token(conn, row[0])
            token_hash = hashlib.sha256(raw.encode()).hexdigest()
            app.logger.info("password reset requested for user_id=%s token_hash=%s",
                            row[0], token_hash[:8])
    finally:
        conn.close()
    remaining = 0.2 - (_time.monotonic() - start)
    if remaining > 0:
        _time.sleep(remaining)
    return jsonify({"message": "if the account exists, a reset link has been issued"})


@app.route("/api/auth/reset-password", methods=["POST"])
@limiter.limit("10 per minute")
def api_reset_password():
    """Public single-use redeem. Success → 200; any token problem → 400."""
    try:
        payload = ResetPasswordIn.model_validate(request.get_json() or {})
    except ValidationError as e:
        return _validation_error_response(e)
    conn = get_db()
    try:
        try:
            redeem_reset_token(conn, payload.token, payload.new_password)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    finally:
        conn.close()
    return jsonify({"message": "password has been reset"})


# ==================== API: USERS (human-admin only) ====================
@app.route("/api/users")
def api_list_users():
    conn = get_db()
    users = list_users(conn)
    conn.close()
    return jsonify(users)


@app.route("/api/users", methods=["POST"])
def api_create_user():
    data = request.get_json() or {}
    conn = get_db()
    try:
        uid = create_user(conn, data.get("username", ""), data.get("password", ""),
                          data.get("role", "user"))
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400
    conn.close()
    return jsonify({"id": uid, "message": "user created"}), 201


@app.route("/api/users/<int:user_id>", methods=["DELETE"])
def api_delete_user(user_id):
    conn = get_db()
    try:
        ok = delete_user(conn, user_id)
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400
    conn.close()
    if not ok:
        return jsonify({"error": "user not found"}), 404
    return jsonify({"message": "user deleted"})


@app.route("/api/users/<int:user_id>/revoke", methods=["POST"])
def api_revoke_user_sessions(user_id):
    """Kill switch: revoke all sessions of a user (API keys untouched — separate switch)."""
    conn = get_db()
    revoke_user_sessions(conn, user_id)
    conn.close()
    return jsonify({"message": "sessions revoked"})


@app.route("/api/users/<int:user_id>/password", methods=["POST"])
@admin_required
def api_admin_set_password(user_id):
    """Admin force-reset: set a temp password, flag must-change, revoke all target sessions.

    The temp secret is returned ONCE (hash stored, never the raw value).
    """
    try:
        payload = AdminSetPasswordIn.model_validate(request.get_json() or {})
    except ValidationError as e:
        return _validation_error_response(e)
    temp = payload.temp_password
    if not temp:
        import secrets as _secrets
        temp = _secrets.token_urlsafe(10)
    conn = get_db()
    try:
        exists = conn.execute("SELECT id, username FROM users WHERE id = %s",
                              (user_id,)).fetchone()
        if not exists:
            return jsonify({"error": "user not found"}), 404
        try:
            set_password(conn, user_id, temp, must_change=True)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        log_audit_action(conn, "ADMIN_FORCE_PASSWORD_RESET", "user", user_id,
                         new_value=exists[1])
        conn.commit()
    finally:
        conn.close()
    return jsonify({"id": user_id, "username": exists[1],
                    "temp_password": temp, "must_change_password": True})


@app.route("/api/users/<int:user_id>/reset-token", methods=["POST"])
@admin_required
def api_admin_reset_token(user_id):
    """Admin-issued reset token: raw token + link returned ONCE, must-change set."""
    conn = get_db()
    try:
        exists = conn.execute("SELECT id FROM users WHERE id = %s", (user_id,)).fetchone()
        if not exists:
            return jsonify({"error": "user not found"}), 404
        raw = issue_reset_token(conn, user_id)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        app.logger.info("admin reset token issued for user_id=%s token_hash=%s",
                        user_id, token_hash[:8])
        conn.execute("UPDATE users SET must_change_password = 1 WHERE id = %s", (user_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"id": user_id, "token": raw, "link": f"/reset?token={raw}"})


# ==================== API: KEYS (human-admin only) ====================
@app.route("/api/keys")
def api_list_keys():
    conn = get_db()
    keys = list_api_keys(conn)
    conn.close()
    return jsonify(keys)


@app.route("/api/keys", methods=["POST"])
def api_create_key():
    data = request.get_json() or {}
    ident = getattr(g, "current_identity", None) or {}
    conn = get_db()
    try:
        raw = create_api_key(conn, data.get("name", ""), created_by=ident.get("id"),
                             expires_at=data.get("expires_at"),
                             allowed_ips=data.get("allowed_ips", ""))
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400
    conn.close()
    return jsonify({"key": raw, "message": "store this key securely — it is shown only once"}), 201


@app.route("/api/keys/<int:key_id>", methods=["DELETE"])
def api_revoke_key(key_id):
    conn = get_db()
    ok = revoke_api_key(conn, key_id)
    conn.close()
    if not ok:
        return jsonify({"error": "key not found"}), 404
    return jsonify({"message": "key revoked"})


@app.errorhandler(413)
def _too_large(_e):
    return jsonify({"error": "file too large (max 50MB)"}), 413


def _req_float(data, field, default=0):
    """V5: coerce numeric input or raise a 400-friendly ValueError."""
    try:
        return float(data.get(field, default))
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a number")


from pydantic import BaseModel, ConfigDict, Field, ValidationError


class _StrippedModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class PasswordChangeIn(_StrippedModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=1)


class ForgotPasswordIn(_StrippedModel):
    username: str = Field(min_length=1)


class ResetPasswordIn(_StrippedModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=1)


class AdminSetPasswordIn(_StrippedModel):
    temp_password: str | None = Field(default=None, min_length=1)



def get_db():
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError(
            "DATABASE_URL is not set. Set it to your PostgreSQL (Supabase) "
            "connection string; see README and .env.example.")
    return get_connection()


# ==================== SERVE REACT CRM ====================
@app.route("/")
def serve_root():
    return send_from_directory(REACT_BUILD_DIR, "index.html")


@app.route("/<path:path>")
def serve_static(path):
    if path and os.path.exists(os.path.join(REACT_BUILD_DIR, path)):
        return send_from_directory(REACT_BUILD_DIR, path)
    # Missing file with an extension (e.g. a stale /assets/*.js bundle):
    # fail loudly with 404. Serving index.html here makes the browser
    # execute HTML as JS (MIME error) and leaves the SPA permanently blank.
    # Returned directly (not via abort) so the 404 error handler — which
    # maps unknown routes to index.html — does not convert it back.
    if "." in os.path.basename(path or ""):
        return jsonify({"error": "not found"}), 404
    return send_from_directory(REACT_BUILD_DIR, "index.html")


# ==================== API: COMPANIES (P0 master) ====================
class CompanyIn(_StrippedModel):
    name: str = Field(min_length=1)
    code: str | None = None
    country: str | None = None
    address: str | None = None
    contact_person: str | None = None
    swift: str | None = None
    lc_bank: str | None = None


class CompanyPatchIn(_StrippedModel):
    name: str | None = Field(default=None, min_length=1)
    code: str | None = None
    country: str | None = None
    address: str | None = None
    contact_person: str | None = None
    swift: str | None = None
    lc_bank: str | None = None


def _clean_company(payload) -> dict:
    data = payload.model_dump()
    return {k: (v.strip() or None) if isinstance(v, str) else v
            for k, v in data.items()}


@app.route("/api/companies")
def api_list_companies():
    conn = get_db()
    rows = list_companies(conn)
    conn.close()
    return jsonify(rows)


@app.route("/api/register/products")
def api_register_products():
    company_id = request.args.get("company_id", type=int)
    if company_id is None:
        return jsonify({"error": "company_id is required"}), 400
    conn = get_db()
    company = get_company(conn, company_id)
    if not company:
        conn.close()
        return jsonify({"error": "unknown company"}), 400
    try:
        rows = get_register_products(conn, company_id)
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400
    conn.close()
    return jsonify(rows)


@app.route("/api/companies", methods=["POST"])
@admin_required
def api_create_company():
    try:
        payload = CompanyIn.model_validate(request.get_json() or {})
    except ValidationError as e:
        return _validation_error_response(e)
    conn = get_db()
    try:
        cid = create_company(conn, **_clean_company(payload))
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400
    conn.close()
    if cid is None:
        return jsonify({"success": False,
                        "error": f"Company '{payload.name}' already exists"}), 409
    return jsonify({"success": True, "id": cid,
                    **_clean_company(payload)}), 201


@app.route("/api/companies/<int:cid>", methods=["PUT"])
@admin_required
def api_update_company(cid):
    try:
        payload = CompanyPatchIn.model_validate(request.get_json() or {})
    except ValidationError as e:
        return _validation_error_response(e)
    fields = {k: v for k, v in _clean_company(payload).items()
              if k in (request.get_json() or {})}
    if not fields:
        return jsonify({"error": "nothing to update"}), 400
    conn = get_db()
    try:
        result = update_company(conn, cid, fields)
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400
    if result is None:
        conn.close()
        return jsonify({"error": "company not found"}), 404
    if result is False:
        conn.close()
        return jsonify({"error": "Company name already exists"}), 409
    row = get_company(conn, cid)
    conn.close()
    return jsonify({"success": True, **row})


@app.route("/api/companies/<int:cid>", methods=["DELETE"])
@admin_required
def api_delete_company(cid):
    conn = get_db()
    result = delete_company(conn, cid)
    conn.close()
    if result == "linked":
        return jsonify({"error": "Company is linked to sales and cannot be deleted. "
                                 "Rename it instead."}), 409
    if not result:
        return jsonify({"error": "company not found"}), 404
    return jsonify({"success": True})


# ==================== API: CHEMICALS ====================
@app.route("/api/chemicals")
def api_chemicals():
    conn = get_db()
    chemicals = get_all_chemicals(conn)
    conn.close()
    return jsonify(chemicals)


class ChemicalCreateIn(_StrippedModel):
    name: str = Field(min_length=1)
    qty: float = Field(default=0, ge=0)
    unit: str = Field(default="KG", min_length=1)
    reorder_level: float = Field(default=0, ge=0)


@app.route("/api/chemicals", methods=["POST"])
def api_add_chemical():
    denied = _require_admin()
    if denied:
        return denied
    try:
        payload = ChemicalCreateIn.model_validate(request.get_json() or {})
    except ValidationError as e:
        return _validation_error_response(e)
    name = payload.name
    unit = payload.unit.strip().upper()
    conn = get_db()
    success = add_chemical(conn, name, payload.qty, unit)
    if not success:
        conn.close()
        return jsonify({"success": False, "name": name,
                        "error": f"Chemical '{name}' already exists"}), 409
    if payload.reorder_level:
        try:
            set_reorder_level(conn, name, payload.reorder_level)
        except ValueError as e:
            conn.close()
            return jsonify({"error": str(e)}), 400
    # New chemicals born at/below their alarm (or at zero with none set)
    # would otherwise stay silent: raise the buy alert explicitly.
    # Deduped against the set_reorder_level call above when one fired.
    row = conn.execute("SELECT id FROM chemicals WHERE name = %s",
                       (name,)).fetchone()
    if row:
        notify_reorder_status(conn, row[0], name, payload.qty,
                              payload.reorder_level)
    conn.close()
    return jsonify({"success": True, "name": name})


@app.route("/api/chemicals/update", methods=["POST"])
def api_update_chemical():
    denied = _require_admin()
    if denied:
        return denied
    data = request.get_json() or {}
    try:
        delta = _req_float(data, "delta", 0)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    name = str(data.get("name", "")).strip()
    reason = (data.get("reason") or "").strip()[:120] or None
    conn = get_db()
    try:
        success = update_stock(conn, name, delta, reason=reason)
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400
    conn.close()
    if not success:
        return jsonify({"success": success, "name": name, "delta": delta}), 404
    return jsonify({"success": success, "name": name, "delta": delta})


@app.route("/api/chemicals/reorder", methods=["PUT"])
def api_set_reorder_level():
    denied = _require_admin()
    if denied:
        return denied
    data = request.get_json() or {}
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        level = float(data.get("reorder_level", 0) or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "reorder_level must be a number"}), 400
    conn = get_db()
    try:
        success = set_reorder_level(conn, name, level)
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400
    conn.close()
    if not success:
        return jsonify({"error": "Chemical not found"}), 404
    return jsonify({"success": True, "name": name, "reorder_level": level})


@app.route("/api/chemicals/history")
@admin_required
def api_chemicals_history():
    chemical_id = request.args.get("chemical_id", type=int)
    since = request.args.get("since")
    until = request.args.get("until")
    limit = request.args.get("limit", 200, type=int)
    conn = get_db()
    rows = get_stock_movements(conn, chemical_id=chemical_id,
                               since=since, until=until, limit=limit)
    conn.close()
    return jsonify(rows)


# ==================== API: RECIPES ====================
@app.route("/api/recipes")
def api_recipes():
    conn = get_db()
    recipes = list_recipes(conn)
    conn.close()
    return jsonify(recipes)


@app.route("/api/recipes/<path:name>")
def api_recipe_detail(name):
    company_id = request.args.get("company_id", type=int)
    if company_id is None:
        return jsonify({"error": "company_id is required"}), 400
    conn = get_db()
    recipe = get_recipe_by_name(conn, company_id, name)
    items = list_recipe_items(conn, company_id, name)
    conn.close()
    if not recipe:
        return jsonify({"error": "Recipe not found"}), 404
    return jsonify({"recipe": recipe, "items": items})


@app.route("/api/recipes", methods=["POST"])
def api_create_recipe():
    data = request.get_json() or {}
    try:
        payload = RecipeCreateIn.model_validate(data)
    except ValidationError as e:
        return _validation_error_response(e)
    conn = get_db()
    try:
        success = add_recipe(conn, payload.name, payload.yield_qty, payload.water_percentage,
                             payload.company_id, payload.product_name)
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400
    if not success:
        # Connection may be in a bad state after IntegrityError rollback;
        # get a fresh connection for the lookup.
        conn.close()
        conn = get_db()
        existing = find_recipe(conn, payload.company_id, payload.name)
        conn.close()
        return jsonify({"success": False, "name": payload.name,
                        "error": f"Recipe '{payload.name}' already exists",
                        "existing": {"name": existing["name"]}}), 409
    conn.close()
    return jsonify({"success": True, "name": payload.name}), 201


class RecipeCreateIn(_StrippedModel):
    name: str = Field(min_length=1)
    yield_qty: float = Field(default=1, gt=0)
    water_percentage: float = Field(default=0, ge=0, le=100)
    company_id: int
    product_name: str = Field(min_length=1)


@app.route("/api/recipes/<path:name>/items", methods=["POST"])
def api_add_recipe_item(name):
    data = request.get_json() or {}
    try:
        pct = _req_float(data, "percentage", 0)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    chem_name = str(data.get("chemical", "")).strip()
    company_id = data.get("company_id")
    if company_id is None:
        return jsonify({"error": "company_id is required"}), 400
    try:
        company_id = int(company_id)
    except (TypeError, ValueError):
        return jsonify({"error": "company_id must be an integer"}), 400
    conn = get_db()
    try:
        success = add_recipe_item(conn, company_id, name, chem_name, pct)
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400
    conn.close()
    if not success:
        return jsonify({"success": success}), 404
    return jsonify({"success": success})


@app.route("/api/recipes/<path:name>/items/<chem>", methods=["DELETE"])
@admin_required
def api_delete_recipe_item(name, chem):
    company_id = request.args.get("company_id", type=int)
    if company_id is None:
        return jsonify({"error": "company_id is required"}), 400
    conn = get_db()
    success = delete_recipe_item(conn, company_id, name, chem)
    conn.close()
    if not success:
        return jsonify({"success": success}), 404
    return jsonify({"success": success})


@app.route("/api/recipes/<path:name>", methods=["DELETE"])
@admin_required
def api_delete_recipe(name):
    company_id = request.args.get("company_id", type=int)
    if company_id is None:
        return jsonify({"error": "company_id is required"}), 400
    conn = get_db()
    success = delete_recipe(conn, company_id, name)
    conn.close()
    if not success:
        return jsonify({"success": success}), 404
    return jsonify({"success": success})


@app.route("/api/recipes/<path:name>", methods=["PUT"])
def api_update_recipe(name):
    data = request.get_json() or {}
    total_qty = data.get("total_quantity")
    water_pct = data.get("water_percentage")
    company_id = data.get("company_id")
    if company_id is None:
        return jsonify({"error": "company_id is required"}), 400
    try:
        company_id = int(company_id)
    except (TypeError, ValueError):
        return jsonify({"error": "company_id must be an integer"}), 400
    conn = get_db()
    try:
        success = update_recipe(conn, company_id, name, total_quantity=total_qty, water_percentage=water_pct)
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400
    conn.close()
    if not success:
        return jsonify({"error": "Recipe not found"}), 404
    return jsonify({"success": True})


@app.route("/api/recipes/<path:name>/items/<chem>", methods=["PUT"])
def api_update_recipe_item(name, chem):
    data = request.get_json() or {}
    try:
        pct = _req_float(data, "percentage", 0)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    company_id = data.get("company_id")
    if company_id is None:
        return jsonify({"error": "company_id is required"}), 400
    try:
        company_id = int(company_id)
    except (TypeError, ValueError):
        return jsonify({"error": "company_id must be an integer"}), 400
    conn = get_db()
    try:
        success = update_recipe_item(conn, company_id, name, chem, pct)
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400
    conn.close()
    if not success:
        return jsonify({"error": "Recipe item not found"}), 404
    return jsonify({"success": True})


# ==================== API: UPLOAD & COMPARISON ====================
@app.route("/api/upload", methods=["POST"])
@limiter.limit("15 per minute")
def api_upload():
    if "file" not in request.files:
        return jsonify({"error": "No file selected"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    # V3: never trust the client filename (path traversal). Store under a
    # random name; keep the original only for display/DB records.
    from werkzeug.utils import secure_filename
    import uuid
    ALLOWED_UPLOAD_EXTS = {".pdf", ".xlsx", ".xls"}
    safe_display = secure_filename(file.filename)
    ext = os.path.splitext(safe_display)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTS:
        return jsonify({"error": "Only PDF and Excel files are accepted"}), 400
    from raas_tracker.db import data_dir as _data_dir
    upload_dir = os.path.join(_data_dir(), "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, f"{uuid.uuid4().hex}{ext}")
    file.save(filepath)

    from parse_stock import parse_stock_file
    try:
        upload_data = parse_stock_file(filepath)
    except Exception:
        # V5/V9: parser crashes become 400s, and the stored file is removed
        # so failed uploads leave nothing behind.
        app.logger.exception("stock parse failed")
        try:
            os.remove(filepath)
        except OSError:
            pass
        return jsonify({"error": "Could not parse file"}), 400

    if not upload_data:
        try:
            os.remove(filepath)
        except OSError:
            pass
        return jsonify({"error": "Could not parse file"}), 400

    conn = get_db()
    results = compare_stock_upload(conn, upload_data)
    upload_id = save_upload(conn, file.filename, results)
    conn.close()

    return jsonify({
        "success": True,
        "upload_id": upload_id,
        "filename": file.filename,
        "results": results
    })


@app.route("/api/uploads")
def api_upload_history():
    conn = get_db()
    history = get_upload_history(conn)
    conn.close()
    return jsonify(history)


@app.route("/api/uploads/<int:upload_id>")
def api_upload_detail(upload_id):
    conn = get_db()
    upload = conn.execute("SELECT * FROM uploads WHERE id = %s", (upload_id,)).fetchone()
    rows = get_upload_results(conn, upload_id)
    conn.close()
    if not upload:
        return jsonify({"error": "Upload not found"}), 404
    return jsonify({"upload": upload, "rows": rows})


@app.route("/api/uploads/<int:upload_id>", methods=["DELETE"])
@admin_required
def api_delete_upload(upload_id):
    conn = get_db()
    upload = conn.execute("SELECT id FROM uploads WHERE id = %s", (upload_id,)).fetchone()
    if not upload:
        conn.close()
        return jsonify({"error": "Upload not found"}), 404

    conn.execute("DELETE FROM upload_rows WHERE upload_id = %s", (upload_id,))
    conn.execute("DELETE FROM uploads WHERE id = %s", (upload_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "deleted_id": upload_id})


@app.route("/api/uploads/<int:upload_id>/approve", methods=["POST"])
@admin_required
@limiter.limit("15 per minute")
def api_approve_upload(upload_id):
    from chem_stock import approve_upload, get_unmapped_rows
    conn = get_db()
    exists = conn.execute("SELECT id FROM uploads WHERE id = %s", (upload_id,)).fetchone()
    if not exists:
        conn.close()
        return jsonify({"error": "Upload not found"}), 404
    ok = approve_upload(conn, upload_id, reviewed_by=_actor())
    if not ok:
        unmapped = get_unmapped_rows(conn, upload_id)
        conn.close()
        return jsonify({"error": "Upload has unmapped units - map them first",
                        "unmapped": unmapped}), 400
    conn.close()
    return jsonify({"approved": True})


@app.route("/api/uploads/<int:upload_id>/apply", methods=["POST"])
@admin_required
@limiter.limit("15 per minute")
def api_apply_upload(upload_id):
    from chem_stock import adjust_stock_from_upload
    conn = get_db()
    row = conn.execute("SELECT status FROM uploads WHERE id = %s", (upload_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Upload not found"}), 404
    if row[0] != "approved":
        conn.close()
        return jsonify({"error": "Approve the upload before applying"}), 400
    ok = adjust_stock_from_upload(conn, upload_id, reviewed_by=_actor())
    conn.close()
    if not ok:
        return jsonify({"error": "Could not apply upload"}), 500
    return jsonify({"adjusted": True})


@app.route("/api/uploads/<int:upload_id>/download")
@admin_required
def api_upload_download(upload_id):
    """Download the generated report file for an upload."""
    from raas_tracker.db import data_dir as _data_dir
    conn = get_db()
    upload = conn.execute(
        "SELECT id, filename FROM uploads WHERE id = %s", (upload_id,)
    ).fetchone()
    conn.close()
    if not upload:
        return jsonify({"error": "Upload not found"}), 404

    # Look for generated report files in data/reports/<upload_id>/
    reports_dir = os.path.join(_data_dir(), "reports", str(upload_id))
    if not os.path.isdir(reports_dir):
        return jsonify({"error": "no generated report for this upload"}), 404

    files = [f for f in os.listdir(reports_dir) if os.path.isfile(os.path.join(reports_dir, f))]
    if not files:
        return jsonify({"error": "no generated report for this upload"}), 404

    # Serve the most recent file by mtime
    files.sort(key=lambda f: os.path.getmtime(os.path.join(reports_dir, f)), reverse=True)
    return send_from_directory(reports_dir, files[0], as_attachment=True)


@app.route("/api/uploads/<int:upload_id>/export")
@limiter.limit("15 per minute")
def api_upload_export(upload_id):
    conn = get_db()
    upload = conn.execute("SELECT * FROM uploads WHERE id = %s", (upload_id,)).fetchone()
    if not upload:
        conn.close()
        return jsonify({"error": "Upload not found"}), 404

    rows = get_upload_results(conn, upload_id)
    results = {
        "stats": {
            "total": upload[5], "matched": upload[6],
            "last_month_mismatches": upload[7], "this_month_mismatches": upload[8],
            "both_mismatches": upload[9], "not_in_db": upload[10],
            "not_in_upload": upload[11], "match_percentage": upload[12]
        },
        "matches": [], "last_month_mismatches": [], "this_month_mismatches": [],
        "both_mismatches": [], "not_in_db": [], "not_in_upload": []
    }
    for row in rows:
        if row["matched"]:
            results["matches"].append(row)
        else:
            results["not_in_db"].append(row)

    from raas_tracker.db import data_dir as _data_dir
    output_path = os.path.join(_data_dir(), "reports", f"comparison_report_{upload_id}.xlsx")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    from chem_stock import export_comparison_report
    export_comparison_report(results, output_path)
    conn.close()

    return jsonify({"success": True, "filename": f"comparison_report_{upload_id}.xlsx"})


# ==================== API: REPORTS ====================
@app.route("/api/reports/generate", methods=["POST"])
@limiter.limit("15 per minute")
def api_report_generate():
    data = request.get_json() or {}
    # recipes is now a list of {"company_id": int, "recipe_name": str}
    recipe_selections = data.get("recipes", [])
    try:
        qty = _req_float(data, "qty", 0)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if not recipe_selections:
        return jsonify({"error": "No recipes selected"}), 400

    # Validate each selection has company_id and recipe_name
    for sel in recipe_selections:
        if "company_id" not in sel or "recipe_name" not in sel:
            return jsonify({"error": "Each recipe selection must have company_id and recipe_name"}), 400

    conn = get_db()
    recipes_list = list_recipes(conn)

    if qty == 0:
        for sel in recipe_selections:
            recipe = next((r for r in recipes_list if r["name"] == sel["recipe_name"] and r["company_id"] == sel["company_id"]), None)
            if recipe:
                qty += recipe["total_quantity"]

    # Use the qty from request or fall back to recipe total_quantity for each
    final_selections = []
    for sel in recipe_selections:
        production_qty = qty if qty > 0 else next((r["total_quantity"] for r in recipes_list if r["name"] == sel["recipe_name"] and r["company_id"] == sel["company_id"]), 1)
        final_selections.append({"company_id": sel["company_id"], "recipe_name": sel["recipe_name"], "production_qty": production_qty})
    
    report = generate_multi_recipe_report(conn, final_selections)
    conn.close()

    return jsonify({"report": report, "recipes": recipe_selections, "qty": qty})


@app.route("/api/reports/export", methods=["POST"])
@limiter.limit("15 per minute")
def api_report_export():
    data = request.get_json() or {}
    # recipes is now a list of {"company_id": int, "recipe_name": str}
    recipe_selections = data.get("recipes", [])
    try:
        qty = _req_float(data, "qty", 0)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if not recipe_selections:
        return jsonify({"error": "No recipes selected"}), 400

    # Validate each selection has company_id and recipe_name
    for sel in recipe_selections:
        if "company_id" not in sel or "recipe_name" not in sel:
            return jsonify({"error": "Each recipe selection must have company_id and recipe_name"}), 400

    conn = get_db()
    recipes_list = list_recipes(conn)

    if qty == 0:
        for sel in recipe_selections:
            recipe = next((r for r in recipes_list if r["name"] == sel["recipe_name"] and r["company_id"] == sel["company_id"]), None)
            if recipe:
                qty += recipe["total_quantity"]

    # Use the qty from request or fall back to recipe total_quantity for each
    final_selections = []
    for sel in recipe_selections:
        production_qty = qty if qty > 0 else next((r["total_quantity"] for r in recipes_list if r["name"] == sel["recipe_name"] and r["company_id"] == sel["company_id"]), 1)
        final_selections.append({"company_id": sel["company_id"], "recipe_name": sel["recipe_name"], "production_qty": production_qty})
    
    report = generate_multi_recipe_report(conn, final_selections)
    conn.close()

    if report:
        # V4: recipe names are free-text input — strip path separators/traversal.
        import re as _re
        if len(recipe_selections) > 1:
            safe_name = "Combined"
        else:
            safe_name = _re.sub(r"[^A-Za-z0-9_-]", "_", recipe_selections[0]["recipe_name"])[:50] or "report"
        try:
            qty_int = int(qty)
        except (TypeError, ValueError):
            return jsonify({"error": "qty must be a number"}), 400
        from raas_tracker.db import data_dir as _data_dir
        output_path = os.path.join(_data_dir(), "reports", f"report_{safe_name}_{qty_int}.csv")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        export_report_to_csv(report, ", ".join(s["recipe_name"] for s in recipe_selections), qty, output_path)
        return jsonify({"success": True, "filename": f"report_{safe_name}_{qty_int}.csv"})
    return jsonify({"error": "Could not generate report"}), 500


# ==================== API: LIVE COMMERCIAL REPORT ====================
@app.route("/api/reports/live")
@admin_required
def api_commercial_report():
    conn = get_db()
    rows = get_commercial_report(conn)
    conn.close()
    return jsonify(rows)


@app.route("/api/reports/live/filtered")
@admin_required
def api_commercial_report_filtered():
    """Filtered commercial report with pagination."""
    filters = {
        "date_anchor": request.args.get("date_anchor"),
        "date_from": request.args.get("date_from"),
        "date_to": request.args.get("date_to"),
        "customer_name": request.args.get("customer_name"),
        "product_name": request.args.get("product_name"),
        "company_id": request.args.get("company_id", type=int),
        "stage": request.args.get("stage"),
        "payment_status": request.args.get("payment_status"),
        "page": request.args.get("page", 1, type=int),
        "page_size": request.args.get("page_size", 50, type=int),
    }
    # Remove None values
    filters = {k: v for k, v in filters.items() if v is not None}
    conn = get_db()
    rows = get_commercial_report(conn, filters)
    conn.close()
    return jsonify(rows)


@app.route("/api/reports/live/summary")
@admin_required
def api_commercial_report_summary():
    """Period-aggregated commercial report summary."""
    filters = {
        "date_anchor": request.args.get("date_anchor"),
        "date_from": request.args.get("date_from"),
        "date_to": request.args.get("date_to"),
        "customer_name": request.args.get("customer_name"),
        "product_name": request.args.get("product_name"),
        "company_id": request.args.get("company_id", type=int),
        "stage": request.args.get("stage"),
        "payment_status": request.args.get("payment_status"),
        "group_by": request.args.get("group_by", "month"),
    }
    # Remove None values
    filters = {k: v for k, v in filters.items() if v is not None}
    conn = get_db()
    periods = get_commercial_report_summary(conn, filters)
    conn.close()
    return jsonify({"periods": periods})


@app.route("/api/reports/live/export", methods=["POST"])
@admin_required
@limiter.limit("15 per minute")
def api_commercial_report_export():
    conn = get_db()
    rows = get_commercial_report(conn)
    conn.close()
    if not rows:
        return jsonify({"error": "No data to export"}), 400
    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Customer Name", "PI No", "PI Date", "LC No", "LC Date",
        "Product Name", "Unit", "Quantity", "Unit Price", "Total Price ($)",
        "Invoice Date", "Latest Ship Date", "Actual Ship Date",
        "Maturity Date", "Receive Date", "Received Amount ($)",
        "Due Amount ($)", "Payment Status", "Payment Comment"
    ])
    for r in rows:
        writer.writerow([
            r.get("customer_name", ""),
            r.get("pi_number", ""), r.get("pi_date", ""),
            r.get("lc_number", ""), r.get("lc_date", ""),
            r.get("product_name", ""),
            r.get("unit", ""), r.get("quantity", 0),
            r.get("unit_price", 0), r.get("total_price", 0),
            r.get("invoice_date", ""),
            r.get("latest_ship_date", ""), r.get("actual_ship_date", ""),
            r.get("maturity_date", ""),
            r.get("receive_date", ""),
            r.get("received_amount", 0),
            r.get("due_amount", 0),
            r.get("payment_status", ""), r.get("payment_comment", "")
        ])
    from datetime import date as _date
    filename = f"commercial_report_{_date.today().isoformat()}.csv"
    output.seek(0)
    return jsonify({"success": True, "filename": filename, "content": output.getvalue()})


# ==================== API: AUDIT LOGS ====================
@app.route("/api/audit-logs")
@admin_required
def api_audit_logs():
    limit = request.args.get("limit", 100, type=int)
    conn = get_db()
    logs = get_audit_logs(conn, limit=limit)
    conn.close()
    return jsonify(logs)


# ==================== API: NOTIFICATIONS ====================
def _human_identity():
    ident = getattr(g, "current_identity", None) or {}
    if ident.get("type") != "human":
        return None
    return ident


@app.route("/api/notifications")
def api_notifications():
    ident = _human_identity()
    if not ident:
        return jsonify({"error": "authentication required"}), 401
    limit = request.args.get("limit", 50, type=int)
    conn = get_db()
    try:
        items = list_notifications_for(conn, ident["id"], ident["role"], limit=limit)
        unread = unread_count(conn, ident["id"], ident["role"])
    finally:
        conn.close()
    return jsonify({"items": items, "unread": unread})


@app.route("/api/notifications/read", methods=["POST"])
def api_notifications_read():
    ident = _human_identity()
    if not ident:
        return jsonify({"error": "authentication required"}), 401
    data = request.get_json() or {}
    ids = data.get("ids")
    if ids is not None and (
        not isinstance(ids, list)
        or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)
    ):
        return jsonify({"error": "ids must be a list of integers"}), 400
    conn = get_db()
    try:
        if ids is None:
            marked = mark_read_all_for(conn, ident["id"], ident["role"])
        else:
            marked = mark_read(conn, ident["id"], ids)
        unread = unread_count(conn, ident["id"], ident["role"])
    finally:
        conn.close()
    return jsonify({"marked": marked, "unread": unread})


# ==================== API: REASON CODES ====================
@app.route("/api/reason-codes")
def api_reason_codes():
    conn = get_db()
    codes = get_all_reason_codes(conn)
    conn.close()
    return jsonify(codes)


# ==================== API: UNIT CONVERSIONS ====================
@app.route("/api/unit-conversions")
def api_unit_conversions():
    conn = get_db()
    conversions = get_all_unit_conversions(conn)
    conn.close()
    return jsonify(conversions)


@app.route("/api/unit-conversions", methods=["POST"])
def api_create_conversion():
    data = request.get_json() or {}
    from_unit = str(data.get("from_unit", "")).strip()
    to_unit = str(data.get("to_unit", "")).strip()
    try:
        factor = float(data.get("factor"))
    except (TypeError, ValueError):
        return jsonify({"error": "factor must be a positive number"}), 400
    if not from_unit or not to_unit:
        return jsonify({"error": "from_unit and to_unit are required"}), 400
    if not (factor > 0 and factor < float("inf")):
        return jsonify({"error": "factor must be a positive number"}), 400
    conn = get_db()
    ok = add_unit_conversion(conn, from_unit, to_unit, factor)
    conn.close()
    if not ok:
        return jsonify({"error": "Could not save conversion"}), 500
    return jsonify({"success": True, "from_unit": from_unit.upper(),
                    "to_unit": to_unit.upper(), "factor": factor})


# ==================== API: PRODUCTION RUNS ====================
class ProductionRunCreateIn(_StrippedModel):
    production_qty: float = Field(gt=0)
    order_number: str | None = None
    batch_number: str | None = None
    production_date: str | None = None
    notes: str | None = None


@app.route("/api/recipes/<path:name>/produce", methods=["POST"])
@admin_required
def api_produce_recipe(name):
    try:
        payload = ProductionRunCreateIn.model_validate(request.get_json() or {})
    except ValidationError as e:
        return _validation_error_response(e)
    data = request.get_json() or {}
    company_id = data.get("company_id")
    if company_id is None:
        return jsonify({"error": "company_id is required"}), 400
    try:
        company_id = int(company_id)
    except (TypeError, ValueError):
        return jsonify({"error": "company_id must be an integer"}), 400
    conn = get_db()
    try:
        result = create_production_run(
            conn, company_id, name, payload.production_qty,
            order_number=payload.order_number,
            batch_number=payload.batch_number,
            production_date=payload.production_date,
            notes=payload.notes,
            created_by=getattr(g, "current_identity", {}).get("id"),
        )
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400
    except Exception:
        conn.close()
        app.logger.exception("Production run failed")
        return jsonify({"error": "internal server error"}), 500
    conn.close()
    if not result:
        return jsonify({"error": "Recipe not found"}), 404
    return jsonify(result), 201


@app.route("/api/recipes/<path:name>/runs")
def api_list_production_runs(name):
    company_id = request.args.get("company_id", type=int)
    if company_id is None:
        return jsonify({"error": "company_id is required"}), 400
    conn = get_db()
    # Verify recipe exists
    recipe = get_recipe_by_name(conn, company_id, name)
    if not recipe:
        conn.close()
        return jsonify({"error": "Recipe not found"}), 404
    runs = conn.execute(
        """SELECT id, order_number, batch_number, production_date, qty_produced, notes, created_at
           FROM production_runs WHERE recipe_id = %s ORDER BY created_at DESC""",
        (recipe["id"],)
    ).fetchall()
    conn.close()
    return jsonify([
        {"id": r[0], "order_number": r[1], "batch_number": r[2],
         "production_date": r[3], "qty_produced": r[3], "notes": r[5], "created_at": r[6]}
        for r in runs
    ])


@app.route("/api/recipes/<path:name>/runs/<int:run_id>")
def api_get_production_run(name, run_id):
    company_id = request.args.get("company_id", type=int)
    if company_id is None:
        return jsonify({"error": "company_id is required"}), 400
    conn = get_db()
    recipe = get_recipe_by_name(conn, company_id, name)
    if not recipe:
        conn.close()
        return jsonify({"error": "Recipe not found"}), 404
    run = conn.execute(
        """SELECT id, order_number, batch_number, production_date, qty_produced, notes, created_at
           FROM production_runs WHERE id = %s AND recipe_id = %s""",
        (run_id, recipe["id"])
    ).fetchone()
    if not run:
        conn.close()
        return jsonify({"error": "Run not found"}), 404
    items = conn.execute(
        """SELECT chemical_name, required_qty, deducted_qty, unit
           FROM production_run_items WHERE run_id = %s""",
        (run_id,)
    ).fetchall()
    conn.close()
    return jsonify({
        "id": run[0], "order_number": run[1], "batch_number": run[1],
        "production_date": run[2], "qty_produced": run[2], "notes": run[4], "created_at": run[5],
        "items": [
            {"chemical_name": i[0], "required_qty": i[1], "deducted_qty": i[2], "unit": i[3]}
            for i in items
        ]
    })


# ==================== API: SALES TRACKER ====================


class SaleItemIn(_StrippedModel):
    product_name: str = Field(min_length=1)
    quantity: float = Field(ge=0)
    unit_price: float = Field(ge=0)
    unit: str = Field(default="KG", min_length=1)


class SaleHeaderIn(_StrippedModel):
    pi_number: str = Field(min_length=1)
    pi_date: str | None = None
    client_name: str | None = None
    pi_file_path: str | None = None
    company_id: int | None = None
    comments: str | None = None


class SaleCreateIn(BaseModel):
    sale: SaleHeaderIn
    items: list[SaleItemIn] = Field(min_length=1)


def _validation_error_response(e: ValidationError):
    return jsonify({"error": "Invalid payload",
                    "details": [{"field": ".".join(str(p) for p in err["loc"]),
                                 "message": err["msg"]} for err in e.errors()]}), 400


def _duplicate_pi_warning(conn, pi_number: str):
    row = conn.execute("SELECT COUNT(*) FROM sales WHERE pi_number = %s",
                       (pi_number,)).fetchone()
    if row and row[0] > 0:
        return f"PI number '{pi_number}' already exists ({row[0]} existing record(s)) — saved anyway"
    return None


@app.route("/api/sales/parse", methods=["POST"])
@limiter.limit("15 per minute")
def api_parse_pi():
    """Parse an uploaded PI document (.pdf or .docx) in-memory and return extracted data."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400
    fname = file.filename.lower()
    if not (fname.endswith(".pdf") or fname.endswith(".docx")):
        return jsonify({"error": "Only .pdf and .docx files are accepted. "
                                 "For legacy .doc, save as .docx or .pdf and retry."}), 400
    try:
        import io
        from parse_sales import parse_pi_stream
        buffer = io.BytesIO(file.read())
        extraction = parse_pi_stream(buffer, file.filename)
        return jsonify(extraction.model_dump())
    except Exception:
        app.logger.exception("PI parse failed")
        return jsonify({"error": "Could not parse file. Text-based .pdf or .docx required."}), 400


@app.route("/api/sales")
def api_list_sales():
    stage = request.args.get("stage")
    search = request.args.get("q")
    conn = get_db()
    sales = get_all_sales(conn, stage=stage, search=search)
    conn.close()
    return jsonify(sales)


@app.route("/api/sales/export")
@limiter.limit("15 per minute")
def api_sales_export():
    import csv
    import io
    from flask import Response
    from datetime import date as _date
    conn = get_db()
    sales = get_all_sales(conn)
    conn.close()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "stage", "pi_number", "pi_date", "client_name",
                     "lc_number", "lc_date", "shipment_date", "item_count",
                     "total_value_usd", "total_paid_usd", "balance_usd",
                     "payment_date", "created_at"])
    from chem_stock import csv_safe
    for s in sales:
        writer.writerow([s["id"], csv_safe(s["stage"]), csv_safe(s["pi_number"]), s["pi_date"],
                         csv_safe(s["client_name"]), csv_safe(s["lc_number"]), s["lc_date"],
                         s["shipment_date"], s["item_count"], s["total_value"],
                         s["total_paid"], s["balance"], s["payment_date"],
                         s["created_at"]])
    filename = f"sales_pipeline_{_date.today().isoformat()}.csv"
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.route("/api/sales/summary")
def api_sales_summary():
    conn = get_db()
    summary = get_sales_summary(conn)
    conn.close()
    return jsonify(summary)


@app.route("/api/sales/<int:sale_id>")
def api_get_sale(sale_id):
    conn = get_db()
    sale = get_sale_by_id(conn, sale_id)
    conn.close()
    if not sale:
        return jsonify({"error": "Sale not found"}), 404
    return jsonify(sale)


@app.route("/api/sales", methods=["POST"])
def api_create_sale():
    try:
        payload = SaleCreateIn(**(request.get_json() or {}))
    except ValidationError as e:
        return _validation_error_response(e)
    sale_data = payload.sale.model_dump()
    items = [i.model_dump() for i in payload.items]
    conn = get_db()
    if sale_data.get("company_id") is not None:
        company = get_company(conn, sale_data["company_id"])
        if not company:
            conn.close()
            return jsonify({"error": "unknown company"}), 400
        sale_data["client_name"] = company["name"]
    warning = _duplicate_pi_warning(conn, sale_data["pi_number"])
    sale_id = add_sale(conn, sale_data, items)
    conn.close()
    resp = {"id": sale_id, "message": "Sale created"}
    if warning:
        resp["warning"] = warning
    return jsonify(resp), 201


class SaleHeaderPatchIn(_StrippedModel):
    pi_number: str | None = Field(default=None, min_length=1)
    pi_date: str | None = None
    client_name: str | None = None
    pi_file_path: str | None = None
    comments: str | None = None


class SaleFullItemIn(_StrippedModel):
    id: int | None = None
    product_name: str = Field(min_length=1)
    quantity: float = Field(ge=0)
    unit_price: float = Field(ge=0)
    unit: str = Field(default="KG", min_length=1)


class SaleFullUpdateIn(BaseModel):
    header: SaleHeaderIn
    items: list[SaleFullItemIn] = Field(min_length=1)
    removedIds: list[int] = Field(default_factory=list)


@app.route("/api/sales/<int:sale_id>", methods=["PUT"])
@admin_required
def api_update_sale(sale_id):
    raw = request.get_json() or {}
    if "items" in raw:
        # Atomic full update: header + items + removals in one transaction.
        try:
            full = SaleFullUpdateIn(**raw)
        except ValidationError as e:
            return _validation_error_response(e)
        conn = get_db()
        try:
            sale = update_sale_full(conn, sale_id, full.header.model_dump(),
                                    [i.model_dump() for i in full.items],
                                    full.removedIds)
        except ValueError as e:
            conn.close()
            return jsonify({"error": str(e)}), 400
        except Exception:
            conn.close()
            app.logger.exception("sale full update failed")
            return jsonify({"error": "internal server error"}), 500
        conn.close()
        if not sale:
            return jsonify({"error": "Sale not found"}), 404
        return jsonify(sale)
    try:
        patch = SaleHeaderPatchIn(**raw)
    except ValidationError as e:
        return _validation_error_response(e)
    data = patch.model_dump(exclude_none=True)
    conn = get_db()
    fields, vals = [], []
    for key in ("pi_number", "pi_date", "client_name", "pi_file_path",
                "comments"):
        if key in data:
            fields.append(f"{key} = %s")
            vals.append(data[key])
    if fields:
        from datetime import datetime as _dt, timezone
        vals.append(_dt.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
        vals.append(sale_id)
        conn.execute(f"UPDATE sales SET {', '.join(fields)}, updated_at = %s WHERE id = %s", vals)
        conn.commit()
    conn.close()
    return jsonify({"message": "Sale updated"})


@app.route("/api/sales/<int:sale_id>", methods=["DELETE"])
@admin_required
def api_delete_sale(sale_id):
    conn = get_db()
    delete_sale(conn, sale_id)
    conn.close()
    return jsonify({"message": "Sale deleted"})


@app.route("/api/sales/<int:sale_id>/move", methods=["POST"])
@admin_required
def api_move_sale(sale_id):
    data = request.get_json() or {}
    notes = data.get("notes")
    conn = get_db()
    new_stage = advance_sale(conn, sale_id, notes)
    conn.close()
    if not new_stage:
        return jsonify({"error": "Cannot advance sale"}), 400
    return jsonify({"new_stage": new_stage, "message": f"Moved to {new_stage}"})


@app.route("/api/sales/<int:sale_id>/lc", methods=["PUT"])
@admin_required
def api_update_lc(sale_id):
    data = request.get_json() or {}
    # V5: require the LC number instead of KeyError-500 on missing keys.
    lc_number = str(data.get("lc_number", "")).strip()
    if not lc_number:
        return jsonify({"error": "lc_number is required"}), 400
    conn = get_db()
    update_sale_lc(conn, sale_id, lc_number, data.get("lc_date"), data.get("shipment_date"))
    move_sale_to_stage(conn, sale_id, "lc_received", "LC details entered")
    conn.close()
    return jsonify({"message": "LC details saved"})


class PaymentIn(_StrippedModel):
    payment_date: str | None = None
    payment_amount: float = Field(gt=0)
    notes: str | None = None
    maturity_date: str | None = None


class PaymentPatchIn(_StrippedModel):
    payment_date: str | None = None
    payment_amount: float | None = Field(default=None, gt=0)
    notes: str | None = None


@app.route("/api/sales/<int:sale_id>/payment", methods=["PUT"])
@admin_required
def api_update_payment(sale_id):
    try:
        payment = PaymentIn(**(request.get_json() or {}))
    except ValidationError as e:
        return _validation_error_response(e)
    conn = get_db()
    try:
        result = record_sale_payment(conn, sale_id, payment.payment_date,
                                     payment.payment_amount, payment.notes,
                                     payment.maturity_date)
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400
    conn.close()
    result["message"] = "Payment recorded"
    return jsonify(result)


@app.route("/api/sales/<int:sale_id>/payments/<int:payment_id>", methods=["PUT"])
@admin_required
def api_edit_payment(sale_id, payment_id):
    try:
        patch = PaymentPatchIn(**(request.get_json() or {}))
    except ValidationError as e:
        return _validation_error_response(e)
    conn = get_db()
    try:
        ok = update_sale_payment_record(conn, payment_id, patch.payment_date,
                                        patch.payment_amount, patch.notes)
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400
    conn.close()
    if not ok:
        return jsonify({"error": "Payment not found"}), 404
    return jsonify({"message": "Payment updated"})


@app.route("/api/sales/<int:sale_id>/payments/<int:payment_id>", methods=["DELETE"])
@admin_required
def api_delete_payment(sale_id, payment_id):
    conn = get_db()
    ok = delete_sale_payment_record(conn, payment_id)
    conn.close()
    if not ok:
        return jsonify({"error": "Payment not found"}), 404
    return jsonify({"message": "Payment deleted"})


@app.route("/api/sales/<int:sale_id>/shipments", methods=["POST"])
@admin_required
def api_add_shipment(sale_id):
    try:
        payload = ShipmentIn.model_validate(request.get_json() or {})
    except ValidationError as e:
        return _validation_error_response(e)
    conn = get_db()
    try:
        shipment_id = add_shipment(
            conn, sale_id, payload.ship_date, payload.invoice_number,
            payload.invoice_date, payload.notes)
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400
    conn.close()
    if shipment_id is None:
        return jsonify({"error": "Sale not found"}), 404
    return jsonify({"id": shipment_id, "message": "Shipment recorded"}), 201


@app.route("/api/sales/<int:sale_id>/shipments/<int:shipment_id>", methods=["DELETE"])
@admin_required
def api_delete_shipment(sale_id, shipment_id):
    conn = get_db()
    ok = delete_shipment(conn, sale_id, shipment_id)
    conn.close()
    if not ok:
        return jsonify({"error": "Shipment not found"}), 404
    return jsonify({"message": "Shipment deleted"})


@app.route("/api/sales/<int:sale_id>/items", methods=["POST"])
@admin_required
def api_add_item(sale_id):
    try:
        item = SaleItemIn(**(request.get_json() or {}))
    except ValidationError as e:
        return _validation_error_response(e)
    conn = get_db()
    item_id = add_sale_item(conn, sale_id, item.product_name, item.quantity, item.unit_price, item.unit)
    conn.close()
    return jsonify({"id": item_id, "message": "Item added"}), 201


class SaleItemPatchIn(BaseModel):
    product_name: str | None = Field(default=None, min_length=1)
    quantity: float | None = Field(default=None, ge=0)
    unit_price: float | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, min_length=1)


class ShipmentIn(_StrippedModel):
    ship_date: str = Field(min_length=1)
    invoice_number: str | None = None
    invoice_date: str | None = None
    notes: str | None = None


@app.route("/api/sales/<int:sale_id>/items/<int:item_id>", methods=["PUT"])
@admin_required
def api_update_item(sale_id, item_id):
    try:
        patch = SaleItemPatchIn(**(request.get_json() or {}))
    except ValidationError as e:
        return _validation_error_response(e)
    conn = get_db()
    update_sale_item(conn, item_id, patch.product_name, patch.quantity, patch.unit_price, patch.unit)
    conn.close()
    return jsonify({"message": "Item updated"})


@app.route("/api/sales/<int:sale_id>/items/<int:item_id>", methods=["DELETE"])
@admin_required
def api_delete_item(sale_id, item_id):
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM sale_items WHERE sale_id = %s",
                         (sale_id,)).fetchone()[0]
    if count <= 1:
        conn.close()
        return jsonify({"error": "A sale must keep at least one product item"}), 400
    delete_sale_item(conn, item_id)
    conn.close()
    return jsonify({"message": "Item deleted"})


# ==================== API: CRON JOBS ====================
@app.route("/api/cron/maturity-check", methods=["POST"])
def api_cron_maturity_check():
    """Daily cron job to check for maturity due/escalated sales.
    
    Auth: Requires Authorization: Bearer <CRON_SECRET> header.
    Checks all unpaid sales with maturity_date <= today and creates notifications.
    """
    cron_secret = os.getenv("CRON_SECRET")
    auth_header = request.headers.get("Authorization", "")
    if not cron_secret:
        return jsonify({"error": "CRON_SECRET not configured"}), 500
    if auth_header != f"Bearer {cron_secret}":
        return jsonify({"error": "unauthorized"}), 401
    
    conn = get_db()
    try:
        today = date.today()
        # Find sales with maturity_date not null, not fully paid
        rows = conn.execute("""
            SELECT s.id, s.client_name, s.maturity_date, s.company_id,
                   COALESCE(SUM(sp.payment_amount), 0) AS paid,
                   COALESCE(ROUND(SUM(si.quantity * si.unit_price)::numeric, 2)::float8, 0) AS total
            FROM sales s
            LEFT JOIN sale_payments sp ON sp.sale_id = s.id
            LEFT JOIN sale_items si ON si.sale_id = s.id
            WHERE s.maturity_date IS NOT NULL
            GROUP BY s.id, s.client_name, s.maturity_date, s.company_id
            HAVING COALESCE(SUM(sp.payment_amount), 0) < COALESCE(ROUND(SUM(si.quantity * si.unit_price)::numeric, 2)::float8, 0)
        """).fetchall()
        
        notified = 0
        for row in rows:
            sale_id, client_name, maturity_date = row[0], row[1], row[2]
            # Parse maturity_date
            mat = None
            if maturity_date:
                try:
                    mat = date.fromisoformat(str(maturity_date)[:10])
                except ValueError:
                    continue
            if not mat:
                continue
            if mat <= today:
                notify_maturity_initial(conn, sale_id, client_name, maturity_date)
                notified += 1
                if mat <= today - timedelta(days=7):
                    notify_maturity_escalation(conn, sale_id, client_name, maturity_date, (today - mat).days)
                    notified += 1
        return jsonify({"success": True, "checked": len(rows), "notified": notified})
    except Exception as e:
        app.logger.exception("cron maturity check failed")
        return jsonify({"error": "internal server error"}), 500
    finally:
        conn.close()


# ==================== MAIN ====================
if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG") == "1"
    print("=" * 50, file=sys.stderr)
    print("  RAAS Tracker - React CRM", file=sys.stderr)
    print(f"  Mode: {'DEBUG' if debug else 'PRODUCTION'}", file=sys.stderr)
    print(f"  Host: {host}:{port}", file=sys.stderr)
    print(f"  HTTPS: {'enforced' if os.getenv('FORCE_HTTPS') == '1' else 'not enforced'}", file=sys.stderr)
    print(f"  Cookie Secure: {os.getenv('COOKIE_SECURE') != '0'}", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    app.run(debug=debug, host=host, port=port)
