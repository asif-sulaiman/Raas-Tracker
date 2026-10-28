"""Phase 3 of the invoice-maturity plan: per-invoice payment reminders.

The clock starts at the actual ship date, not at booking:

- at sight: the reminder fires 7 days after ``actual_ship_date``;
- deferred: it fires on the stored ``maturity_date``;
- both escalate 7 days after that, once each.

Every reminder is an in-app notification keyed ``invoice:{id}:due`` /
``invoice:{id}:due:7`` and one email to the directors in ``NOTIFY_EMAILS``
(never a client address). Paying or voiding the invoice clears both keys, so
the bell empties and a later re-open can notify again.
"""

import smtplib
from datetime import date, timedelta

from chem_stock import add_sale, create_company
from raas_tracker import mailer
from raas_tracker.lcs import attach_pis, create_lc
from raas_tracker.notifications import list_notifications_for
from raas_tracker.reminders import run_invoice_reminders
from raas_tracker.sales import create_invoice, mark_invoice_paid, void_invoice


# --------------------------------------------------------------------------- #
# Fixtures/helpers
# --------------------------------------------------------------------------- #
def _sale(db, client, pi, code, qty=10, price=50.0):
    cid = create_company(db, name=client, code=code)
    sid = add_sale(db, {
        "pi_number": pi,
        "client_name": client,
        "company_id": cid,
    }, [
        {"product_name": f"{pi}-product", "quantity": qty,
         "unit_price": price, "unit": "KG"},
    ])
    return cid, sid


def _invoice(db, sale_id, number, ship, maturity, status="shipped"):
    """Create one invoice and force its shipped state (production is Phase 2)."""
    lines = [{"sale_item_id": r[0], "quantity": float(r[1])}
             for r in db.execute(
                 "SELECT id, quantity FROM sale_items WHERE sale_id = %s "
                 "ORDER BY id", (sale_id,)).fetchall()]
    inv = create_invoice(db, sale_id, number, lines=lines,
                         invoice_date="2026-01-15")["invoice_id"]
    db.execute(
        "UPDATE invoices SET status = %s, actual_ship_date = %s, "
        "maturity_date = %s WHERE id = %s",
        (status, ship, maturity, inv))
    db.commit()
    return inv


def _types(db):
    return [n["type"] for n in list_notifications_for(db, 1, "admin")]


def _keys(db):
    return {n["dedupe_key"] for n in list_notifications_for(db, 1, "admin")}


def _cron(client, secret="test-secret"):
    return client.post("/api/cron/maturity-check",
                       headers={"Authorization": f"Bearer {secret}"})


# --------------------------------------------------------------------------- #
# When the reminder is due
# --------------------------------------------------------------------------- #
def test_at_sight_waits_seven_days_after_the_ship_date(db):
    _, sid = _sale(db, "Rem At Sight", "PI-REM-AS", "RMA")
    inv = _invoice(db, sid, "INV-REM-AS", "2026-10-01", "2026-10-01")

    assert run_invoice_reminders(db, date(2026, 10, 7)) == [], (
        "nothing is due before ship + 7")
    created = run_invoice_reminders(db, date(2026, 10, 8))
    assert [c["invoice_id"] for c in created] == [inv]
    assert _types(db).count("invoice_payment_due") == 1


def test_at_sight_dedupes_then_escalates_a_week_later(db):
    _, sid = _sale(db, "Rem Escalate", "PI-REM-ESC", "RME")
    inv = _invoice(db, sid, "INV-REM-ESC", "2026-10-01", "2026-10-01")

    first = run_invoice_reminders(db, date(2026, 10, 8))
    assert [(c["invoice_id"], c["escalation"]) for c in first] == [(inv, False)]
    assert run_invoice_reminders(db, date(2026, 10, 9)) == [], "same key dedupes"

    second = run_invoice_reminders(db, date(2026, 10, 15))
    assert [(c["invoice_id"], c["escalation"]) for c in second] == [(inv, True)]
    assert f"invoice:{inv}:due" in _keys(db)
    assert f"invoice:{inv}:due:7" in _keys(db)


def test_deferred_reminds_on_the_maturity_date_not_before(db):
    cid, sid = _sale(db, "Rem Deferred", "PI-REM-DEF", "RMD")
    lc = create_lc(db, company_id=cid, lc_number="LC-REM-DEF",
                   payment_terms="deferred", tenor_days=90)
    attach_pis(db, lc["id"], [sid])
    inv = _invoice(db, sid, "INV-REM-DEF", "2026-11-10", "2027-02-08")

    assert run_invoice_reminders(db, date(2027, 2, 7)) == [], (
        "a deferred invoice is not due before its maturity")
    created = run_invoice_reminders(db, date(2027, 2, 8))
    assert created[0]["invoice_id"] == inv
    assert created[0]["due_date"] == "2027-02-08"


def test_deferred_escalates_seven_days_after_maturity(db):
    cid, sid = _sale(db, "Rem DefEsc", "PI-REM-DESC", "RMX")
    lc = create_lc(db, company_id=cid, lc_number="LC-REM-DESC",
                   payment_terms="deferred", tenor_days=30)
    attach_pis(db, lc["id"], [sid])
    inv = _invoice(db, sid, "INV-REM-DESC", "2026-11-10", "2026-12-10")

    assert run_invoice_reminders(db, date(2026, 12, 10))[0]["escalation"] is False
    created = run_invoice_reminders(db, date(2026, 12, 17))
    assert [(c["invoice_id"], c["escalation"], c["days_late"])
            for c in created] == [(inv, True, 7)]


# --------------------------------------------------------------------------- #
# Resolution clears the reminders
# --------------------------------------------------------------------------- #
def test_paying_the_invoice_clears_its_reminder_keys(db):
    _, sid = _sale(db, "Rem Paid", "PI-REM-PAID", "RMP")
    inv = _invoice(db, sid, "INV-REM-PAID", "2026-10-01", "2026-10-01")
    run_invoice_reminders(db, date(2026, 10, 8))
    assert "invoice_payment_due" in _types(db)

    mark_invoice_paid(db, inv, 500, "2026-10-09")

    assert "invoice_payment_due" not in _types(db), (
        "a fully paid invoice must not keep its reminder")
    assert f"invoice:{inv}:due" not in _keys(db)
    assert f"invoice:{inv}:due:7" not in _keys(db)
    assert run_invoice_reminders(db, date(2026, 10, 20)) == [], (
        "a paid invoice is never scanned again")


def test_voiding_the_invoice_clears_its_reminder_keys(db):
    _, sid = _sale(db, "Rem Void", "PI-REM-VOID", "RMV")
    inv = _invoice(db, sid, "INV-REM-VOID", "2026-10-01", "2026-10-01")
    run_invoice_reminders(db, date(2026, 10, 8))
    assert "invoice_payment_due" in _types(db)

    assert void_invoice(db, sid, inv) is True

    assert "invoice_payment_due" not in _types(db)
    assert f"invoice:{inv}:due" not in _keys(db)
    assert f"invoice:{inv}:due:7" not in _keys(db)


# --------------------------------------------------------------------------- #
# One notification stream per invoice
# --------------------------------------------------------------------------- #
def test_each_invoice_notifies_on_its_own_schedule(db):
    _, sid = _sale(db, "Rem Multi", "PI-REM-MULTI", "RMM", qty=10)
    first = db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s", (sid,)).fetchone()[0]
    # Two invoices over the same single PI line would over-invoice, but the
    # reminder clock only reads the invoice dates; split by quantity instead.
    lines = [{"sale_item_id": first, "quantity": 5.0}]
    inv_a = create_invoice(db, sid, "INV-REM-MULTI-A", lines=lines,
                           invoice_date="2026-01-15")["invoice_id"]
    inv_b = create_invoice(db, sid, "INV-REM-MULTI-B", lines=lines,
                           invoice_date="2026-01-16")["invoice_id"]
    db.execute(
        "UPDATE invoices SET status = 'shipped', actual_ship_date = %s, "
        "maturity_date = %s WHERE id = %s",
        ("2026-09-25", "2026-09-25", inv_a))
    db.execute(
        "UPDATE invoices SET status = 'shipped', actual_ship_date = %s, "
        "maturity_date = %s WHERE id = %s",
        ("2026-10-05", "2026-10-05", inv_b))
    db.commit()

    only_a = run_invoice_reminders(db, date(2026, 10, 8))
    assert [c["invoice_id"] for c in only_a] == [inv_a], (
        "the second invoice is not due yet")

    later = run_invoice_reminders(db, date(2026, 10, 13))
    assert {c["invoice_id"] for c in later if not c["escalation"]} == {inv_b}
    assert [c["invoice_id"] for c in later if c["escalation"]] == [inv_a], (
        "invoice A escalates on its own clock while B gets its first reminder")


def test_reminder_payload_is_usd_and_names_the_invoice(db):
    _, sid = _sale(db, "Rem Payload", "PI-REM-PAY", "RMY")
    inv = _invoice(db, sid, "INV-REM-PAY", "2026-10-01", "2026-10-01")

    created = run_invoice_reminders(db, date(2026, 10, 8))
    item = created[0]
    assert item["invoice_id"] == inv
    assert item["invoice_number"] == "INV-REM-PAY"
    assert item["sale_id"] == sid
    assert item["amount"] == 500.0
    assert item["currency"] == "USD"
    assert "INV-REM-PAY" in item["body"]
    assert "$500.00" in item["body"]
    assert "USD" in item["body"]
    # The email is composed from the same payload, only for a new reminder.
    assert "INV-REM-PAY" in item["email_subject"]
    assert "$500.00" in item["email_body"]


def test_reminder_quotes_the_outstanding_balance(db):
    _, sid = _sale(db, "Rem Partial", "PI-REM-PART", "RMZ")
    inv = _invoice(db, sid, "INV-REM-PART", "2026-10-01", "2026-10-01")
    mark_invoice_paid(db, inv, 200, "2026-10-02")

    created = run_invoice_reminders(db, date(2026, 10, 8))
    assert created[0]["amount"] == 300.0
    assert "$300.00" in created[0]["body"]


# --------------------------------------------------------------------------- #
# The cron job
# --------------------------------------------------------------------------- #
def _due_today_invoice(db, client_name, pi, code):
    """A shipped invoice whose at-sight reminder date is exactly today."""
    _, sid = _sale(db, client_name, pi, code)
    ship = (date.today() - timedelta(days=7)).isoformat()
    inv = _invoice(db, sid, f"INV-{code}", ship, ship)
    return sid, inv


def test_cron_creates_reminders_and_emails_each_new_one(
        client, db, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "test-secret")
    sent = []
    monkeypatch.setattr(
        mailer, "send_email",
        lambda subject, body: sent.append((subject, body)) or True)
    sid, inv = _due_today_invoice(db, "Rem Cron", "PI-REM-CRON", "RMC")

    data = _cron(client).get_json()
    assert data["invoice_reminders"] == 1
    assert data["emails_sent"] == 1
    assert len(sent) == 1
    assert "INV-RMC" in sent[0][0]
    assert _keys(db) >= {f"invoice:{inv}:due"}
    row = db.execute(
        "SELECT new_value FROM audit_logs WHERE action = 'CRON_MATURITY_CHECK' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    assert "invoice_reminders=1" in row[0]
    assert "emails=1" in row[0]


def test_cron_second_run_creates_nothing_and_emails_nothing(
        client, db, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "test-secret")
    sent = []
    monkeypatch.setattr(
        mailer, "send_email",
        lambda subject, body: sent.append((subject, body)) or True)
    _due_today_invoice(db, "Rem Twice", "PI-REM-TWICE", "RMT")

    _cron(client)
    data = _cron(client).get_json()

    assert data["invoice_reminders"] == 0
    assert data["emails_sent"] == 0
    assert len(sent) == 1, "one email per newly created reminder, not per run"


def test_unconfigured_mailer_is_a_noop_and_the_cron_stays_green(
        client, db, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "test-secret")
    for key in ("SMTP_HOST", "SMTP_USER", "SMTP_FROM", "NOTIFY_EMAILS"):
        monkeypatch.delenv(key, raising=False)
    _due_today_invoice(db, "Rem NoSmtp", "PI-REM-NOSMTP", "RMN")

    response = _cron(client)

    assert response.status_code == 200
    data = response.get_json()
    assert data["invoice_reminders"] == 1, "the bell still works"
    assert data["emails_sent"] == 0


def test_sale_scan_skips_sales_that_have_invoices(
        client, db, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "test-secret")
    today = date.today().isoformat()
    # A sale with an invoice: the invoice clock owns its reminders.
    _, with_invoice = _sale(db, "Rem Skip A", "PI-REM-SKIP-A", "RSA")
    db.execute("UPDATE sales SET maturity_date = %s WHERE id = %s",
               (today, with_invoice))
    _invoice(db, with_invoice, "INV-RSA", today, today)
    db.commit()
    # A legacy sale with no invoice: the sale-level scan still owns it.
    _, legacy = _sale(db, "Rem Skip B", "PI-REM-SKIP-B", "RSB")
    db.execute("UPDATE sales SET maturity_date = %s WHERE id = %s",
               (today, legacy))
    db.commit()

    data = _cron(client).get_json()

    assert data["sale_reminders"] == 1
    assert data["invoice_reminders"] == 0, "an at-sight invoice is due today + 7"
    rows = db.execute(
        "SELECT entity_id FROM notifications WHERE type = 'maturity_due'"
    ).fetchall()
    assert [r[0] for r in rows] == [legacy], (
        "the invoiced sale must not also get the sale-level reminder")


# --------------------------------------------------------------------------- #
# The mailer itself
# --------------------------------------------------------------------------- #
class _FakeSMTP:
    sent = []
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.started_tls = False
        self.credentials = None
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.credentials = (user, password)

    def send_message(self, msg):
        _FakeSMTP.sent.append(msg)


def test_mailer_sends_to_exactly_the_director_list(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "raas@example.com")
    monkeypatch.setenv("NOTIFY_EMAILS", "d1@example.com, d2@example.com")
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    _FakeSMTP.sent.clear()
    _FakeSMTP.instances.clear()

    assert mailer.send_email("Invoice due", "Body text") is True

    msg = _FakeSMTP.sent[0]
    assert msg["To"] == "d1@example.com, d2@example.com"
    assert msg["Subject"] == "Invoice due"
    assert "Body text" in msg.get_content()
    assert _FakeSMTP.instances[0].started_tls is True
    assert _FakeSMTP.instances[0].timeout is not None


def test_mailer_without_config_never_touches_smtp(monkeypatch):
    for key in ("SMTP_HOST", "SMTP_USER", "SMTP_FROM", "NOTIFY_EMAILS"):
        monkeypatch.delenv(key, raising=False)

    def _boom(*args, **kwargs):
        raise AssertionError("SMTP must not be constructed without config")

    monkeypatch.setattr(smtplib, "SMTP", _boom)
    assert mailer.send_email("Subject", "Body") is False


def test_mailer_swallows_an_smtp_failure(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "raas@example.com")
    monkeypatch.setenv("NOTIFY_EMAILS", "d1@example.com")

    def _down(*args, **kwargs):
        raise OSError("no route to host")

    monkeypatch.setattr(smtplib, "SMTP", _down)
    assert mailer.send_email("Subject", "Body") is False, (
        "a broken mailer must not raise into the cron request")
