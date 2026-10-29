"""Recipe sourcing: running LCs, producible PIs and invoices.

The Create New Recipe modal must not offer LCs that are done (every PI
completed) and must not offer invoices that are past production. These tests
pin the service contract behind ``open_only`` / ``producible``.

Stage truth lives on the child sales: LC completion is derived and lags
(``_sync_lc_completion`` runs only on sale moves), so an LC can be stamped
``lc_received`` with every PI completed, or ``completed`` with a late-attached
open PI. ``open_only`` follows the children, never the LC stage.
"""
import psycopg  # noqa: F401  (import guard, same as test_lcs.py)

from raas_tracker import lcs as lcsmod
from raas_tracker import sales as salesmod


def _company(conn, name):
    row = conn.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id", (name,)
    ).fetchone()
    conn.commit()
    return row[0]


def _sale(conn, company_id, pi, product="Prod", qty=10, price=5):
    return salesmod.add_sale(
        conn,
        {"pi_number": pi, "client_name": "Buyer", "company_id": company_id},
        [{"product_name": product, "quantity": qty, "unit_price": price}],
    )


def _invoice(conn, sale_id, number):
    lines = [
        {"sale_item_id": r[0], "quantity": float(r[1])}
        for r in conn.execute(
            "SELECT id, quantity FROM sale_items WHERE sale_id = %s ORDER BY id",
            (sale_id,)).fetchall()
    ]
    return salesmod.create_invoice(conn, sale_id, number, lines=lines,
                                   invoice_date="2026-01-15")["invoice_id"]


def _lc(conn, company_id, number, sale_ids=()):
    lc = lcsmod.create_lc(conn, company_id, number)
    if sale_ids:
        lcsmod.attach_pis(conn, lc["id"], list(sale_ids))
    return lc


def _set_sale_stage(conn, sale_id, stage):
    conn.execute("UPDATE sales SET stage = %s WHERE id = %s", (stage, sale_id))
    conn.commit()


def _set_lc_stage(conn, lc_id, stage):
    conn.execute("UPDATE letters_of_credit SET stage = %s WHERE id = %s",
                 (stage, lc_id))
    conn.commit()


def _set_invoice_status(conn, invoice_id, status):
    conn.execute("UPDATE invoices SET status = %s WHERE id = %s",
                 (status, invoice_id))
    conn.commit()


# --------------------------------------------------------------------------- #
# list_lcs
# --------------------------------------------------------------------------- #
def test_list_lcs_default_still_lists_completed_and_keeps_pi_count(db):
    co = _company(db, "Src Default Co")
    s_open = _sale(db, co, "PI-DEF-1")
    s_done = _sale(db, co, "PI-DEF-2")
    _set_sale_stage(db, s_done, "completed")
    lc_open = _lc(db, co, "LC-DEF-OPEN", [s_open])
    lc_done = _lc(db, co, "LC-DEF-DONE", [s_done])
    _set_lc_stage(db, lc_done["id"], "completed")

    rows = lcsmod.list_lcs(db, co)
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {lc_open["id"], lc_done["id"]}
    assert by_id[lc_open["id"]]["pi_count"] == 1
    assert by_id[lc_done["id"]]["pi_count"] == 1


def test_open_only_drops_lc_whose_children_are_all_completed(db):
    """A stale ``lc_received`` LC with every PI paid is closed in practice."""
    co = _company(db, "Src Stale Co")
    s = _sale(db, co, "PI-STALE-1")
    lc = _lc(db, co, "LC-STALE", [s])
    _set_sale_stage(db, s, "completed")
    assert lcsmod.get_lc(db, lc["id"])["stage"] == "lc_received"

    assert lcsmod.list_lcs(db, co, open_only=True) == []


def test_open_only_keeps_completed_lc_with_a_late_open_child(db):
    """Late attach can leave an open PI under a completed LC; work is real."""
    co = _company(db, "Src Late Co")
    s1 = _sale(db, co, "PI-LATE-1")
    s2 = _sale(db, co, "PI-LATE-2")
    lc = _lc(db, co, "LC-LATE", [s1, s2])
    _set_lc_stage(db, lc["id"], "completed")
    _set_sale_stage(db, s1, "completed")

    rows = lcsmod.list_lcs(db, co, open_only=True)
    assert [r["id"] for r in rows] == [lc["id"]]
    assert rows[0]["open_pi_count"] == 1


def test_open_only_excludes_lc_without_pis(db):
    co = _company(db, "Src Empty Co")
    _lc(db, co, "LC-EMPTY")
    assert lcsmod.list_lcs(db, co, open_only=True) == []


def test_open_only_counts_open_and_producible_pis(db):
    co = _company(db, "Src Count Co")
    s_producible = _sale(db, co, "PI-COUNT-1")
    _invoice(db, s_producible, "INV-COUNT-1")
    s_booked = _sale(db, co, "PI-COUNT-2")
    inv_booked = _invoice(db, s_booked, "INV-COUNT-2")
    _set_invoice_status(db, inv_booked, "booked")
    s_done = _sale(db, co, "PI-COUNT-3")
    _set_sale_stage(db, s_done, "completed")
    lc = _lc(db, co, "LC-COUNT", [s_producible, s_booked, s_done])

    row = {r["id"]: r for r in lcsmod.list_lcs(db, co, open_only=True)}[lc["id"]]
    assert row["pi_count"] == 3
    assert row["open_pi_count"] == 2
    assert row["producible_pi_count"] == 1


# --------------------------------------------------------------------------- #
# get_lc
# --------------------------------------------------------------------------- #
def test_get_lc_pis_carry_client_and_producible_count(db):
    co = _company(db, "Src Detail Co")
    s_due = _sale(db, co, "PI-DET-1")
    _invoice(db, s_due, "INV-DET-1")
    s_closed = _sale(db, co, "PI-DET-2")
    inv_closed = _invoice(db, s_closed, "INV-DET-2")
    _set_invoice_status(db, inv_closed, "paid")
    lc = _lc(db, co, "LC-DET", [s_due, s_closed])

    pis = {p["id"]: p for p in lcsmod.get_lc(db, lc["id"])["pis"]}
    assert pis[s_due]["client_name"] == "Buyer"
    assert pis[s_due]["company_id"] == co
    assert pis[s_due]["producible_invoice_count"] == 1
    assert pis[s_closed]["producible_invoice_count"] == 0


def test_get_lc_open_only_drops_completed_but_keeps_produced_out_pis(db):
    co = _company(db, "Src Open Detail Co")
    s_done = _sale(db, co, "PI-OD-1")
    _set_sale_stage(db, s_done, "completed")
    s_no_work = _sale(db, co, "PI-OD-2")
    inv = _invoice(db, s_no_work, "INV-OD-2")
    _set_invoice_status(db, inv, "booked")
    s_open = _sale(db, co, "PI-OD-3")
    _invoice(db, s_open, "INV-OD-3")
    lc = _lc(db, co, "LC-OD", [s_done, s_no_work, s_open])

    pis = {p["id"]: p
           for p in lcsmod.get_lc(db, lc["id"], open_only=True)["pis"]}
    assert set(pis) == {s_no_work, s_open}
    assert pis[s_no_work]["producible_invoice_count"] == 0


def test_get_lc_default_shape_unchanged(db):
    """Adding keys must not remove or rename the legacy ones."""
    co = _company(db, "Src Shape Co")
    s = _sale(db, co, "PI-SHAPE-1")
    lc = _lc(db, co, "LC-SHAPE", [s])
    pi = lcsmod.get_lc(db, lc["id"])["pis"][0]
    assert {"id", "pi_number", "stage"} <= set(pi)


# --------------------------------------------------------------------------- #
# list_invoices
# --------------------------------------------------------------------------- #
def test_list_invoices_producible_only_returns_planned_and_produced(db):
    co = _company(db, "Src Inv Co")
    s = _sale(db, co, "PI-INV-1")
    inv_planned = _invoice(db, s, "INV-PLANNED")
    inv_produced = _invoice(db, s, "INV-PRODUCED")
    inv_booked = _invoice(db, s, "INV-BOOKED")
    _set_invoice_status(db, inv_produced, "produced")
    _set_invoice_status(db, inv_booked, "booked")

    kept = salesmod.list_invoices(db, s, producible_only=True)
    assert {i["invoice_id"] for i in kept} == {inv_planned, inv_produced}

    every = salesmod.list_invoices(db, s)
    assert {i["invoice_id"] for i in every} == {
        inv_planned, inv_produced, inv_booked}


def test_list_invoices_default_still_readiness_aware(db):
    co = _company(db, "Src Inv Ready Co")
    s = _sale(db, co, "PI-INV-2")
    _invoice(db, s, "INV-READY-1")
    rows = salesmod.list_invoices(db, s, with_readiness=True)
    assert rows and "can_book" in rows[0]


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #
def test_api_lcs_open_only_param(admin_client, db):
    co = _company(db, "Src Route Co")
    s_open = _sale(db, co, "PI-RT-1")
    s_done = _sale(db, co, "PI-RT-2")
    _set_sale_stage(db, s_done, "completed")
    lc_open = _lc(db, co, "LC-RT-OPEN", [s_open])
    lc_done = _lc(db, co, "LC-RT-DONE", [s_done])

    body = admin_client.get(
        f"/api/lcs?company_id={co}&open_only=true").get_json()
    assert [r["id"] for r in body] == [lc_open["id"]]
    assert body[0]["producible_pi_count"] == 0
    assert "pi_count" in body[0]

    every = admin_client.get(f"/api/lcs?company_id={co}").get_json()
    assert {r["id"] for r in every} == {lc_open["id"], lc_done["id"]}


def test_api_lcs_invalid_company_id_still_400(admin_client):
    r = admin_client.get("/api/lcs?company_id=abc")
    assert r.status_code == 400


def test_api_get_lc_open_only_param(admin_client, db):
    co = _company(db, "Src Route Detail Co")
    s_done = _sale(db, co, "PI-RD-1")
    _set_sale_stage(db, s_done, "completed")
    s_open = _sale(db, co, "PI-RD-2")
    lc = _lc(db, co, "LC-RD", [s_done, s_open])

    body = admin_client.get(f"/api/lcs/{lc['id']}?open_only=true").get_json()
    assert [p["id"] for p in body["pis"]] == [s_open]
    assert body["pis"][0]["producible_invoice_count"] == 0

    every = admin_client.get(f"/api/lcs/{lc['id']}").get_json()
    assert {p["id"] for p in every["pis"]} == {s_done, s_open}


def test_api_list_invoices_producible_param(admin_client, db):
    co = _company(db, "Src Route Inv Co")
    s = _sale(db, co, "PI-RI-1")
    _invoice(db, s, "INV-RI-1")
    inv_booked = _invoice(db, s, "INV-RI-2")
    _set_invoice_status(db, inv_booked, "booked")

    body = admin_client.get(
        f"/api/sales/{s}/invoices?producible=true").get_json()
    assert [i["invoice_number"] for i in body] == ["INV-RI-1"]

    every = admin_client.get(f"/api/sales/{s}/invoices").get_json()
    assert len(every) == 2
