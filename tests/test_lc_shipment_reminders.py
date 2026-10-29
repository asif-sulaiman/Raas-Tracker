"""LC latest-date-of-shipment reminders for the daily cron.

An LC's ``expiry_date`` is its latest permissible shipment date: after it the
LC is invalid and must be amended. The bell warns once inside the last week
(``0 <= days_left <= 7``, key ``lc:{id}:ship:7``) and once after the deadline
(key ``lc:{id}:ship:0``). Only an LC with open PIs (or with no PIs at all)
is scanned; extending the date re-arms the warning because the update clears
both keys.

No email: LC alerts stay in the bell.
"""

from datetime import date, timedelta

from chem_stock import add_sale, create_company
from raas_tracker.lcs import attach_pis, create_lc
from raas_tracker.notifications import list_notifications_for
from raas_tracker.reminders import run_lc_shipment_reminders


# --------------------------------------------------------------------------- #
# Fixtures/helpers
# --------------------------------------------------------------------------- #
def _company(db, name, code):
    return create_company(db, name=name, code=code)


def _lc(db, company_id, number, lc_date, expiry):
    return create_lc(db, company_id=company_id, lc_number=number,
                     lc_date=lc_date, expiry_date=expiry)


def _pi(db, company_id, client, number):
    return add_sale(db, {
        "pi_number": number,
        "client_name": client,
        "company_id": company_id,
    }, [
        {"product_name": f"{number}-product", "quantity": 10,
         "unit_price": 50.0, "unit": "KG"},
    ])


def _keys(db):
    return {n["dedupe_key"] for n in list_notifications_for(db, 1, "admin")}


def _items(db, type_="lc_shipment_deadline"):
    return [n for n in list_notifications_for(db, 1, "admin")
            if n["type"] == type_]


# --------------------------------------------------------------------------- #
# When the warning fires
# --------------------------------------------------------------------------- #
def test_warns_inside_the_seven_day_window(db):
    cid = _company(db, "LC Warn Co", "LWC")
    lc = _lc(db, cid, "LC-WARN", "2026-12-01", "2026-12-30")
    attach_pis(db, lc["id"], [_pi(db, cid, "LC Warn Co", "PI-LC-WARN")])

    assert run_lc_shipment_reminders(db, date(2026, 12, 22)) == [], (
        "eight days out is still outside the window")
    created = run_lc_shipment_reminders(db, date(2026, 12, 23))

    assert [c["lc_id"] for c in created] == [lc["id"]]
    assert created[0]["days_left"] == 7
    assert created[0]["severity"] == "warning"
    assert created[0]["expiry_date"] == "2026-12-30"
    assert "LC-WARN" in created[0]["body"]
    assert "2026-12-30" in created[0]["body"]
    assert created[0]["company_name"] == "LC Warn Co"
    assert f"lc:{lc['id']}:ship:7" in _keys(db)


def test_silent_eight_days_out(db):
    cid = _company(db, "LC Early Co", "LWE")
    lc = _lc(db, cid, "LC-EARLY", "2026-12-01", "2026-12-30")
    attach_pis(db, lc["id"], [_pi(db, cid, "LC Early Co", "PI-LC-EARLY")])

    assert run_lc_shipment_reminders(db, date(2026, 12, 22)) == []
    assert _items(db) == []


def test_warning_is_deduped(db):
    cid = _company(db, "LC Dup Co", "LWD")
    lc = _lc(db, cid, "LC-DUP", "2026-12-01", "2026-12-30")
    attach_pis(db, lc["id"], [_pi(db, cid, "LC Dup Co", "PI-LC-DUP")])

    first = run_lc_shipment_reminders(db, date(2026, 12, 25))
    assert len(first) == 1
    assert run_lc_shipment_reminders(db, date(2026, 12, 26)) == []
    assert len(run_lc_shipment_reminders(db, date(2026, 12, 29))) == 0
    assert len(_items(db)) == 1


def test_critical_after_the_deadline(db):
    cid = _company(db, "LC Late Co", "LWL")
    lc = _lc(db, cid, "LC-LATE", "2026-12-01", "2026-12-30")
    attach_pis(db, lc["id"], [_pi(db, cid, "LC Late Co", "PI-LC-LATE")])
    run_lc_shipment_reminders(db, date(2026, 12, 28))

    created = run_lc_shipment_reminders(db, date(2027, 1, 2))

    assert [c["severity"] for c in created] == ["critical"]
    assert created[0]["days_left"] == -3
    assert "passed" in created[0]["title"].lower()
    assert _items(db, "lc_shipment_deadline_passed")
    assert f"lc:{lc['id']}:ship:0" in _keys(db)
    assert run_lc_shipment_reminders(db, date(2027, 1, 3)) == [], "deduped"


# --------------------------------------------------------------------------- #
# Which LCs are scanned
# --------------------------------------------------------------------------- #
def test_completed_pis_stop_reminders(db):
    cid = _company(db, "LC Done Co", "LWDN")
    lc = _lc(db, cid, "LC-DONE", "2026-12-01", "2026-12-30")
    sid = _pi(db, cid, "LC Done Co", "PI-LC-DONE")
    attach_pis(db, lc["id"], [sid])
    db.execute("UPDATE sales SET stage = 'completed' WHERE id = %s", (sid,))
    db.commit()

    assert run_lc_shipment_reminders(db, date(2026, 12, 28)) == []


def test_one_open_pi_keeps_the_whole_lc_scanned(db):
    cid = _company(db, "LC Mixed Co", "LWM")
    lc = _lc(db, cid, "LC-MIXED", "2026-12-01", "2026-12-30")
    done = _pi(db, cid, "LC Mixed Co", "PI-LC-MIX-A")
    open_pi = _pi(db, cid, "LC Mixed Co", "PI-LC-MIX-B")
    attach_pis(db, lc["id"], [done, open_pi])
    db.execute("UPDATE sales SET stage = 'completed' WHERE id = %s", (done,))
    db.commit()

    created = run_lc_shipment_reminders(db, date(2026, 12, 28))
    assert [c["lc_id"] for c in created] == [lc["id"]]


def test_zero_pi_lc_still_warns(db):
    cid = _company(db, "LC Empty Co", "LWE2")
    lc = _lc(db, cid, "LC-EMPTY", "2026-12-01", "2026-12-30")

    created = run_lc_shipment_reminders(db, date(2026, 12, 28))

    assert [c["lc_id"] for c in created] == [lc["id"]], (
        "a created-but-unlinked LC still lapses")


def test_blank_or_unparseable_expiry_is_silent(db):
    cid = _company(db, "LC Blank Co", "LWB")
    blank = _lc(db, cid, "LC-BLANK", "2026-12-01", None)
    bad = _lc(db, cid, "LC-BAD", "2026-12-01", "30/12/2026")

    assert run_lc_shipment_reminders(db, date(2026, 12, 28)) == []
    assert _items(db) == []
    # The blank LC is the one we created with no date, so nothing can match it.
    assert blank["expiry_date"] is None
    assert bad["expiry_date"] == "30/12/2026"


# --------------------------------------------------------------------------- #
# Amendment re-arms the warning
# --------------------------------------------------------------------------- #
def test_extending_the_deadline_rearms_the_warning(db):
    cid = _company(db, "LC Amend Co", "LWA")
    lc = _lc(db, cid, "LC-AMEND", "2026-12-01", "2026-12-20")
    attach_pis(db, lc["id"], [_pi(db, cid, "LC Amend Co", "PI-LC-AMEND")])
    assert len(run_lc_shipment_reminders(db, date(2026, 12, 15))) == 1

    # The amendment itself is covered by the API test; here the engine must
    # see the new deadline and speak again once it is inside the window.
    db.execute("UPDATE letters_of_credit SET expiry_date = '2027-01-15' "
               "WHERE id = %s", (lc["id"],))
    db.commit()
    assert run_lc_shipment_reminders(db, date(2026, 12, 18)) == [], (
        "an extended deadline stops the old warning")

    created = run_lc_shipment_reminders(db, date(2027, 1, 10))
    assert [c["days_left"] for c in created] == [5], (
        "the engine re-arms once the new deadline enters the window")


def test_engine_clears_keys_when_more_than_a_week_out(db):
    cid = _company(db, "LC Heal Co", "LWH")
    lc = _lc(db, cid, "LC-HEAL", "2026-12-01", "2026-12-20")
    attach_pis(db, lc["id"], [_pi(db, cid, "LC Heal Co", "PI-LC-HEAL")])
    run_lc_shipment_reminders(db, date(2026, 12, 15))
    assert _items(db), "warning created inside the window"

    db.execute("UPDATE letters_of_credit SET expiry_date = '2027-03-01' "
               "WHERE id = %s", (lc["id"],))
    db.commit()
    run_lc_shipment_reminders(db, date(2026, 12, 18))

    assert _items(db) == [], "an out-of-band extension clears the stale bell"


# --------------------------------------------------------------------------- #
# The cron job
# --------------------------------------------------------------------------- #
def _lc_due_in(db, days, name="LC Cron Co", code="LCR", number="LC-CRON"):
    cid = _company(db, name, code)
    expiry = (date.today() + timedelta(days=days)).isoformat()
    lc = _lc(db, cid, number, (date.today() - timedelta(days=30)).isoformat(),
             expiry)
    attach_pis(db, lc["id"], [_pi(db, cid, name, f"PI-{number}")])
    return lc


def test_cron_get_creates_lc_reminders(client, db, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "test-secret")
    lc = _lc_due_in(db, 5)

    response = client.get("/api/cron/maturity-check",
                          headers={"Authorization": "Bearer test-secret"})

    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data["lc_reminders"] == 1
    assert data["emails_sent"] == 0, "LC alerts stay in the bell"
    assert _keys(db) >= {f"lc:{lc['id']}:ship:7"}
    row = db.execute(
        "SELECT new_value FROM audit_logs WHERE action = 'CRON_MATURITY_CHECK' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    assert "lc_reminders=1" in row[0]


def test_cron_get_still_requires_the_bearer_secret(client, db, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "test-secret")
    lc = _lc_due_in(db, 5)

    assert client.get("/api/cron/maturity-check").status_code == 401
    assert client.get("/api/cron/maturity-check",
                      headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert _keys(db) == set(), "an unauthorized run writes nothing"
    assert lc["id"]
