"""Phase 2 lane A (domain services): LC CRUD, attach/detach, stage propagation.

TDD RED first: imports `raas_tracker.lcs` (missing) and exercises the exact
contract lane B codes routes against.
"""
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from raas_tracker import lcs as lcsmod  # noqa: E402
from raas_tracker import sales as salesmod  # noqa: E402


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def _company(conn, name=None):
    name = name or f"LC Co {_tag()}"
    row = conn.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id", (name,)
    ).fetchone()
    conn.commit()
    return row[0]


def _sale(conn, company_id, pi=None, product="Prod", qty=10, price=5):
    sale_id = salesmod.add_sale(
        conn,
        {"pi_number": pi or f"PI-{_tag()}", "client_name": "x",
         "company_id": company_id},
        [{"product_name": product, "quantity": qty, "unit_price": price}],
    )
    return sale_id


# --------------------------------------------------------------------------- #
# create_lc
# --------------------------------------------------------------------------- #
def test_create_lc_trims_and_returns_dict(db):
    co = _company(db)
    lc = lcsmod.create_lc(db, co, "  LC-TRIM-1  ", lc_date="2026-01-05")
    assert lc["lc_number"] == "LC-TRIM-1"
    assert lc["company_id"] == co
    assert lc["id"]
    # persisted trimmed
    row = db.execute(
        "SELECT lc_number FROM letters_of_credit WHERE id = %s", (lc["id"],)
    ).fetchone()
    assert row[0] == "LC-TRIM-1"


def test_create_lc_rejects_blank(db):
    co = _company(db)
    for bad in ("", "   ", None):
        with pytest.raises(ValueError):
            lcsmod.create_lc(db, co, bad)
    db.rollback()


def test_create_lc_trim_dedupe(db):
    co = _company(db)
    tag = _tag()
    lcsmod.create_lc(db, co, f"  LC-DD-{tag}  ")
    with pytest.raises(ValueError):
        lcsmod.create_lc(db, co, f"LC-DD-{tag}")
    db.rollback()
    with pytest.raises(ValueError):
        lcsmod.create_lc(db, co, f"  LC-DD-{tag} ")
    db.rollback()


def test_create_lc_unique_scoped_per_company(db):
    tag = _tag()
    co1 = _company(db, f"LC Sc A {tag}")
    co2 = _company(db, f"LC Sc B {tag}")
    lcsmod.create_lc(db, co1, f"LC-SC-{tag}")
    # same number, different company -> OK
    other = lcsmod.create_lc(db, co2, f"LC-SC-{tag}")
    assert other["company_id"] == co2
    # same company duplicate -> ValueError
    with pytest.raises(ValueError):
        lcsmod.create_lc(db, co1, f"LC-SC-{tag}")
    db.rollback()


def test_create_lc_audits_lc_create(db):
    co = _company(db)
    tag = _tag()
    lc = lcsmod.create_lc(db, co, f"LC-AUD-{tag}")
    row = db.execute(
        "SELECT action FROM audit_logs WHERE entity_type = 'lc' AND entity_id = %s"
        " ORDER BY id DESC LIMIT 1",
        (lc["id"],),
    ).fetchone()
    assert row is not None and row[0] == "LC_CREATE"


# --------------------------------------------------------------------------- #
# get_lc / list_lcs
# --------------------------------------------------------------------------- #
def test_get_lc_returns_pis(db):
    co = _company(db)
    lc = lcsmod.create_lc(db, co, f"LC-GET-{_tag()}")
    s1 = _sale(db, co, product="P1")
    s2 = _sale(db, co, product="P2")
    lcsmod.attach_pis(db, lc["id"], [s1, s2])
    got = lcsmod.get_lc(db, lc["id"])
    assert got["id"] == lc["id"]
    assert got["lc_number"] == lc["lc_number"]
    pis = {p["id"]: p for p in got["pis"]}
    assert set(pis) == {s1, s2}
    assert pis[s1]["pi_number"]
    assert pis[s1]["stage"]


def test_get_lc_missing_returns_none(db):
    assert lcsmod.get_lc(db, 999999999) is None


def test_list_lcs_scoped(db):
    tag = _tag()
    co1 = _company(db, f"LC L A {tag}")
    co2 = _company(db, f"LC L B {tag}")
    lcsmod.create_lc(db, co1, f"LC-L1-{tag}")
    lcsmod.create_lc(db, co2, f"LC-L2-{tag}")
    all_lcs = lcsmod.list_lcs(db)
    assert {l["lc_number"] for l in all_lcs} >= {f"LC-L1-{tag}", f"LC-L2-{tag}"}
    only1 = lcsmod.list_lcs(db, co1)
    assert {l["lc_number"] for l in only1} == {f"LC-L1-{tag}"}
    only2 = lcsmod.list_lcs(db, co2)
    assert {l["lc_number"] for l in only2} == {f"LC-L2-{tag}"}


# --------------------------------------------------------------------------- #
# attach / detach
# --------------------------------------------------------------------------- #
def test_attach_pis_mirrors_and_audits(db):
    co = _company(db)
    tag = _tag()
    lc = lcsmod.create_lc(db, co, f"LC-AT-{tag}", lc_date="2026-02-01")
    s1 = _sale(db, co)
    out = lcsmod.attach_pis(db, lc["id"], [s1])
    assert {p["id"] for p in out["pis"]} == {s1}
    row = db.execute(
        "SELECT lc_id, lc_number, lc_date FROM sales WHERE id = %s", (s1,)
    ).fetchone()
    assert row[0] == lc["id"]
    assert row[1] == f"LC-AT-{tag}"
    assert row[2] == "2026-02-01"
    audits = db.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE action = 'LC_ATTACH'"
    ).fetchone()[0]
    assert audits >= 1


def test_attach_rejects_company_mismatch_no_partial(db):
    tag = _tag()
    co1 = _company(db, f"LC M A {tag}")
    co2 = _company(db, f"LC M B {tag}")
    lc = lcsmod.create_lc(db, co1, f"LC-MM-{tag}")
    good = _sale(db, co1)
    bad = _sale(db, co2)
    with pytest.raises(ValueError):
        lcsmod.attach_pis(db, lc["id"], [good, bad])
    db.rollback()
    # one txn: neither sale linked
    assert db.execute(
        "SELECT lc_id FROM sales WHERE id = %s", (good,)
    ).fetchone()[0] is None
    assert db.execute(
        "SELECT lc_id FROM sales WHERE id = %s", (bad,)
    ).fetchone()[0] is None


def test_detach_clears_mirrors(db):
    co = _company(db)
    lc = lcsmod.create_lc(db, co, f"LC-DT-{_tag()}", lc_date="2026-03-01")
    s1 = _sale(db, co)
    lcsmod.attach_pis(db, lc["id"], [s1])
    assert lcsmod.detach_pi(db, lc["id"], s1) is True
    row = db.execute(
        "SELECT lc_id, lc_number, lc_date FROM sales WHERE id = %s", (s1,)
    ).fetchone()
    assert row[0] is None
    assert row[1] is None
    assert row[2] is None
    # second detach: not linked anymore
    assert lcsmod.detach_pi(db, lc["id"], s1) is False


def test_detach_unlinked_returns_false(db):
    co = _company(db)
    lc = lcsmod.create_lc(db, co, f"LC-DU-{_tag()}")
    s1 = _sale(db, co)
    assert lcsmod.detach_pi(db, lc["id"], s1) is False
    assert lcsmod.detach_pi(db, lc["id"], 999999999) is False


# --------------------------------------------------------------------------- #
# move_lc_stage
# --------------------------------------------------------------------------- #
def test_move_lc_stage_propagates(db):
    co = _company(db)
    lc = lcsmod.create_lc(db, co, f"LC-MV-{_tag()}")
    s1 = _sale(db, co, product="P1")
    s2 = _sale(db, co, product="P2")
    lcsmod.attach_pis(db, lc["id"], [s1, s2])
    for prod in ("P1", "P2"):
        db.execute(
            "INSERT INTO recipes (name, company_id, product_name)"
            " VALUES (%s, %s, %s)",
            (f"R-MV-{prod}-{_tag()}", co, prod),
        )
    db.commit()
    salesmod.create_invoice(db, s1, f"INV-MV-{_tag()}")
    assert lcsmod.move_lc_stage(db, lc["id"], "shipment_ongoing", notes="go") is True
    assert lcsmod.get_lc(db, lc["id"])["stage"] == "shipment_ongoing"
    for sid in (s1, s2):
        assert db.execute(
            "SELECT stage FROM sales WHERE id = %s", (sid,)
        ).fetchone()[0] == "shipment_ongoing"
        hist = db.execute(
            "SELECT COUNT(*) FROM sales_stage_history WHERE sale_id = %s"
            " AND to_stage = %s",
            (sid, "shipment_ongoing"),
        ).fetchone()[0]
        assert hist >= 1
        audit = db.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action = 'SALE_MOVE'"
            " AND entity_id = %s",
            (sid,),
        ).fetchone()[0]
        assert audit >= 1


def test_move_lc_stage_sequential_double_move(db):
    """Concurrent-ish: two moves in sequence, last write wins, history kept."""
    co = _company(db)
    lc = lcsmod.create_lc(db, co, f"LC-2MV-{_tag()}")
    s1 = _sale(db, co, product="PSeq")
    lcsmod.attach_pis(db, lc["id"], [s1])
    db.execute(
        "INSERT INTO recipes (name, company_id, product_name)"
        " VALUES (%s, %s, %s)",
        (f"R-SEQ-{_tag()}", co, "PSeq"),
    )
    db.commit()
    salesmod.create_invoice(db, s1, f"INV-SEQ-{_tag()}")
    assert lcsmod.move_lc_stage(db, lc["id"], "shipment_ongoing") is True
    assert lcsmod.move_lc_stage(db, lc["id"], "payment_due") is True
    assert lcsmod.get_lc(db, lc["id"])["stage"] == "payment_due"
    assert db.execute(
        "SELECT stage FROM sales WHERE id = %s", (s1,)
    ).fetchone()[0] == "payment_due"
    stages = [
        r[0]
        for r in db.execute(
            "SELECT to_stage FROM sales_stage_history WHERE sale_id = %s"
            " ORDER BY id",
            (s1,),
        ).fetchall()
    ]
    assert "shipment_ongoing" in stages and "payment_due" in stages


def test_move_lc_stage_invalid_raises(db):
    co = _company(db)
    lc = lcsmod.create_lc(db, co, f"LC-BAD-{_tag()}")
    with pytest.raises(ValueError):
        lcsmod.move_lc_stage(db, lc["id"], "nope_stage")
    db.rollback()


def test_move_lc_stage_missing_returns_false(db):
    assert lcsmod.move_lc_stage(db, 999999999, "shipment_ongoing") is False


# --------------------------------------------------------------------------- #
# Finding-5: company change clears lc_id
# --------------------------------------------------------------------------- #
def test_company_change_clears_lc_link(db):
    tag = _tag()
    co1 = _company(db, f"LC R A {tag}")
    co2 = _company(db, f"LC R B {tag}")
    lc = lcsmod.create_lc(db, co1, f"LC-RL-{tag}")
    sid = _sale(db, co1)
    lcsmod.attach_pis(db, lc["id"], [sid])
    assert db.execute(
        "SELECT lc_id FROM sales WHERE id = %s", (sid,)
    ).fetchone()[0] == lc["id"]
    sale = salesmod.get_sale_by_id(db, sid)
    items = [
        {"id": it["id"], "product_name": it["product_name"],
         "quantity": it["quantity"], "unit_price": it["unit_price"],
         "unit": it.get("unit") or "KG"}
        for it in sale["items"]
    ]
    salesmod.update_sale_full(
        db, sid,
        {"pi_number": sale["pi_number"], "company_id": co2},
        items, [],
    )
    assert db.execute(
        "SELECT company_id, lc_id FROM sales WHERE id = %s", (sid,)
    ).fetchone() == (co2, None)


# --------------------------------------------------------------------------- #
# Oracle gate 2 remediation (Phase 2): B1/B2/B4+B6/B5+M2
# --------------------------------------------------------------------------- #
def test_attach_order_independent(db):
    """B1: attach [a,b] vs [b,a] links the same set (sorted lock order)."""
    co = _company(db)
    lc = lcsmod.create_lc(db, co, f"LC-ORD-{_tag()}")
    s1 = _sale(db, co)
    s2 = _sale(db, co)
    out = lcsmod.attach_pis(db, lc["id"], [s2, s1])
    assert {p["id"] for p in out["pis"]} == {s1, s2}
    # reverse order on a second LC links identically
    lc2 = lcsmod.create_lc(db, co, f"LC-ORD2-{_tag()}")
    s3 = _sale(db, co)
    s4 = _sale(db, co)
    out2 = lcsmod.attach_pis(db, lc2["id"], [s4, s3])
    assert {p["id"] for p in out2["pis"]} == {s3, s4}
    # dupes are deduped
    lc3 = lcsmod.create_lc(db, co, f"LC-ORD3-{_tag()}")
    s5 = _sale(db, co)
    out3 = lcsmod.attach_pis(db, lc3["id"], [s5, s5, s5])
    assert {p["id"] for p in out3["pis"]} == {s5}


def test_detach_already_detached_returns_false(db):
    """B2: detach of an already-detached sale returns False (one txn)."""
    co = _company(db)
    lc = lcsmod.create_lc(db, co, f"LC-DT2-{_tag()}")
    s1 = _sale(db, co)
    lcsmod.attach_pis(db, lc["id"], [s1])
    assert lcsmod.detach_pi(db, lc["id"], s1) is True
    assert lcsmod.detach_pi(db, lc["id"], s1) is False
    assert lcsmod.detach_pi(db, lc["id"], 999999999) is False


def _lc_ready_setup(db, co, product, with_recipe=True, with_invoice=True):
    lc = lcsmod.create_lc(db, co, f"LC-GT-{_tag()}")
    sid = _sale(db, co, product=product)
    lcsmod.attach_pis(db, lc["id"], [sid])
    if with_recipe:
        db.execute(
            "INSERT INTO recipes (name, company_id, product_name)"
            " VALUES (%s, %s, %s)",
            (f"R-{_tag()}", co, product),
        )
        db.commit()
    if with_invoice:
        salesmod.create_invoice(db, sid, f"INV-{_tag()}")
    return lc["id"], sid


def test_move_lc_stage_gate_unready_raises(db):
    """B4+B6: lc_received -> shipment_ongoing unready raises ValueError."""
    co = _company(db)
    tag = _tag()
    lc_id, _ = _lc_ready_setup(db, co, f"GateUn-{tag}",
                               with_recipe=False, with_invoice=False)
    with pytest.raises(ValueError, match="recipes:"):
        lcsmod.move_lc_stage(db, lc_id, "shipment_ongoing")
    db.rollback()
    with pytest.raises(ValueError, match="invoices:"):
        lcsmod.move_lc_stage(db, lc_id, "shipment_ongoing")
    db.rollback()
    # stage must not have moved
    assert lcsmod.get_lc(db, lc_id)["stage"] == "lc_received"


def test_move_lc_stage_gate_ready_passes(db):
    """B4+B6: ready LC moves to shipment_ongoing."""
    co = _company(db)
    tag = _tag()
    prod = f"GateOk-{tag}"
    lc_id, sid = _lc_ready_setup(db, co, prod,
                                 with_recipe=True, with_invoice=True)
    assert lcsmod.move_lc_stage(db, lc_id, "shipment_ongoing") is True
    assert lcsmod.get_lc(db, lc_id)["stage"] == "shipment_ongoing"
    assert db.execute(
        "SELECT stage FROM sales WHERE id = %s", (sid,)
    ).fetchone()[0] == "shipment_ongoing"


def test_move_lc_stage_direct_jump_gated(db):
    """B4+B6: pi_issued -> shipment_ongoing direct jump is also gated."""
    co = _company(db)
    tag = _tag()
    prod = f"GateJump-{tag}"
    lc_id, _ = _lc_ready_setup(db, co, prod,
                               with_recipe=False, with_invoice=False)
    db.execute("UPDATE letters_of_credit SET stage = 'pi_issued' WHERE id = %s",
               (lc_id,))
    db.commit()
    with pytest.raises(ValueError, match="recipes:"):
        lcsmod.move_lc_stage(db, lc_id, "shipment_ongoing")
    db.rollback()
    assert lcsmod.get_lc(db, lc_id)["stage"] == "pi_issued"


def test_move_lc_stage_overshoot_gated(db):
    """Gate 2 re-review: lc_received -> payment_due jumping OVER
    shipment_ongoing is gated too."""
    co = _company(db)
    tag = _tag()
    lc_id, _ = _lc_ready_setup(db, co, f"GateOver-{tag}",
                               with_recipe=False, with_invoice=False)
    with pytest.raises(ValueError, match="recipes:"):
        lcsmod.move_lc_stage(db, lc_id, "payment_due")
    db.rollback()
    assert lcsmod.get_lc(db, lc_id)["stage"] == "lc_received"


def test_linked_sale_direct_move_refused(db):
    """B5+M2: linked sale direct-move to shipment_ongoing refused."""
    co = _company(db)
    lc = lcsmod.create_lc(db, co, f"LC-B5-{_tag()}")
    sid = _sale(db, co)
    lcsmod.attach_pis(db, lc["id"], [sid])
    assert salesmod.move_sale_to_stage(db, sid, "shipment_ongoing") is False
    db.rollback()
    assert salesmod.advance_sale(db, sid) is not None  # pi_issued->lc_received ok
    # now at lc_received, next advance would be shipment_ongoing -> refused
    assert salesmod.advance_sale(db, sid) is None
    db.rollback()


def test_linked_sale_via_lc_propagates(db):
    """B5+M2: move_lc_stage propagates to children despite the guard."""
    co = _company(db)
    tag = _tag()
    prod = f"ViaLc-{tag}"
    lc_id, sid = _lc_ready_setup(db, co, prod,
                                 with_recipe=True, with_invoice=True)
    assert lcsmod.move_lc_stage(db, lc_id, "shipment_ongoing") is True
    assert db.execute(
        "SELECT stage FROM sales WHERE id = %s", (sid,)
    ).fetchone()[0] == "shipment_ongoing"


def test_linked_sale_autocomplete_unaffected(db):
    """B5+M2: payment_due -> completed stays allowed for linked sales."""
    from chem_stock import record_sale_payment as _pay
    co = _company(db)
    lc = lcsmod.create_lc(db, co, f"LC-AC-{_tag()}")
    sid = _sale(db, co, qty=10, price=10)
    lcsmod.attach_pis(db, lc["id"], [sid])
    assert salesmod.move_sale_to_stage(db, sid, "payment_due") is True
    out = _pay(db, sid, "2026-09-02", 100.0)
    assert out["stage"] == "completed"


def test_relink_sale_company_helper(db):
    tag = _tag()
    co1 = _company(db, f"LC H A {tag}")
    co2 = _company(db, f"LC H B {tag}")
    lc = lcsmod.create_lc(db, co1, f"LC-H-{tag}")
    sid = _sale(db, co1)
    lcsmod.attach_pis(db, lc["id"], [sid])
    # same company: keeps link
    assert salesmod.relink_sale_company(db, sid, co1) is True
    db.commit()
    assert db.execute(
        "SELECT lc_id FROM sales WHERE id = %s", (sid,)
    ).fetchone()[0] == lc["id"]
    # different company: clears link
    assert salesmod.relink_sale_company(db, sid, co2) is True
    db.commit()
    row = db.execute(
        "SELECT lc_id, lc_number, lc_date FROM sales WHERE id = %s", (sid,)
    ).fetchone()
    assert row[0] is None
    # missing sale -> False
    assert salesmod.relink_sale_company(db, 999999999, co1) is False
    db.rollback()
