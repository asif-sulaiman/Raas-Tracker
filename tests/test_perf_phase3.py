"""Phase 3 performance regressions: hot-path FK indexes + reference ETags.

Every per-key query listed here was a sequential scan. Most are per-request
costs (the API-key rate-limit COUNT runs on every API-key-authenticated call;
the recipes list runs an EXISTS on production_runs per row), the rest are
per-view costs on sale/upload detail pages. The data is tiny today, so this is
hygiene for scale, not a measured win — the reference-data ETags are the part
that saves bytes on every repeat view today.

`no-cache`, not the dashboard's `max-age=15`: each of these lists backs a page
that writes through the same URL and immediately refetches it
(`Chemicals.jsx` POSTs an add then reloads the list), and `apiFetch` uses the
browser's default cache mode. A freshness window would let that refetch serve
the pre-write body — "I added it and it is not there".
"""
import pytest

import flask_app
from raas_tracker import db as dbmod

REFERENCE_ENDPOINTS = [
    "/api/chemicals",
    "/api/companies",
    "/api/recipes",
    "/api/unit-conversions",
    "/api/reason-codes",
]

# (index name, table, column list as written in the DDL)
PERF3_INDEXES = [
    ("idx_api_key_rate_limits_key_hit", "api_key_rate_limits", "(key_id, hit_at)"),
    ("idx_production_runs_recipe_id", "production_runs", "(recipe_id)"),
    ("idx_production_run_links_invoice_id", "production_run_links", "(invoice_id)"),
    ("idx_production_run_items_run_id", "production_run_items", "(run_id)"),
    ("idx_sales_stage_history_sale_id", "sales_stage_history", "(sale_id)"),
    ("idx_sale_payments_sale_id", "sale_payments", "(sale_id)"),
    ("idx_sale_payments_invoice_id", "sale_payments", "(invoice_id)"),
    ("idx_shipments_sale_id", "shipments", "(sale_id)"),
    ("idx_upload_rows_upload_id", "upload_rows", "(upload_id)"),
    ("idx_approval_workflow_upload_id", "approval_workflow", "(upload_id)"),
]


def _indexes(db):
    return {row[0]: row[1] for row in db.execute(
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = 'public'").fetchall()}


def test_hot_path_indexes_exist(db):
    """Every per-key query this phase identified must find its index."""
    indexes = _indexes(db)
    for name, table, columns in PERF3_INDEXES:
        assert name in indexes, f"{name} is missing from the migrated schema"
        definition = indexes[name]
        assert f"ON public.{table} " in definition + " ", definition
        assert columns in definition, f"{name} is not on {table}{columns}: {definition}"


def test_rate_limit_count_leads_with_the_key_column(db):
    """The rate-limit COUNT filters by key_id and a hit_at window, so key_id
    must lead the composite or the index cannot serve the hot query."""
    definition = _indexes(db)["idx_api_key_rate_limits_key_hit"]
    assert definition.index("key_id") < definition.index("hit_at"), definition


def test_schema_version_is_ten():
    """The PERF-3 indexes are new DDL: the stored version must move with them
    or the fast path skips the migration on every warm database."""
    assert dbmod._SCHEMA_VERSION == 10


# ==================== reference-data ETags ====================

@pytest.mark.parametrize("path", REFERENCE_ENDPOINTS)
def test_reference_endpoints_revalidate_with_etags(admin_client, path):
    first = admin_client.get(path)
    assert first.status_code == 200, first.get_data(as_text=True)
    etag = first.headers["ETag"]
    cache_control = first.headers["Cache-Control"]
    assert "private" in cache_control
    assert "no-cache" in cache_control, (
        "reference lists are write-then-reread from their own pages; a "
        "freshness window would serve the pre-write body on that refetch"
    )
    assert "Cookie" in first.headers.get("Vary", "")

    second = admin_client.get(path, headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.get_data() == b""
    assert second.headers["ETag"] == etag
    assert second.headers["Cache-Control"] == cache_control


def test_a_write_invalidates_the_reference_etag(admin_client):
    first = admin_client.get("/api/chemicals")
    etag = first.headers["ETag"]

    r = admin_client.post(
        "/api/chemicals", json={"name": "Perf3Acid", "qty": 5, "unit": "KG"})
    assert r.status_code == 200, r.get_json()

    changed = admin_client.get("/api/chemicals", headers={"If-None-Match": etag})
    assert changed.status_code == 200
    assert changed.headers["ETag"] != etag


def test_reference_endpoints_still_require_auth(client):
    for path in REFERENCE_ENDPOINTS:
        assert client.get(path).status_code == 401, path
