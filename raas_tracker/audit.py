"""Audit log plus per-request actor state."""

import psycopg
import json
import os
import re
from datetime import date, datetime
from typing import Optional, List, Dict, Any, Union

import threading as _threading


_audit_state = _threading.local()


def set_audit_actor(name: Optional[str]) -> None:
    """Set the acting user for the current thread (called per request by Flask)."""
    _audit_state.name = name or "system"


def log_audit_action(conn: psycopg.Connection, action: str, entity_type: str = None,
                     entity_id: int = None, user_id: str = "system",
                     old_value: str = None, new_value: str = None,
                     ip_address: str = None) -> int:
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
    
    Returns:
        Log ID
    """
    if user_id == "system":
        user_id = getattr(_audit_state, "name", "system")
    cursor = conn.execute(
        """INSERT INTO audit_logs (action, entity_type, entity_id, user_id, old_value, new_value, ip_address)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (action, entity_type, entity_id, user_id, old_value, new_value, ip_address)
    )
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
