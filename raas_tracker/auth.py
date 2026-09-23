"""Users, sessions, API keys, login throttle, and setup tokens."""

import psycopg
import json
import os
import re
from datetime import date, datetime
from typing import Optional, List, Dict, Any, Union

from .audit import log_audit_action

import hashlib as _hashlib
import secrets as _secrets
from datetime import datetime as _datetime, timedelta as _timedelta


BCRYPT_ROUNDS = 13
SESSION_TTL_HOURS = 1
MAX_LOGIN_FAILS = 5
LOGIN_WINDOW_MINUTES = 10


def _hash_password(password: str) -> str:
    """bcrypt-12 over a SHA256 pre-hash (avoids bcrypt's 72-byte truncation)."""
    import bcrypt
    digest = _hashlib.sha256(password.encode("utf-8")).hexdigest()
    return bcrypt.hashpw(digest.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")


def _check_password(password: str, password_hash: str) -> bool:
    import bcrypt
    digest = _hashlib.sha256(password.encode("utf-8")).hexdigest()
    try:
        return bcrypt.checkpw(digest.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def validate_username(username: str) -> str:
    """Normalize + validate a username. Raises ValueError."""
    import re as _re
    name = (username or "").strip()
    if not (3 <= len(name) <= 32):
        raise ValueError("username must be 3-32 characters")
    if not _re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("username may only contain letters, digits, _, . and -")
    return name


def validate_password(password: str) -> None:
    """Raises ValueError if the password is unacceptable."""
    if password is None or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    if len(password) > 512:
        raise ValueError("password must be at most 512 characters")


def create_user(conn: psycopg.Connection, username: str, password: str,
                role: str = "user") -> int:
    """Create a user with a bcrypt-12 hash. Returns the new user ID."""
    name = validate_username(username)
    validate_password(password)
    if role not in ("admin", "user"):
        raise ValueError("role must be 'admin' or 'user'")
    try:
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s) RETURNING id",
            (name, _hash_password(password), role)
        )
        uid = cursor.fetchone()[0]
    except psycopg.IntegrityError:
        raise ValueError(f"username '{name}' already exists")
    conn.commit()
    log_audit_action(conn, "USER_CREATE", "user", uid, new_value=f"{name}:{role}")
    conn.commit()
    return uid


def get_setting(conn: psycopg.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM app_settings WHERE key = %s", (key,)).fetchone()
    return row[0] if row else None


def ensure_setup_token(conn: psycopg.Connection) -> Optional[str]:
    """Generate the single-use setup token (raw) on first need.

    Stores only its SHA256 hash. Prints the raw token to the server console
    for the operator. Returns None when setup is closed or a token exists.
    """
    if count_users(conn) > 0:
        return None
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'setup_token_hash'").fetchone()
    if row:
        return None
    raw = _secrets.token_urlsafe(32)
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('setup_token_hash', %s) "
                 "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                 (_hashlib.sha256(raw.encode("utf-8")).hexdigest(),))
    conn.commit()
    import sys as _sys
    print("=" * 64, file=_sys.stderr)
    print("  SETUP TOKEN (single-use - enter it on the /setup page):", file=_sys.stderr)
    print(f"  {raw}", file=_sys.stderr)
    print("  If lost, delete the 'setup_token_hash' row from app_settings", file=_sys.stderr)
    print("  to regenerate. Set DISABLE_SETUP=true to disable setup.", file=_sys.stderr)
    print("=" * 64, file=_sys.stderr)
    return raw


def check_setup_token(conn: psycopg.Connection, presented: Optional[str]) -> bool:
    """Constant-time comparison of the presented setup token."""
    import hmac as _hmac
    if not presented:
        return False
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'setup_token_hash'").fetchone()
    if not row:
        return False
    digest = _hashlib.sha256(presented.encode("utf-8")).hexdigest()
    return _hmac.compare_digest(digest, row[0])


def create_first_admin(conn: psycopg.Connection, username: str, password: str) -> int:
    """Race-safe first-admin creation.

    psycopg transactions are implicit, replacing BEGIN IMMEDIATE: the
    count-then-insert is atomic, and UNIQUE(username) makes double-submit
    safe (unique violations map back to ValueError to keep the API).
    """
    try:
        if count_users(conn) > 0:
            raise ValueError("setup already completed")
        name = validate_username(username)
        validate_password(password)
        try:
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, 'admin') RETURNING id",
                (name, _hash_password(password))
            )
            uid = cursor.fetchone()[0]
        except psycopg.IntegrityError:
            raise ValueError("setup already completed")
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('setup_completed', 'true') "
                     "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value")
        log_audit_action(conn, "USER_CREATE", "user", uid, new_value=f"{name}:admin")
        conn.commit()
        return uid
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


def get_user_by_username(conn: psycopg.Connection, username: str) -> Optional[Dict[str, Any]]:
    """Return public user fields (never the password hash)."""
    row = conn.execute(
        "SELECT id, username, role, created_at FROM users WHERE username = %s",
        (username,)
    ).fetchone()
    if not row:
        return None
    return {"id": row[0], "username": row[1], "role": row[2], "created_at": row[3]}


_DUMMY_HASH: Optional[str] = None


def _dummy_hash() -> str:
    """Lazily-built bcrypt hash used to equalize login timing.

    Unknown usernames run the same bcrypt work as real ones, so response
    time cannot reveal which usernames exist.
    """
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = _hash_password("dummy-never-a-real-password")
    return _DUMMY_HASH


def verify_user(conn: psycopg.Connection, username: str, password: str) -> Optional[Dict[str, Any]]:
    """Check credentials. Returns the public user dict or None (generic failure)."""
    row = conn.execute(
        "SELECT id, username, password_hash, role, created_at FROM users WHERE username = %s",
        ((username or "").strip(),)
    ).fetchone()
    if not row:
        _check_password(password or "", _dummy_hash())
        return None
    if not _check_password(password or "", row[2]):
        return None
    return {"id": row[0], "username": row[1], "role": row[3], "created_at": row[4]}


def list_users(conn: psycopg.Connection) -> List[Dict[str, Any]]:
    """All users, public fields only."""
    return [
        {"id": r[0], "username": r[1], "role": r[2], "created_at": r[3]}
        for r in conn.execute("SELECT id, username, role, created_at FROM users ORDER BY id").fetchall()
    ]


def count_users(conn: psycopg.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def delete_user(conn: psycopg.Connection, user_id: int) -> bool:
    """Delete a user (cascades sessions). Refuses to remove the last admin."""
    row = conn.execute("SELECT role, username FROM users WHERE id = %s", (user_id,)).fetchone()
    if not row:
        return False
    if row[0] == "admin":
        admins = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
        if admins <= 1:
            raise ValueError("cannot delete the last admin")
    conn.execute("DELETE FROM users WHERE id = %s", (user_id,))
    log_audit_action(conn, "USER_DELETE", "user", user_id, old_value=row[1])
    conn.commit()
    return True


def create_session(conn: psycopg.Connection, user_id: int,
                   ttl_hours: int = SESSION_TTL_HOURS) -> str:
    """Mint a session token. Returns the raw token (only time it is visible)."""
    token = _secrets.token_urlsafe(32)
    token_hash = _hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires = (_datetime.utcnow() + _timedelta(hours=ttl_hours)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
        (token_hash, user_id, expires)
    )
    conn.commit()
    return token


def get_session_user(conn: psycopg.Connection, token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Validate a session token. Returns the user dict or None."""
    if not token:
        return None
    token_hash = _hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = conn.execute(
        """SELECT u.id, u.username, u.role, u.created_at, s.expires_at, s.revoked
           FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = %s
           AND s.expires_at > to_char(clock_timestamp(), 'YYYY-MM-DD HH:MM:SS')""",
        (token_hash,)
    ).fetchone()
    if not row or row[5]:
        return None
    return {"id": row[0], "username": row[1], "role": row[2], "created_at": row[3]}


def revoke_session(conn: psycopg.Connection, token: Optional[str]) -> None:
    """Revoke a single session (logout / kill switch)."""
    if not token:
        return
    conn.execute("UPDATE sessions SET revoked = 1 WHERE token_hash = %s",
                 (_hashlib.sha256(token.encode("utf-8")).hexdigest(),))
    conn.commit()


def revoke_user_sessions(conn: psycopg.Connection, user_id: int) -> None:
    """Revoke all sessions of a user."""
    conn.execute("UPDATE sessions SET revoked = 1 WHERE user_id = %s", (user_id,))
    conn.commit()


def cleanup_expired_sessions(conn: psycopg.Connection) -> int:
    """Delete expired sessions. Returns rows removed."""
    cursor = conn.execute("DELETE FROM sessions WHERE expires_at <= "
                          "to_char(NOW(), 'YYYY-MM-DD HH:MM:SS')")
    conn.commit()
    return cursor.rowcount


def _api_key_hash(raw_key: str) -> str:
    return _hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def create_api_key(conn: psycopg.Connection, name: str, created_by: Optional[int] = None,
                   expires_at: Optional[str] = None, allowed_ips: str = "") -> str:
    """Create a script API key. Returns the raw key ONCE (never stored)."""
    import re as _re
    name = (name or "").strip()
    if not (1 <= len(name) <= 64):
        raise ValueError("key name must be 1-64 characters")
    raw = "ck_live_" + _secrets.token_urlsafe(32)
    conn.execute(
        """INSERT INTO api_keys (key_hash, name, created_by, expires_at, allowed_ips)
           VALUES (%s, %s, %s, %s, %s)""",
        (_api_key_hash(raw), name, created_by, expires_at, allowed_ips or "")
    )
    conn.commit()
    return raw


def list_api_keys(conn: psycopg.Connection) -> List[Dict[str, Any]]:
    """All keys WITHOUT hashes. Includes a fingerprint (first 12 hash chars)."""
    return [
        {"id": r[0], "fingerprint": r[1][:12], "name": r[2], "created_by": r[3],
         "created_at": r[4], "expires_at": r[5], "allowed_ips": r[6],
         "revoked": bool(r[7]), "last_used_at": r[8], "last_used_ip": r[9]}
        for r in conn.execute(
            """SELECT id, key_hash, name, created_by, created_at, expires_at,
                      allowed_ips, revoked, last_used_at, last_used_ip
               FROM api_keys ORDER BY id""").fetchall()
    ]


def _ip_allowed(allowed_ips: str, ip: Optional[str]) -> bool:
    """Comma-separated exact IPs/CIDRs. Empty means any."""
    import ipaddress as _ip
    spec = (allowed_ips or "").strip()
    if not spec:
        return True
    if not ip:
        return False
    for entry in [e.strip() for e in spec.split(",") if e.strip()]:
        try:
            if "/" in entry:
                if _ip.ip_address(ip) in _ip.ip_network(entry, strict=False):
                    return True
            elif ip == entry:
                return True
        except ValueError:
            continue
    return False


def validate_api_key(conn: psycopg.Connection, raw_key: Optional[str],
                     ip: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Stateless per-request validation: hash, revocation, expiry, IP allowlist."""
    if not raw_key:
        return None
    row = conn.execute(
        """SELECT id, name, expires_at, allowed_ips, revoked FROM api_keys
           WHERE key_hash = %s
           AND (expires_at IS NULL OR expires_at > to_char(clock_timestamp(), 'YYYY-MM-DD HH:MM:SS'))""",
        (_api_key_hash(raw_key),)
    ).fetchone()
    if not row or row[4]:
        return None
    if not _ip_allowed(row[3], ip):
        return None
    conn.execute("UPDATE api_keys SET last_used_at = to_char(NOW(), 'YYYY-MM-DD HH:MM:SS'), last_used_ip = %s WHERE id = %s",
                 (ip, row[0]))
    conn.commit()
    return {"id": row[0], "name": row[1], "type": "api-key"}


def revoke_api_key(conn: psycopg.Connection, key_id: int) -> bool:
    """Permanent revocation. Independent of user sessions (separate kill switch)."""
    cursor = conn.execute("UPDATE api_keys SET revoked = 1 WHERE id = %s", (key_id,))
    conn.commit()
    return cursor.rowcount > 0


def record_login_attempt(conn: psycopg.Connection, username: Optional[str],
                         ip_address: Optional[str], success: bool) -> None:
    conn.execute(
        "INSERT INTO login_attempts (username, ip_address, success) VALUES (%s, %s, %s)",
        ((username or "").strip() or None, ip_address, 1 if success else 0)
    )
    # Periodic cleanup: remove entries older than 1 day (server clock —
    # the app host and the database host may disagree by seconds or more).
    conn.execute("DELETE FROM login_attempts WHERE attempted_at < "
                 "to_char(NOW() - INTERVAL '1 day', 'YYYY-MM-DD HH:MM:SS')")
    conn.commit()


def is_login_blocked(conn: psycopg.Connection, username: Optional[str],
                     ip_address: Optional[str], max_fails: int = MAX_LOGIN_FAILS,
                     window_minutes: int = LOGIN_WINDOW_MINUTES) -> bool:
    """True when failures in the window reach the limit (per username OR per IP)."""
    name = (username or "").strip() or None
    # Window computed on the database clock via clock_timestamp() (true
    # current time, not the enclosing transaction's start time): stored
    # timestamps come from NOW(), and the app host clock may differ from
    # the database clock.
    row = conn.execute(
        """SELECT COUNT(*) FROM login_attempts
           WHERE success = 0
           AND attempted_at >= to_char(clock_timestamp() - make_interval(mins => %s), 'YYYY-MM-DD HH:MM:SS')
           AND (username = %s OR ip_address = %s)""",
        (window_minutes, name, ip_address)
    ).fetchone()
    return (row[0] if row else 0) >= max_fails


def check_api_key_rate_limit(conn: psycopg.Connection, key_id: int,
                              max_hits: int = 300, window_seconds: int = 60) -> bool:
    """True if rate limit exceeded for this API key (DB-backed, survives restarts)."""
    row = conn.execute(
        """SELECT COUNT(*) FROM api_key_rate_limits
           WHERE key_id = %s
           AND hit_at >= to_char(clock_timestamp() - make_interval(secs => %s), 'YYYY-MM-DD HH:MM:SS')""",
        (key_id, window_seconds)
    ).fetchone()
    return (row[0] if row else 0) >= max_hits


def record_api_key_hit(conn: psycopg.Connection, key_id: int) -> None:
    """Record an API key usage hit and clean old entries."""
    conn.execute("INSERT INTO api_key_rate_limits (key_id) VALUES (%s)", (key_id,))
    conn.execute("DELETE FROM api_key_rate_limits WHERE hit_at < "
                 "to_char(NOW() - INTERVAL '1 day', 'YYYY-MM-DD HH:MM:SS')")
    conn.commit()
