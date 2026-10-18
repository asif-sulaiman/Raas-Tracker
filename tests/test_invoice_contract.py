"""INVOICE-CONTRACT phase 1: explicit lines + required invoice_date.

The old contract allowed three create modes: explicit lines, auto-seeded
lines, and a header-only seam used by tests. The header-only seam let a real
receivable exist with no lines, which the invoice-driven report could not see
(it reported the money as quantity 0 / total 0 / due 0). This phase removes
every line-less mode: an invoice is now born with >= 1 explicit line, and it
carries the ``invoice_date`` the operator states (previously the date was not
recorded at all; ``created_at`` was the only trace).

These tests are the RED-first spec for that contract; the schema bump to 6 is
asserted directly so a missing bump cannot silently skip the migration.
"""

import uuid

import pytest

from raas_tracker import sales as salesmod
from raas_tracker._exceptions import ServiceError
from raas_tracker.db import _SCHEMA_VERSION, _table_columns

INVOICE_DATE = "2026-01-15"


def _tag() -> str:
    return uuid.uuid4().hex[:8]


# --------------------------------------------------------------------------- #
# service-level helpers (patterns from tests/test_invoice_items.py)
# --------------------------------------------------------------------------- #
def _company(conn, name=None):
    name = name or f"Contract Co {_tag()}"
    row = conn.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id", (name,)
    ).fetchone()
    conn.commit()
    return row[0]


def _sale(conn, company_id, product="ContractProd", qty=10, price=5):
    return salesmod.add_sale(
        conn,
        {"pi_number": f"PI-{_tag()}", "client_name": "x",
         "company_id": company_id},
        [{"product_name": product, "quantity": qty, "unit_price": price}],
    )


def _sale_item_id(conn, sale_id):
    return conn.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id LIMIT 1",
        (sale_id,)).fetchone()[0]


def _line(sale_item_id, quantity):
    return {"sale_item_id": sale_item_id, "quantity": quantity}


# --------------------------------------------------------------------------- #
# API-level helpers
# --------------------------------------------------------------------------- #
def _api_company(admin_client, db, name=None):
    name = name or f"Contract API Co {_tag()}"
    r = admin_client.post("/api/companies", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return db.execute(
        "SELECT id FROM companies WHERE name = %s", (name,)).fetchone()[0]


def _api_sale(admin_client, company_id):
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": f"PI-{_tag()}", "client_name": "x",
                 "company_id": company_id},
        "items": [{"product_name": "ContractApiProd", "quantity": 10,
                   "unit_price": 5, "unit": "KG"}]})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


# --------------------------------------------------------------------------- #
# 1. the service refuses a line-less invoice
# --------------------------------------------------------------------------- #
def test_create_invoice_requires_at_least_one_line(db):
    """``lines=None``/``[]`` is no longer a create mode; nothing is persisted."""
    co = _company(db)
    sid = _sale(db, co)
    for bad in (None, []):
        with pytest.raises(ServiceError, match="lines are required"):
            salesmod.create_invoice(db, sid, f"INV-{_tag()}", lines=bad,
                                    invoice_date=INVOICE_DATE)
        db.rollback()
    assert db.execute(
        "SELECT COUNT(*) FROM invoices WHERE sale_id = %s", (sid,)
    ).fetchone()[0] == 0


def test_batch_still_refuses_an_entry_without_lines(db):
    """The batch route always required >= 1 line per entry; it keeps doing so."""
    co = _company(db)
    sid = _sale(db, co)
    si = _sale_item_id(db, sid)
    for bad in (None, []):
        with pytest.raises(ValueError):
            salesmod.create_invoices_batch(db, sid, [
                {"invoice_number": f"INV-{_tag()}", "invoice_date": INVOICE_DATE,
                 "lines": bad}])


# --------------------------------------------------------------------------- #
# 2. the service refuses a missing invoice_date
# --------------------------------------------------------------------------- #
def test_create_invoice_requires_invoice_date(db):
    """Blank/absent date is a refusal, and it is named in the message."""
    co = _company(db)
    sid = _sale(db, co)
    si = _sale_item_id(db, sid)
    for bad in (None, "", "   "):
        with pytest.raises(ServiceError, match="invoice_date is required"):
            salesmod.create_invoice(db, sid, f"INV-{_tag()}",
                                    lines=[_line(si, 1)], invoice_date=bad)
        db.rollback()
    assert db.execute(
        "SELECT COUNT(*) FROM invoices WHERE sale_id = %s", (sid,)
    ).fetchone()[0] == 0


def test_batch_requires_invoice_date_per_entry(db):
    """Every batch entry states its own date; a missing one refuses the batch."""
    co = _company(db)
    sid = _sale(db, co)
    si = _sale_item_id(db, sid)
    with pytest.raises(ServiceError, match="invoice_date"):
        salesmod.create_invoices_batch(db, sid, [
            {"invoice_number": f"INV-{_tag()}", "lines": [_line(si, 1)]}])
    assert db.execute(
        "SELECT COUNT(*) FROM invoices WHERE sale_id = %s", (sid,)
    ).fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# 3. invoice_date round-trips (stripped, persisted, listed)
# --------------------------------------------------------------------------- #
def test_invoice_date_round_trips_through_create_and_list(db):
    co = _company(db)
    sid = _sale(db, co)
    si = _sale_item_id(db, sid)
    out = salesmod.create_invoice(db, sid, f"INV-{_tag()}",
                                  lines=[_line(si, 2)],
                                  invoice_date=" 2026-02-03 ")
    assert out["invoice_date"] == "2026-02-03", "stored stripped"
    row = db.execute("SELECT invoice_date FROM invoices WHERE id = %s",
                     (out["invoice_id"],)).fetchone()
    assert row[0] == "2026-02-03"
    listed = salesmod.list_invoices(db, sid)
    assert listed[0]["invoice_date"] == "2026-02-03"


def test_batch_stores_each_entries_invoice_date(db):
    co = _company(db)
    sid = _sale(db, co)
    si = _sale_item_id(db, sid)
    out = salesmod.create_invoices_batch(db, sid, [
        {"invoice_number": f"INV-A-{_tag()}", "invoice_date": "2026-03-01",
         "lines": [_line(si, 1)]},
        {"invoice_number": f"INV-B-{_tag()}", "invoice_date": "2026-03-02",
         "lines": [_line(si, 1)]},
    ])
    assert [i["invoice_date"] for i in out["invoices"]] == [
        "2026-03-01", "2026-03-02"]
    listed = {r["invoice_number"]: r["invoice_date"]
              for r in salesmod.list_invoices(db, sid)}
    assert listed == {out["invoices"][0]["invoice_number"]: "2026-03-01",
                      out["invoices"][1]["invoice_number"]: "2026-03-02"}


# --------------------------------------------------------------------------- #
# 4. the API models require the date and at least one line
# --------------------------------------------------------------------------- #
def test_api_create_invoice_requires_invoice_date(admin_client, db):
    cid = _api_company(admin_client, db)
    sid = _api_sale(admin_client, cid)
    si = _sale_item_id(db, sid)
    r = admin_client.post(f"/api/sales/{sid}/invoices", json={
        "invoice_number": "INV-API-ND", "lines": [_line(si, 1)]})
    assert r.status_code == 400, r.get_json()
    assert "invoice_date" in [d["field"] for d in r.get_json()["details"]]


def test_api_create_invoice_requires_lines(admin_client, db):
    cid = _api_company(admin_client, db)
    sid = _api_sale(admin_client, cid)
    r = admin_client.post(f"/api/sales/{sid}/invoices", json={
        "invoice_number": "INV-API-NL", "invoice_date": INVOICE_DATE})
    assert r.status_code == 400, r.get_json()
    assert "lines" in [d["field"] for d in r.get_json()["details"]]


def test_api_create_invoice_stores_date(admin_client, db):
    cid = _api_company(admin_client, db)
    sid = _api_sale(admin_client, cid)
    si = _sale_item_id(db, sid)
    r = admin_client.post(f"/api/sales/{sid}/invoices", json={
        "invoice_number": "INV-API-OK", "invoice_date": INVOICE_DATE,
        "lines": [_line(si, 1)]})
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["invoice_date"] == INVOICE_DATE
    rows = admin_client.get(f"/api/sales/{sid}/invoices").get_json()
    assert rows[0]["invoice_date"] == INVOICE_DATE


# --------------------------------------------------------------------------- #
# 5. schema version 6 carries the column
# --------------------------------------------------------------------------- #
def test_schema_v6_has_invoices_invoice_date(db):
    assert _SCHEMA_VERSION == 6, "DDL changed: bump the version"
    assert "invoice_date" in _table_columns(db, "invoices")
