"""Packaging register: counted containers on their own ledger, never a chemical row.

The user counts physical packaging (`25 kg bag`, `30 kg jar`, `50/120/200 kg drum`)
and needs it on the Chemical Stock page. Packaging is deliberately NOT stored in
`chemicals`, because four readers treat every chemical row as a formula ingredient
or a reconciliation line (uploads.py:75, recipes.py:499/1109, flask_app.py:2499).

These tests pin the two properties that make that safe:

1. the ledger is the single source of truth - there is no mutable `current_qty`
   to drift, a physical count is resolved to a delta server-side, and history is
   read from real columns instead of re-parsed out of audit text;
2. packaging data cannot reach the chemical surface - `get_all_chemicals()` is
   byte-identical before and after packaging rows and movements exist.
"""
import pytest

import flask_app
from raas_tracker import db as dbmod
from raas_tracker import packaging
from raas_tracker.stock import get_all_chemicals

SEEDED_LABELS = [
    "25 kg bag",
    "30 kg jar",
    "50 kg drum",
    "120 kg drum",
    "200 kg drum",
]


def _labels(conn):
    return sorted(row["label"] for row in packaging.list_packaging_items(conn))


def _columns(conn, table):
    return {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s", (table,)).fetchall()}


# --------------------------------------------------------------------------- #
# schema
# --------------------------------------------------------------------------- #
def test_packaging_tables_are_created(db):
    assert _columns(db, "packaging_items") == {
        "id", "label", "capacity_kg", "unit", "reorder_level", "created_at"}
    assert _columns(db, "packaging_movements") == {
        "id", "item_id", "kind", "delta", "before_qty", "after_qty",
        "reason", "actor", "created_at"}


def test_no_mutable_quantity_column_exists(db):
    """A stored count would drift from the ledger; the sum is the truth.

    `chemicals.current_qty` is the mutable-counter pattern this table refuses
    to copy, so the column's absence is the assertion, not an oversight.
    """
    assert "current_qty" not in _columns(db, "packaging_items")


def test_pieces_are_integers_not_real(db):
    """REAL (float4) is what makes chemical quantities imprecise; counts are exact."""
    rows = db.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'packaging_movements' "
        "AND column_name IN ('delta', 'before_qty', 'after_qty')").fetchall()
    assert rows, "movement quantity columns are missing"
    assert {r[1] for r in rows} == {"integer"}


def test_label_is_unique_case_insensitively(db):
    """Identity is lower(label), and lookup must use that same expression.

    `chemicals` indexes lower(name) but reads with `WHERE name = %s`, so a
    differently-cased lookup silently misses. Here the index and the read agree.
    """
    with pytest.raises(Exception):
        db.execute("INSERT INTO packaging_items (label, capacity_kg) "
                   "VALUES ('200 KG DRUM', 200)")
        db.execute("INSERT INTO packaging_items (label, capacity_kg) "
                   "VALUES ('25 kg BAG', 25)")
    db.rollback()


def test_migration_reapplies_packaging_tables(pg_dsn):
    """Dropping the tables and re-migrating restores them, seeds included.

    Idempotent DDL + the version gate are what let a new table arrive on an
    already-migrated production database.
    """
    conn = dbmod.get_connection(pg_dsn)
    conn.execute("DROP TABLE IF EXISTS packaging_movements, packaging_items CASCADE")
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (dbmod._SCHEMA_VERSION_KEY, str(dbmod._SCHEMA_VERSION - 1)))
    conn.commit()
    conn.close()

    conn = dbmod.get_connection(pg_dsn)
    try:
        assert {"label", "capacity_kg"} <= _columns(conn, "packaging_items")
        assert _labels(conn) == sorted(SEEDED_LABELS)
    finally:
        conn.close()


def test_packaging_seed_types_and_capacities(db):
    assert _labels(db) == sorted(SEEDED_LABELS)
    rows = {r["label"]: r for r in packaging.list_packaging_items(db)}
    assert [rows[l]["capacity_kg"] for l in SEEDED_LABELS] == [25, 30, 50, 120, 200]
    assert {rows[l]["unit"] for l in SEEDED_LABELS} == {"PCS"}
    assert all(rows[l]["qty"] == 0 for l in SEEDED_LABELS)


def test_seed_is_idempotent(db):
    """Seeds run per-connection on an empty table, so they must be safe twice."""
    dbmod._ensure_seeds(db)
    dbmod._ensure_seeds(db)
    assert _labels(db) == sorted(SEEDED_LABELS)


# --------------------------------------------------------------------------- #
# ledger
# --------------------------------------------------------------------------- #
def test_received_delta_moves_the_count(db):
    row = packaging.record_packaging_movement(
        db, label="200 kg drum", kind="RECEIVED", delta=40,
        reason="Supplier delivery")
    assert (row["delta"], row["before_qty"], row["after_qty"]) == (40, 0, 40)
    items = {r["label"]: r for r in packaging.list_packaging_items(db)}
    assert items["200 kg drum"]["qty"] == 40


def test_physical_count_is_resolved_server_side(db):
    """The client sends what it counted, never a delta it computed.

    Chemicals take a browser-derived delta (Chemicals.jsx:77), so two people
    editing one row lose an edit. Here the server owns the subtraction.
    """
    packaging.record_packaging_movement(db, label="50 kg drum", kind="RECEIVED", delta=40)
    row = packaging.record_packaging_movement(
        db, label="50 kg drum", kind="COUNT", counted_qty=37,
        reason="Physical count")
    assert (row["delta"], row["before_qty"], row["after_qty"]) == (-3, 40, 37)
    assert packaging.packaging_count(db, label="50 kg drum") == 37


def test_count_to_zero_keeps_the_history(db):
    packaging.record_packaging_movement(db, label="30 kg jar", kind="RECEIVED", delta=12)
    packaging.record_packaging_movement(db, label="30 kg jar", kind="COUNT", counted_qty=0)
    movements = packaging.get_packaging_movements(db, label="30 kg jar")
    assert [m["after_qty"] for m in movements] == [0, 12]
    assert [m["kind"] for m in movements] == ["COUNT", "RECEIVED"]


def test_sum_of_deltas_equals_the_last_after_qty(db):
    for kind, delta in (("RECEIVED", 50), ("ISSUED", -20), ("DAMAGED", -3),
                        ("RECEIVED", 5)):
        packaging.record_packaging_movement(db, label="120 kg drum", kind=kind, delta=delta)
    last = packaging.get_packaging_movements(db, label="120 kg drum")[0]
    assert packaging.packaging_count(db, label="120 kg drum") == 32 == last["after_qty"]


def test_movement_cannot_take_the_count_negative(db):
    """Rejected, and nothing is left behind - not a half-written ledger row."""
    packaging.record_packaging_movement(db, label="25 kg bag", kind="RECEIVED", delta=40)
    with pytest.raises(ValueError):
        packaging.record_packaging_movement(
            db, label="25 kg bag", kind="ISSUED", delta=-500)
    assert packaging.packaging_count(db, label="25 kg bag") == 40
    assert len(packaging.get_packaging_movements(db, label="25 kg bag")) == 1


def test_count_cannot_be_negative(db):
    with pytest.raises(ValueError):
        packaging.record_packaging_movement(
            db, label="25 kg bag", kind="COUNT", counted_qty=-1)


def test_movement_requires_a_delta_or_a_count(db):
    with pytest.raises(ValueError):
        packaging.record_packaging_movement(db, label="25 kg bag", kind="RECEIVED")
    with pytest.raises(ValueError):
        packaging.record_packaging_movement(
            db, label="25 kg bag", kind="COUNT", delta=5)


def test_unknown_kind_is_rejected(db):
    with pytest.raises(ValueError):
        packaging.record_packaging_movement(
            db, label="25 kg bag", kind="MAGIC", delta=1)


def test_unknown_label_returns_none(db):
    assert packaging.record_packaging_movement(
        db, label="invisible crate", kind="RECEIVED", delta=1) is None
    assert packaging.set_packaging_reorder_level(db, "invisible crate", 5) is False


# --------------------------------------------------------------------------- #
# items + reorder level
# --------------------------------------------------------------------------- #
def test_create_item_returns_it_and_rejects_a_duplicate(db):
    created = packaging.create_packaging_item(db, "1000 L IBC", capacity_kg=1000)
    assert created["label"] == "1000 L IBC"
    assert packaging.create_packaging_item(db, "1000 l ibc") is None
    assert "1000 L IBC" in _labels(db)


def test_set_reorder_level_round_trips(db):
    assert packaging.set_packaging_reorder_level(db, "200 kg drum", 25) is True
    items = {r["label"]: r for r in packaging.list_packaging_items(db)}
    assert items["200 kg drum"]["reorder_level"] == 25


def test_reorder_level_cannot_be_negative(db):
    with pytest.raises(ValueError):
        packaging.set_packaging_reorder_level(db, "200 kg drum", -5)


# --------------------------------------------------------------------------- #
# low-stock alerts (deduped, and cleared on recovery)
# --------------------------------------------------------------------------- #
def _notifications(conn, item_id):
    return conn.execute(
        "SELECT type, severity, dedupe_key FROM notifications "
        "WHERE entity_type = 'packaging' AND entity_id = %s ORDER BY id",
        (item_id,)).fetchall()


def test_short_count_raises_one_alert_and_recovery_clears_it(db):
    item = packaging.create_packaging_item(db, "20 L jerrycan", capacity_kg=20)
    # A type born at zero is not yet a shortage: on a fresh database the five
    # seeded types would otherwise open the register with five critical alerts.
    assert _notifications(db, item["id"]) == []

    packaging.record_packaging_movement(
        db, item_id=item["id"], kind="RECEIVED", delta=25)
    packaging.set_packaging_reorder_level(db, "20 L jerrycan", 10)
    row = packaging.record_packaging_movement(
        db, item_id=item["id"], kind="ISSUED", delta=-15)
    assert row["after_qty"] == 10
    assert [a[0] for a in _notifications(db, item["id"])] == ["packaging_low"]

    # Still below the level: the same condition must not spam a second row.
    packaging.record_packaging_movement(
        db, item_id=item["id"], kind="ISSUED", delta=-1)
    assert len(_notifications(db, item["id"])) == 1

    packaging.record_packaging_movement(
        db, item_id=item["id"], kind="RECEIVED", delta=30)
    assert _notifications(db, item["id"]) == []


def test_zero_is_critical_even_without_a_reorder_level(db):
    item = packaging.create_packaging_item(db, "No Alarm Drum", capacity_kg=50)
    packaging.record_packaging_movement(
        db, item_id=item["id"], kind="RECEIVED", delta=5)
    packaging.record_packaging_movement(
        db, item_id=item["id"], kind="SCRAPPED", delta=-5)
    alerts = _notifications(db, item["id"])
    assert [a[0] for a in alerts] == ["packaging_out"]
    assert alerts[0][1] == "critical"


# --------------------------------------------------------------------------- #
# audit trail
# --------------------------------------------------------------------------- #
def test_writes_are_audited_with_old_and_new_values(db):
    packaging.record_packaging_movement(
        db, label="200 kg drum", kind="RECEIVED", delta=9, reason="Delivery")
    row = db.execute(
        "SELECT entity_type, action, old_value, new_value FROM audit_logs "
        "WHERE action = 'PACKAGING_MOVEMENT' ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0] == "packaging"
    assert (row[2], row[3]) == ("0", "9")

    packaging.set_packaging_reorder_level(db, "200 kg drum", 3)
    row = db.execute(
        "SELECT action, old_value, new_value FROM audit_logs "
        "WHERE action = 'SET_PACKAGING_REORDER_LEVEL' ORDER BY id DESC LIMIT 1").fetchone()
    assert (row[1], row[2]) == ("0", "3")


# --------------------------------------------------------------------------- #
# the separation that makes this safe
# --------------------------------------------------------------------------- #
def test_packaging_data_never_reaches_the_chemical_surface(admin_client, db):
    """The regression guard for choosing a separate table over a `kind` column.

    Every chemical reader is unchanged by this slice, so with packaging rows and
    movements present the chemical list must be exactly what it was.
    """
    r = admin_client.post("/api/chemicals",
                          json={"name": "GuardAcid", "qty": 5, "unit": "KG"})
    assert r.status_code == 200
    before = get_all_chemicals(db)

    for label in SEEDED_LABELS:
        assert packaging.record_packaging_movement(
            db, label=label, kind="RECEIVED", delta=7) is not None

    after = get_all_chemicals(db)
    assert after == before, "packaging rows changed the chemical list"

    names = {c["name"].strip().lower() for c in after}
    assert not (names & {l.lower() for l in SEEDED_LABELS}), \
        "a packaging type is visible as a chemical"
    assert db.execute("SELECT COUNT(*) FROM chemicals").fetchone()[0] == 1


# --------------------------------------------------------------------------- #
# HTTP surface
# --------------------------------------------------------------------------- #
def test_list_items_shape_and_etag(admin_client, db):
    packaging.record_packaging_movement(
        db, label="25 kg bag", kind="RECEIVED", delta=100)
    r = admin_client.get("/api/packaging")
    assert r.status_code == 200
    rows = r.get_json()
    assert len(rows) == 5
    bag = next(x for x in rows if x["label"] == "25 kg bag")
    assert bag["qty"] == 100
    assert bag["capacity_kg"] == 25

    etag = r.headers["ETag"]
    again = admin_client.get("/api/packaging", headers={"If-None-Match": etag})
    assert again.status_code == 304


def test_anonymous_is_refused():
    c = flask_app.app.test_client()
    assert c.get("/api/packaging").status_code == 401
    assert c.post("/api/packaging/movements",
                  json={"label": "25 kg bag", "kind": "RECEIVED",
                        "delta": 1}).status_code == 401


def test_create_item_is_admin_only(admin_client, user_client):
    r = user_client.post("/api/packaging/items",
                         json={"label": "Operator Crate", "capacity_kg": 5})
    assert r.status_code == 403
    assert r.get_json()["error"] == "admin required"
    assert admin_client.post("/api/packaging/items",
                             json={"label": "Admin Crate", "capacity_kg": 5}).status_code == 200


def test_create_item_duplicate_is_409(admin_client):
    assert admin_client.post("/api/packaging/items",
                             json={"label": "Dup Drum", "capacity_kg": 10}).status_code == 200
    r = admin_client.post("/api/packaging/items",
                          json={"label": "dup drum", "capacity_kg": 10})
    assert r.status_code == 409


def test_validation_errors_use_the_error_contract(admin_client):
    r = admin_client.post("/api/packaging/items", json={"label": "  "})
    assert r.status_code == 400
    assert r.get_json()["details"]
    r = admin_client.post("/api/packaging/items",
                          json={"label": "Neg", "capacity_kg": -1})
    assert r.status_code == 400
    r = admin_client.post("/api/packaging/movements",
                          json={"label": "25 kg bag", "kind": "NOT_A_KIND", "delta": 1})
    assert r.status_code == 400


def test_movement_is_open_to_an_operator(user_client, db):
    """Recording what arrived is a floor-level action, like unit conversions."""
    r = user_client.post("/api/packaging/movements",
                         json={"label": "200 kg drum", "kind": "RECEIVED", "delta": 6})
    assert r.status_code == 200
    assert packaging.packaging_count(db, label="200 kg drum") == 6


def test_movement_rejects_a_negative_result_as_400(admin_client, db):
    packaging.record_packaging_movement(db, label="30 kg jar", kind="RECEIVED", delta=4)
    r = admin_client.post("/api/packaging/movements",
                          json={"label": "30 kg jar", "kind": "ISSUED", "delta": -9})
    assert r.status_code == 400
    assert packaging.packaging_count(db, label="30 kg jar") == 4


def test_movement_on_an_unknown_label_is_404(admin_client):
    r = admin_client.post("/api/packaging/movements",
                          json={"label": "ghost drum", "kind": "RECEIVED", "delta": 1})
    assert r.status_code == 404


def test_reorder_level_round_trip_and_admin_gate(admin_client, user_client):
    r = user_client.put("/api/packaging/reorder",
                        json={"label": "50 kg drum", "reorder_level": 5})
    assert r.status_code == 403
    r = admin_client.put("/api/packaging/reorder",
                         json={"label": "50 kg drum", "reorder_level": 5})
    assert r.status_code == 200
    items = {x["label"]: x for x in admin_client.get("/api/packaging").get_json()}
    assert items["50 kg drum"]["reorder_level"] == 5


def test_history_is_a_direct_read(admin_client, db):
    packaging.record_packaging_movement(db, label="120 kg drum", kind="RECEIVED", delta=8)
    packaging.record_packaging_movement(db, label="120 kg drum", kind="COUNT", counted_qty=3)
    rows = admin_client.get("/api/packaging/history",
                            query_string={"label": "120 kg drum"}).get_json()
    assert [r["after_qty"] for r in rows] == [3, 8]
    assert rows[0]["delta"] == -5
    # History must not be reconstructed from audit text.
    assert all("kind" in r and "before_qty" in r for r in rows)


def test_history_limit_is_clamped(admin_client, db):
    for _ in range(3):
        packaging.record_packaging_movement(db, label="25 kg bag", kind="RECEIVED", delta=1)
    rows = admin_client.get("/api/packaging/history",
                            query_string={"limit": "99999"}).get_json()
    assert len(rows) <= flask_app.PACKAGING_HISTORY_MAX_LIMIT


def test_write_routes_declare_the_write_budget(admin_client, monkeypatch):
    """AGENTS.md: 15/min on mutating routes, not the 300/min read default."""
    monkeypatch.delenv("RAAS_RATE_LIMITS", raising=False)
    codes = [admin_client.post("/api/packaging/movements",
                               json={"label": "25 kg bag", "kind": "RECEIVED",
                                     "delta": 1}).status_code
             for _ in range(flask_app.WRITE_LIMIT_PER_MINUTE + 1)]
    assert codes[:flask_app.WRITE_LIMIT_PER_MINUTE] == [200] * flask_app.WRITE_LIMIT_PER_MINUTE
    assert codes[flask_app.WRITE_LIMIT_PER_MINUTE] == 429
