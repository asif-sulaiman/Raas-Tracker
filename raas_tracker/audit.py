"""Audit log plus per-request actor state."""

import psycopg
import json
import os
import re
from datetime import date, datetime
from typing import Optional, List, Dict, Any, Union

import threading as _threading

from flask import has_request_context, request


_audit_state = _threading.local()

# Actor labels written to audit_logs.user_id.
SYSTEM_ACTOR = "system"
# A write made without a verified credential (public route, rejected request).
# Deliberately distinct from SYSTEM_ACTOR so an unauthenticated action can
# never be mistaken for a trusted background job.
ANONYMOUS_ACTOR = "anonymous"
# The scheduled maturity-check job, set by its route handler.
CRON_ACTOR = "cron"

_MAX_IP_LEN = 45  # longest sane IPv6 text form, with room for a scope id


def set_audit_actor(name: Optional[str]) -> None:
    """Set the acting user for the current thread (called per request by Flask)."""
    _audit_state.name = name or ANONYMOUS_ACTOR


def clear_audit_actor() -> None:
    """Drop this thread's actor.

    Called at request teardown: waitress serves from a thread pool, so without
    this a thread keeps the identity of the request it last handled and stamps
    it onto the next, unauthenticated one.
    """
    _audit_state.name = None


def get_audit_actor() -> str:
    """This thread's actor, or ``system`` for work with no request context."""
    return getattr(_audit_state, "name", None) or SYSTEM_ACTOR


def request_ip() -> Optional[str]:
    """Client IP for an audit row, or None when there is no request context.

    This is ``request.remote_addr``, which ``ProxyFix(x_for=1)`` (flask_app.py)
    rewrites from the rightmost ``X-Forwarded-For`` entry. That entry is the
    real client only when a trusted proxy actually appends it: a client that
    reaches the app directly can supply the header itself. Audit IPs are
    therefore evidence, not proof — see the ProxyFix trust item in Risks.
    """
    if not has_request_context():
        return None
    return (request.remote_addr or "").strip()[:_MAX_IP_LEN] or None


def log_audit_action(conn: psycopg.Connection, action: str, entity_type: str = None,
                     entity_id: int = None, user_id: Optional[str] = None,
                     old_value: str = None, new_value: str = None,
                     ip_address: str = None, atomic: bool = True) -> int:
    """Log an audit action.

    Args:
        conn: Database connection
        action: Action performed (e.g., 'APPROVE_ROW', 'REJECT_ROW', 'ADJUST_STOCK')
        entity_type: Type of entity (e.g., 'upload', 'upload_row', 'chemical')
        entity_id: ID of entity
        user_id: User performing action
        old_value: Previous value
        new_value: New value
        ip_address: IP address of user
        atomic: When True (default) the audit row is committed immediately, as
            every caller has always expected. When False the INSERT is left in
            the caller's transaction: no commit AND no rollback is issued here,
            so an enclosing operation that aborts leaves no audit trail for work
            that never happened (and a committed one keeps its trail).

    Returns:
        Log ID
    """
    if user_id is None:
        user_id = get_audit_actor()
    if ip_address is None:
        # Default the IP from the live request so every call site is covered.
        # A caller-supplied value (e.g. ADJUST_STOCK's free-text reason) wins.
        ip_address = request_ip()
    cursor = conn.execute(
        """INSERT INTO audit_logs (action, entity_type, entity_id, user_id, old_value, new_value, ip_address)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (action, entity_type, entity_id, user_id, old_value, new_value, ip_address)
    )
    if atomic:
        conn.commit()
    return cursor.fetchone()[0]


def get_audit_logs(conn: psycopg.Connection, entity_type: str = None, 
                   entity_id: int = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Get audit logs.
    
    Args:
        conn: Database connection
        entity_type: Optional filter by entity type
        entity_id: Optional filter by entity ID
        limit: Maximum number of logs to return
    
    Returns:
        List of audit logs
    """
    query = "SELECT id, action, entity_type, entity_id, user_id, old_value, new_value, timestamp, ip_address FROM audit_logs"
    params = []
    
    if entity_type:
        query += " WHERE entity_type = %s"
        params.append(entity_type)
        if entity_id:
            query += " AND entity_id = %s"
            params.append(entity_id)
    elif entity_id:
        query += " WHERE entity_id = %s"
        params.append(entity_id)

    query += " ORDER BY timestamp DESC LIMIT %s"
    params.append(limit)
    
    cursor = conn.execute(query, params)
    return [
        {
            "id": row[0], "action": row[1], "entity_type": row[2],
            "entity_id": row[3], "user_id": row[4], "old_value": row[5],
            "new_value": row[6], "timestamp": row[7], "ip_address": row[8]
        }
        for row in cursor.fetchall()
    ]
