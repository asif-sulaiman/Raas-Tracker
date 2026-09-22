"""Shared fixtures: isolated temp-DB + seeded admin/user + Flask test client."""
import io
import os
import sqlite3
import sys
import tempfile
from contextlib import redirect_stderr

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from chem_stock import get_connection, create_first_admin, ensure_setup_token, create_user


@pytest.fixture()
def db_path():
    fd, path = tempfile.mkstemp(prefix="chemtest_", suffix=".db")
    os.close(fd)
    sqlite3.connect(path).close()
    yield path
    os.remove(path)


@pytest.fixture()
def db(db_path):
    conn = get_connection(db_path)
    with redirect_stderr(io.StringIO()):
        ensure_setup_token(conn)
    create_first_admin(conn, "admin", "admin-pass-123")
    create_user(conn, "user", "user-pass-123", role="user")
    conn.execute("DELETE FROM app_settings WHERE key = 'setup_token_hash'")
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture()
def client(db_path, db, monkeypatch):
    import flask_app
    from chem_stock import get_connection as _gc
    monkeypatch.setattr(flask_app, "get_db", lambda: _gc(db_path))
    return flask_app.app.test_client()


@pytest.fixture()
def admin_client(client, db):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"})
    assert r.status_code == 200
    return client


@pytest.fixture()
def user_client(db, db_path, monkeypatch):
    import flask_app
    from chem_stock import get_connection as _gc
    monkeypatch.setattr(flask_app, "get_db", lambda: _gc(db_path))
    c = flask_app.app.test_client()
    r = c.post("/api/auth/login", json={"username": "user", "password": "user-pass-123"})
    assert r.status_code == 200
    return c
