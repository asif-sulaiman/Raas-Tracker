"""RAAS Tracker - Flask API Backend + React CRM Frontend"""

import os
import sys
from flask import Flask, request, jsonify, send_from_directory, g
from datetime import date
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

from chem_stock import (
    get_connection, get_all_chemicals, update_stock, add_chemical, set_reorder_level,
    add_recipe, get_recipe_by_name, add_recipe_item, list_recipes,
    list_recipe_items, update_recipe, update_recipe_item, delete_recipe_item,
    delete_recipe, generate_report, generate_multi_recipe_report, export_report_to_csv,
    compare_stock_upload, save_upload, get_upload_history, get_upload_results,
    get_unit_conversion, convert_quantity, get_all_unit_conversions, add_unit_conversion,
    validate_expiry_date, get_all_reason_codes, get_pending_approvals, get_audit_logs,
    add_sale, get_all_sales, get_sale_by_id, move_sale_to_stage, advance_sale,
    update_sale_lc, update_sale_payment, add_sale_item, update_sale_item,
    delete_sale_item, delete_sale, get_sales_summary, record_sale_payment,
    update_sale_payment_record, delete_sale_payment_record, update_sale_full
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
                        log_audit_action, check_api_key_rate_limit, record_api_key_hit)
from raas_tracker.notifications import (
    list_notifications_for, unread_count, mark_read, mark_read_all_for
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
}

# Human-admin-only paths. API keys never pass these (scripts can't manage users).
_ADMIN_PATHS = ("/api/users", "/api/keys")


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
                    "username": user["username"], "role": user["role"]}
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
    g.current_identity = ident
    # U1.7: thread-local audit actor for this request's thread.
    try:
        set_audit_actor(_actor())
    except Exception:
        pass
    return None


# ==================== SECURITY: HEADERS, CORS, HTTPS ====================
@app.after_request
def _set_security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-XSS-Protection"] = "0"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if os.getenv("FORCE_HTTPS") == "1":
        resp.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    origin = request.headers.get("Origin")
    if origin:
        resp.headers["Access-Control-Allow-Origin"] = request.host_url.rstrip("/")
        resp.headers["Access-Control-Allow-Credentials"] = "true"
    return resp


@app.before_request
def _enforce_https():
    if os.getenv("FORCE_HTTPS") == "1":
        # When behind a reverse proxy, X-Forwarded-Proto is the canonical check.
        # If the proxy says HTTP, redirect to HTTPS regardless of remote_addr.
        proto = request.headers.get("X-Forwarded-Proto")
        if proto == "http":
            return jsonify({"error": "HTTPS required"}), 301
        # Without a proxy header, only redirect non-localhost direct requests.
        if proto is None and request.scheme != "https" and request.remote_addr not in ("127.0.0.1", "::1"):
            return jsonify({"error": "HTTPS required"}), 301


@app.errorhandler(404)
def _not_found(_e):
    if request.path.startswith("/api"):
        return jsonify({"error": "not found"}), 404
    return send_from_directory(REACT_BUILD_DIR, "index.html")


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
        from datetime import datetime as _dt
        bucket = _dt.utcnow().strftime("%Y%m%d%H")
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
    Creation itself is transactional (BEGIN IMMEDIATE) so simultaneous
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
    return jsonify({"id": ident["id"], "username": ident["username"], "role": ident["role"]})


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



def get_db():
    return get_connection()


# ==================== SERVE REACT CRM ====================
@app.route("/")
def serve_root():
    return send_from_directory(REACT_BUILD_DIR, "index.html")


@app.route("/<path:path>")
def serve_static(path):
    if path and os.path.exists(os.path.join(REACT_BUILD_DIR, path)):
        return send_from_directory(REACT_BUILD_DIR, path)
    return send_from_directory(REACT_BUILD_DIR, "index.html")


# ==================== API: CHEMICALS ====================
@app.route("/api/chemicals")
def api_chemicals():
    conn = get_db()
    chemicals = get_all_chemicals(conn)
    conn.close()
    return jsonify(chemicals)


@app.route("/api/chemicals", methods=["POST"])
def api_add_chemical():
    data = request.get_json() or {}
    try:
        qty = _req_float(data, "qty", 0)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    name = str(data.get("name", "")).strip()
    unit = str(data.get("unit", "KG"))
    conn = get_db()
    success = add_chemical(conn, name, qty, unit)
    conn.close()
    if not success:
        return jsonify({"success": success, "name": name}), 409
    return jsonify({"success": success, "name": name})


@app.route("/api/chemicals/update", methods=["POST"])
def api_update_chemical():
    data = request.get_json() or {}
    try:
        delta = _req_float(data, "delta", 0)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    name = str(data.get("name", "")).strip()
    conn = get_db()
    success = update_stock(conn, name, delta)
    conn.close()
    if not success:
        return jsonify({"success": success, "name": name, "delta": delta}), 404
    return jsonify({"success": success, "name": name, "delta": delta})


@app.route("/api/chemicals/reorder", methods=["PUT"])
def api_set_reorder_level():
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


# ==================== API: RECIPES ====================
@app.route("/api/recipes")
def api_recipes():
    conn = get_db()
    recipes = list_recipes(conn)
    conn.close()
    return jsonify(recipes)


@app.route("/api/recipes/<name>")
def api_recipe_detail(name):
    conn = get_db()
    recipe = get_recipe_by_name(conn, name)
    items = list_recipe_items(conn, name)
    conn.close()
    if not recipe:
        return jsonify({"error": "Recipe not found"}), 404
    return jsonify({"recipe": recipe, "items": items})


@app.route("/api/recipes", methods=["POST"])
def api_create_recipe():
    data = request.get_json() or {}
    try:
        product_yield = _req_float(data, "yield", 1)
        water_pct = _req_float(data, "water_percentage", 0)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    name = str(data.get("name", "")).strip()
    conn = get_db()
    success = add_recipe(conn, name, product_yield, water_pct)
    conn.close()
    if not success:
        return jsonify({"success": success, "name": name}), 409
    return jsonify({"success": success, "name": name})


@app.route("/api/recipes/<name>/items", methods=["POST"])
def api_add_recipe_item(name):
    data = request.get_json() or {}
    try:
        pct = _req_float(data, "percentage", 0)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    chem_name = str(data.get("chemical", "")).strip()
    conn = get_db()
    success = add_recipe_item(conn, name, chem_name, pct)
    conn.close()
    if not success:
        return jsonify({"success": success}), 404
    return jsonify({"success": success})


@app.route("/api/recipes/<name>/items/<chem>", methods=["DELETE"])
def api_delete_recipe_item(name, chem):
    conn = get_db()
    success = delete_recipe_item(conn, name, chem)
    conn.close()
    if not success:
        return jsonify({"success": success}), 404
    return jsonify({"success": success})


@app.route("/api/recipes/<name>", methods=["DELETE"])
def api_delete_recipe(name):
    conn = get_db()
    success = delete_recipe(conn, name)
    conn.close()
    if not success:
        return jsonify({"success": success}), 404
    return jsonify({"success": success})


@app.route("/api/recipes/<name>", methods=["PUT"])
def api_update_recipe(name):
    data = request.get_json() or {}
    total_qty = data.get("total_quantity")
    water_pct = data.get("water_percentage")
    conn = get_db()
    success = update_recipe(conn, name, total_quantity=total_qty, water_percentage=water_pct)
    conn.close()
    if not success:
        return jsonify({"error": "Recipe not found"}), 404
    return jsonify({"success": True})


@app.route("/api/recipes/<name>/items/<chem>", methods=["PUT"])
def api_update_recipe_item(name, chem):
    data = request.get_json() or {}
    try:
        pct = _req_float(data, "percentage", 0)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    conn = get_db()
    success = update_recipe_item(conn, name, chem, pct)
    conn.close()
    if not success:
        return jsonify({"error": "Recipe item not found"}), 404
    return jsonify({"success": True})


# ==================== API: UPLOAD & COMPARISON ====================
@app.route("/api/upload", methods=["POST"])
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
    upload = conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
    rows = get_upload_results(conn, upload_id)
    conn.close()
    if not upload:
        return jsonify({"error": "Upload not found"}), 404
    return jsonify({"upload": upload, "rows": rows})


@app.route("/api/uploads/<int:upload_id>", methods=["DELETE"])
def api_delete_upload(upload_id):
    conn = get_db()
    upload = conn.execute("SELECT id FROM uploads WHERE id = ?", (upload_id,)).fetchone()
    if not upload:
        conn.close()
        return jsonify({"error": "Upload not found"}), 404

    conn.execute("DELETE FROM upload_rows WHERE upload_id = ?", (upload_id,))
    conn.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "deleted_id": upload_id})


@app.route("/api/uploads/<int:upload_id>/export")
def api_upload_export(upload_id):
    conn = get_db()
    upload = conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
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
def api_report_generate():
    data = request.get_json() or {}
    recipe_names = data.get("recipes", [])
    try:
        qty = _req_float(data, "qty", 0)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if not recipe_names:
        return jsonify({"error": "No recipes selected"}), 400

    conn = get_db()
    recipes_list = list_recipes(conn)

    if qty == 0:
        for name in recipe_names:
            recipe = next((r for r in recipes_list if r["name"] == name), None)
            if recipe:
                qty += recipe["total_quantity"]

    recipe_selections = [{"recipe_name": name, "production_qty": qty} for name in recipe_names]
    report = generate_multi_recipe_report(conn, recipe_selections)
    conn.close()

    return jsonify({"report": report, "recipes": recipe_names, "qty": qty})


@app.route("/api/reports/export", methods=["POST"])
def api_report_export():
    data = request.get_json() or {}
    recipe_names = data.get("recipes", [])
    try:
        qty = _req_float(data, "qty", 0)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    conn = get_db()
    recipe_selections = [{"recipe_name": name, "production_qty": qty} for name in recipe_names]
    report = generate_multi_recipe_report(conn, recipe_selections)
    conn.close()

    if report:
        # V4: recipe names are free-text input — strip path separators/traversal.
        import re as _re
        if len(recipe_names) > 1:
            safe_name = "Combined"
        else:
            safe_name = _re.sub(r"[^A-Za-z0-9_-]", "_", recipe_names[0])[:50] or "report"
        try:
            qty_int = int(qty)
        except (TypeError, ValueError):
            return jsonify({"error": "qty must be a number"}), 400
        from raas_tracker.db import data_dir as _data_dir
        output_path = os.path.join(_data_dir(), "reports", f"report_{safe_name}_{qty_int}.csv")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        export_report_to_csv(report, ", ".join(recipe_names), qty, output_path)
        return jsonify({"success": True, "filename": f"report_{safe_name}_{qty_int}.csv"})
    return jsonify({"error": "Could not generate report"}), 500


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


# ==================== API: SALES TRACKER ====================
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class _StrippedModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class SaleItemIn(_StrippedModel):
    product_name: str = Field(min_length=1)
    quantity: float = Field(ge=0)
    unit_price: float = Field(ge=0)


class SaleHeaderIn(_StrippedModel):
    pi_number: str = Field(min_length=1)
    pi_date: str | None = None
    client_name: str | None = None
    pi_file_path: str | None = None


class SaleCreateIn(BaseModel):
    sale: SaleHeaderIn
    items: list[SaleItemIn] = Field(min_length=1)


def _validation_error_response(e: ValidationError):
    return jsonify({"error": "Invalid payload",
                    "details": [{"field": ".".join(str(p) for p in err["loc"]),
                                 "message": err["msg"]} for err in e.errors()]}), 400


def _duplicate_pi_warning(conn, pi_number: str):
    row = conn.execute("SELECT COUNT(*) FROM sales WHERE pi_number = ?",
                       (pi_number,)).fetchone()
    if row and row[0] > 0:
        return f"PI number '{pi_number}' already exists ({row[0]} existing record(s)) — saved anyway"
    return None


@app.route("/api/sales/parse", methods=["POST"])
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


class SaleFullItemIn(_StrippedModel):
    id: int | None = None
    product_name: str = Field(min_length=1)
    quantity: float = Field(ge=0)
    unit_price: float = Field(ge=0)


class SaleFullUpdateIn(BaseModel):
    header: SaleHeaderIn
    items: list[SaleFullItemIn] = Field(min_length=1)
    removedIds: list[int] = Field(default_factory=list)


@app.route("/api/sales/<int:sale_id>", methods=["PUT"])
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
    for key in ("pi_number", "pi_date", "client_name", "pi_file_path"):
        if key in data:
            fields.append(f"{key} = ?")
            vals.append(data[key])
    if fields:
        vals.append(sale_id)
        conn.execute(f"UPDATE sales SET {', '.join(fields)}, updated_at = datetime('now') WHERE id = ?", vals)
        conn.commit()
    conn.close()
    return jsonify({"message": "Sale updated"})


@app.route("/api/sales/<int:sale_id>", methods=["DELETE"])
def api_delete_sale(sale_id):
    conn = get_db()
    delete_sale(conn, sale_id)
    conn.close()
    return jsonify({"message": "Sale deleted"})


@app.route("/api/sales/<int:sale_id>/move", methods=["POST"])
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


class PaymentPatchIn(_StrippedModel):
    payment_date: str | None = None
    payment_amount: float | None = Field(default=None, gt=0)
    notes: str | None = None


@app.route("/api/sales/<int:sale_id>/payment", methods=["PUT"])
def api_update_payment(sale_id):
    try:
        payment = PaymentIn(**(request.get_json() or {}))
    except ValidationError as e:
        return _validation_error_response(e)
    conn = get_db()
    try:
        result = record_sale_payment(conn, sale_id, payment.payment_date,
                                     payment.payment_amount, payment.notes)
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400
    conn.close()
    result["message"] = "Payment recorded"
    return jsonify(result)


@app.route("/api/sales/<int:sale_id>/payments/<int:payment_id>", methods=["PUT"])
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
def api_delete_payment(sale_id, payment_id):
    conn = get_db()
    ok = delete_sale_payment_record(conn, payment_id)
    conn.close()
    if not ok:
        return jsonify({"error": "Payment not found"}), 404
    return jsonify({"message": "Payment deleted"})


@app.route("/api/sales/<int:sale_id>/items", methods=["POST"])
def api_add_item(sale_id):
    try:
        item = SaleItemIn(**(request.get_json() or {}))
    except ValidationError as e:
        return _validation_error_response(e)
    conn = get_db()
    item_id = add_sale_item(conn, sale_id, item.product_name, item.quantity, item.unit_price)
    conn.close()
    return jsonify({"id": item_id, "message": "Item added"}), 201


class SaleItemPatchIn(BaseModel):
    product_name: str | None = Field(default=None, min_length=1)
    quantity: float | None = Field(default=None, ge=0)
    unit_price: float | None = Field(default=None, ge=0)


@app.route("/api/sales/<int:sale_id>/items/<int:item_id>", methods=["PUT"])
def api_update_item(sale_id, item_id):
    try:
        patch = SaleItemPatchIn(**(request.get_json() or {}))
    except ValidationError as e:
        return _validation_error_response(e)
    conn = get_db()
    update_sale_item(conn, item_id, patch.product_name, patch.quantity, patch.unit_price)
    conn.close()
    return jsonify({"message": "Item updated"})


@app.route("/api/sales/<int:sale_id>/items/<int:item_id>", methods=["DELETE"])
def api_delete_item(sale_id, item_id):
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM sale_items WHERE sale_id = ?",
                         (sale_id,)).fetchone()[0]
    if count <= 1:
        conn.close()
        return jsonify({"error": "A sale must keep at least one product item"}), 400
    delete_sale_item(conn, item_id)
    conn.close()
    return jsonify({"message": "Item deleted"})


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
