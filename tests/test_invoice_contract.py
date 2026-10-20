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
from raas_tracker._exceptions import ServiceError, ToleranceExceeded
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


# --------------------------------------------------------------------------- #
# 6. PI-line budget endpoint (Phase 3)
#
# The composer needs one row per PI line with what is already invoiced against
# it, so the N+1 (`/api/sales/<id>` then `/api/invoices/<id>/items` per invoice)
# can go. The numbers must match the writer exactly: `remaining` is what the UI
# offers, `ceiling` is what `check_invoice_tolerance` will accept.
# --------------------------------------------------------------------------- #
def test_sale_invoice_lines_reports_remaining_and_ceiling(db):
    co = _company(db)
    sid = salesmod.add_sale(
        db,
        {"pi_number": f"PI-{_tag()}", "client_name": "x", "company_id": co},
        [{"product_name": "Alpha", "quantity": 100, "unit_price": 2, "unit": "KG"},
         {"product_name": "Beta", "quantity": 10, "unit_price": 7, "unit": "L"}],
    )
    items = db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id", (sid,)
    ).fetchall()
    alpha_id, beta_id = items[0][0], items[1][0]
    inv = salesmod.create_invoice(db, sid, f"INV-{_tag()}",
                                  lines=[_line(alpha_id, 40)],
                                  invoice_date=INVOICE_DATE)

    out = salesmod.list_sale_invoice_lines(db, sid)
    assert out["has_line_less_invoices"] is False
    assert [line["product_name"] for line in out["lines"]] == ["Alpha", "Beta"]

    alpha, beta = out["lines"]
    assert alpha == {
        "sale_id": sid, "sale_item_id": alpha_id, "product_name": "Alpha",
        "unit": "KG", "pi_quantity": 100.0, "invoiced_quantity": 40.0,
        "remaining": 60.0, "ceiling": 105.0, "unit_price": 2.0,
        "invoice_ids": [inv["invoice_id"]],
    }
    assert beta["sale_item_id"] == beta_id
    assert beta["unit"] == "L"
    assert beta["invoiced_quantity"] == 0.0
    assert beta["remaining"] == 10.0
    assert beta["ceiling"] == pytest.approx(10.5)
    assert beta["invoice_ids"] == []


def test_sale_invoice_lines_keeps_a_fully_invoiced_line(db):
    """`remaining` floors at 0 but the line is never filtered out."""
    co = _company(db)
    sid = _sale(db, co, qty=5)
    si = _sale_item_id(db, sid)
    salesmod.create_invoice(db, sid, f"INV-{_tag()}", lines=[_line(si, 5)],
                            invoice_date=INVOICE_DATE)

    lines = salesmod.list_sale_invoice_lines(db, sid)["lines"]
    assert len(lines) == 1
    assert lines[0]["invoiced_quantity"] == 5.0
    assert lines[0]["remaining"] == 0.0


def test_sale_invoice_lines_defaults_a_blank_unit_to_kg(db):
    co = _company(db)
    sid = _sale(db, co)
    db.execute(
        "INSERT INTO sale_items (sale_id, product_name, quantity, unit_price, unit) "
        "VALUES (%s, 'NoUnit', 3, 1, '')", (sid,))
    db.commit()

    by_name = {line["product_name"]: line
               for line in salesmod.list_sale_invoice_lines(db, sid)["lines"]}
    assert by_name["NoUnit"]["unit"] == "KG"


def test_sale_invoice_lines_flags_a_line_less_legacy_invoice(db):
    """Header-only money is real but invisible to quantity: the flag says so."""
    co = _company(db)
    sid = _sale(db, co)
    db.execute(
        "INSERT INTO invoices (sale_id, invoice_number, status, amount) "
        "VALUES (%s, %s, 'planned', 50)", (sid, f"LEG-{_tag()}"))
    db.commit()

    out = salesmod.list_sale_invoice_lines(db, sid)
    assert out["has_line_less_invoices"] is True
    assert out["lines"][0]["invoiced_quantity"] == 0.0


def test_sale_invoice_lines_empty_for_sale_without_pi_lines(db):
    co = _company(db)
    sid = salesmod.add_sale(
        db, {"pi_number": f"PI-{_tag()}", "client_name": "x",
             "company_id": co}, [])
    assert salesmod.list_sale_invoice_lines(db, sid) == {
        "lines": [], "has_line_less_invoices": False}


def test_api_sale_invoice_lines_returns_the_budget(admin_client, db):
    cid = _api_company(admin_client, db)
    sid = _api_sale(admin_client, cid)
    si = _sale_item_id(db, sid)
    created = admin_client.post(f"/api/sales/{sid}/invoices", json={
        "invoice_number": f"INV-{_tag()}", "invoice_date": INVOICE_DATE,
        "lines": [_line(si, 4)]})
    assert created.status_code == 201, created.get_json()

    r = admin_client.get(f"/api/sales/{sid}/invoice-lines")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["has_line_less_invoices"] is False
    line = body["lines"][0]
    assert line["sale_item_id"] == si
    assert line["pi_quantity"] == 10.0
    assert line["invoiced_quantity"] == 4.0
    assert line["remaining"] == 6.0
    assert line["ceiling"] == pytest.approx(10.5)


def test_api_sale_invoice_lines_404s_for_an_unknown_sale(admin_client):
    r = admin_client.get("/api/sales/99999999/invoice-lines")
    assert r.status_code == 404, r.get_json()


def test_api_sale_invoice_lines_requires_auth(client):
    r = client.get("/api/sales/1/invoice-lines")
    assert r.status_code == 401, r.get_json()


# --------------------------------------------------------------------------- #
# 7. server-owned price, audit shape, and endpoint/writer ceiling parity
# --------------------------------------------------------------------------- #
def test_create_invoice_ignores_a_forged_unit_price(admin_client, db):
    """The price is resolved from the PI line, so a client-stated price is inert.

    ``InvoiceBatchLineIn`` declares only ``sale_item_id`` and ``quantity`` and
    Pydantic's default is to ignore extra keys, so the forged fields never reach
    the service. This pins the outcome (201 with the PI price, not a 400 and not
    the forged number) so adding ``extra="forbid"`` or mapping a client price
    through would both have to be a deliberate, test-visible change.
    """
    cid = _api_company(admin_client, db)
    sid = _api_sale(admin_client, cid)          # 10 x 5.0
    si = _sale_item_id(db, sid)

    r = admin_client.post(f"/api/sales/{sid}/invoices", json={
        "invoice_number": f"INV-{_tag()}", "invoice_date": INVOICE_DATE,
        "lines": [{"sale_item_id": si, "quantity": 2,
                   "unit_price": 0.01, "line_total": 0.01}]})
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["amount"] == 10.0

    price, total = db.execute(
        "SELECT unit_price, line_total FROM invoice_items WHERE invoice_id = %s",
        (r.get_json()["invoice_id"],)).fetchone()
    assert float(price) == 5.0
    assert float(total) == 10.0


def test_validate_invoice_lines_ignores_a_forged_unit_price(db):
    """Service-level twin: even a dict carrying ``unit_price`` is rebuilt from the PI."""
    co = _company(db)
    sid = _sale(db, co, product="ForgedPrice", qty=10, price=7.5)
    si = _sale_item_id(db, sid)

    validated = salesmod.validate_invoice_lines(db, sid, [{
        "sale_item_id": si, "quantity": 2,
        "unit_price": 0.01, "line_total": 0.01}])

    assert validated[0]["unit_price"] == 7.5
    assert validated[0]["line_total"] == 15.0


def test_endpoint_ceiling_is_exactly_what_the_writer_accepts(db):
    """The 6 dp ceiling the UI offers must be a quantity the writer takes.

    ``list_sale_invoice_lines`` and ``check_invoice_tolerance`` compute the
    ceiling with the same ``Decimal(str(...)) * 1.05`` expression precisely so
    this boundary holds; a float-converted ceiling that rounded the wrong way
    would make the UI refuse a legal invoice or offer an illegal one.
    """
    co = _company(db)
    sid = _sale(db, co, product="CeilingParity", qty=3.7, price=2)
    si = _sale_item_id(db, sid)

    ceiling = salesmod.list_sale_invoice_lines(db, sid)["lines"][0]["ceiling"]
    assert ceiling == 3.885

    created = salesmod.create_invoice(
        db, sid, f"INV-{_tag()}", lines=[_line(si, ceiling)],
        invoice_date=INVOICE_DATE)
    assert created["status"] == "planned"

    with pytest.raises(ToleranceExceeded):
        salesmod.create_invoice_item(db, created["invoice_id"], si, 0.000001)

    done = salesmod.list_sale_invoice_lines(db, sid)["lines"][0]
    assert done["invoiced_quantity"] == pytest.approx(3.885)
    assert done["remaining"] == 0.0


def test_create_invoice_audits_each_line_then_the_header(admin_client, db,
                                                         monkeypatch):
    """One ``INVOICE_CREATE`` per invoice, one ``INVOICE_ITEM_ADD`` per line, header last."""
    cid = _api_company(admin_client, db)
    sid = _api_sale(admin_client, cid)
    si = _sale_item_id(db, sid)
    seen = []
    real = salesmod.log_audit_action

    def spy(conn, action, *args, **kwargs):
        seen.append(action)
        return real(conn, action, *args, **kwargs)

    monkeypatch.setattr(salesmod, "log_audit_action", spy)
    r = admin_client.post(f"/api/sales/{sid}/invoices", json={
        "invoice_number": f"INV-{_tag()}", "invoice_date": INVOICE_DATE,
        "lines": [_line(si, 2), _line(si, 1)]})
    assert r.status_code == 201, r.get_json()

    assert seen.count("INVOICE_ITEM_ADD") == 2, seen
    assert seen.count("INVOICE_CREATE") == 1, seen
    assert seen[-1] == "INVOICE_CREATE", (
        f"the header audit must follow its lines: {seen}")


def test_create_invoice_refuses_a_non_finite_pi_total_before_writing(db):
    """The ``remaining_invoice_money`` guard turns bad PI data into a 400, not a 500.

    A NaN ``sale_items.quantity`` poisons the sale total. The guard runs before
    ``validate_invoice_lines``, so it fires even when the invoiced line itself is
    healthy - removing the call would let the invoice through and defer the
    blow-up to the next money read.
    """
    co = _company(db)
    sid = _sale(db, co, product="NaNProd", qty=10, price=5)
    si = _sale_item_id(db, sid)
    db.execute("UPDATE sale_items SET quantity = 'NaN'::real WHERE id = %s", (si,))
    db.commit()

    with pytest.raises(ValueError, match="non-finite"):
        salesmod.create_invoice(db, sid, f"INV-{_tag()}", lines=[_line(si, 1)],
                                invoice_date=INVOICE_DATE)

    db.rollback()
    assert db.execute("SELECT COUNT(*) FROM invoices WHERE sale_id = %s",
                      (sid,)).fetchone()[0] == 0
