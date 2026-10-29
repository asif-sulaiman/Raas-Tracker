"""Per-invoice payment reminders for the daily cron.

The clock starts when the goods actually ship:

- at sight: due at ``actual_ship_date + 7``;
- deferred: due at the invoice's stored ``maturity_date``.

A shipped, not-yet-paid invoice gets one bell entry at that date and one
escalation 7 days later. Dedupe keys ``invoice:{id}:due`` and
``invoice:{id}:due:7`` make the run idempotent; paying or voiding the invoice
clears them (``notifications.clear_invoice_reminders``). Only reminders that
were actually inserted are returned, so the cron sends one email per new
reminder and never re-mails a condition it already reported.

The same cron also warns about LCs whose **latest date of shipment**
(``letters_of_credit.expiry_date``) is within a week — after it the LC is
invalid and must be amended. Those alerts are bell-only; amending the date
clears the keys so the warning re-arms for the new deadline.
"""

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import psycopg

from .notifications import clear_lc_shipment_reminders, notify

AT_SIGHT_GRACE_DAYS = 7
ESCALATION_AFTER_DAYS = 7
LC_WINDOW_DAYS = 7
CURRENCY = "USD"


def _as_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _due_date(payment_terms: Optional[str], actual_ship_date: Any,
              maturity_date: Any) -> Optional[date]:
    """Deferred invoices are due at maturity; at sight a week after shipping."""
    if payment_terms == "deferred" and maturity_date:
        return _as_date(maturity_date)
    shipped = _as_date(actual_ship_date)
    if shipped is None:
        return None
    return shipped + timedelta(days=AT_SIGHT_GRACE_DAYS)


def _usd(amount: Optional[float]) -> str:
    return "an unknown amount" if amount is None else f"${amount:,.2f}"


def _body(invoice_number: str, pi_number: Optional[str], due: date,
          days_late: int, amount: Optional[float], overdue: bool) -> str:
    pi = f" on PI {pi_number}" if pi_number else ""
    when = (f"is {days_late} days overdue" if overdue
            else f"is due {due.isoformat()}")
    return (f"Invoice {invoice_number}{pi} {when} — "
            f"{_usd(amount)} {CURRENCY} outstanding (due {due.isoformat()}).")


def run_invoice_reminders(conn: psycopg.Connection,
                          today: date) -> List[Dict[str, Any]]:
    """Create due/escalated reminders for shipped invoices; return the new ones."""
    rows = conn.execute(
        """SELECT i.id, i.invoice_number, i.sale_id, s.client_name,
                  s.pi_number, i.actual_ship_date, i.maturity_date, i.amount,
                  lc.payment_terms,
                  COALESCE((SELECT ROUND(SUM(sp.payment_amount)::numeric, 2)::float8
                              FROM sale_payments sp
                             WHERE sp.invoice_id = i.id), 0)
             FROM invoices i
             JOIN sales s ON s.id = i.sale_id
             LEFT JOIN letters_of_credit lc ON lc.id = s.lc_id
            WHERE i.status = 'shipped'
            ORDER BY i.id""").fetchall()

    created: List[Dict[str, Any]] = []
    for (invoice_id, invoice_number, sale_id, client_name, pi_number,
         actual_ship_date, maturity_date, amount, payment_terms, paid) in rows:
        due = _due_date(payment_terms, actual_ship_date, maturity_date)
        if due is None or due > today:
            continue
        outstanding = (None if amount is None
                       else round(max(0.0, float(amount) - float(paid)), 2))
        days_late = (today - due).days
        base = {
            "invoice_id": invoice_id, "invoice_number": invoice_number,
            "sale_id": sale_id, "pi_number": pi_number,
            "client_name": client_name, "due_date": due.isoformat(),
            "days_late": days_late, "amount": outstanding,
            "currency": CURRENCY,
        }

        title = f"Invoice payment due: {client_name}"
        body = _body(invoice_number, pi_number, due, days_late,
                     outstanding, overdue=False)
        notification_id = notify(
            conn, type="invoice_payment_due", title=title, body=body,
            severity="warning", entity_type="sale", entity_id=sale_id,
            dedupe_key=f"invoice:{invoice_id}:due")
        if notification_id is not None:
            created.append({
                **base, "escalation": False, "title": title, "body": body,
                "email_subject": (f"Invoice {invoice_number} payment due — "
                                  f"{client_name} ({_usd(outstanding)} {CURRENCY})"),
                "email_body": body,
            })

        if days_late < ESCALATION_AFTER_DAYS:
            continue
        title = f"Invoice payment overdue: {client_name}"
        body = _body(invoice_number, pi_number, due, days_late,
                     outstanding, overdue=True)
        notification_id = notify(
            conn, type="invoice_payment_escalated", title=title, body=body,
            severity="critical", entity_type="sale", entity_id=sale_id,
            dedupe_key=f"invoice:{invoice_id}:due:7")
        if notification_id is not None:
            created.append({
                **base, "escalation": True, "title": title, "body": body,
                "email_subject": (f"Invoice {invoice_number} payment overdue — "
                                  f"{client_name} ({_usd(outstanding)} {CURRENCY})"),
                "email_body": body,
            })
    return created


def _lc_body(lc_number: Optional[str], expiry: date, days_left: int) -> str:
    number = f"LC {lc_number}" if lc_number else "The LC"
    if days_left < 0:
        when = f"was {expiry.isoformat()} ({-days_left} days ago)"
    else:
        when = f"is {expiry.isoformat()}"
    return (f"{number} latest date of shipment {when}. Shipment after that "
            "date is invalid — amend the LC to extend it.")


def run_lc_shipment_reminders(conn: psycopg.Connection,
                              today: date) -> List[Dict[str, Any]]:
    """Warn about LCs whose latest shipment date is near or past; return new.

    Scanned: an LC with at least one linked PI still open, plus an LC with no
    PIs at all (it still lapses). An LC whose PIs are all completed is settled
    and silent. Blank or unparseable dates are skipped — nothing to warn
    about — and a date more than a week out clears any stale keys so an
    out-of-band extension re-arms the warning.
    """
    rows = conn.execute(
        """SELECT lc.id, lc.lc_number, lc.lc_date, lc.expiry_date, c.name,
                  (SELECT COUNT(*) FROM sales s
                    WHERE s.lc_id = lc.id AND s.stage <> 'completed') AS open_pis,
                  (SELECT COUNT(*) FROM sales s WHERE s.lc_id = lc.id) AS all_pis
             FROM letters_of_credit lc
             LEFT JOIN companies c ON c.id = lc.company_id
            ORDER BY lc.id""").fetchall()

    created: List[Dict[str, Any]] = []
    for (lc_id, lc_number, lc_date, expiry_date, company_name,
         open_pis, all_pis) in rows:
        deadline = _as_date(expiry_date)
        if deadline is None:
            continue
        if all_pis and not open_pis:
            # Every PI under this LC is completed: the deadline no longer bites.
            clear_lc_shipment_reminders(conn, lc_id)
            conn.commit()
            continue
        days_left = (deadline - today).days
        if days_left > LC_WINDOW_DAYS:
            clear_lc_shipment_reminders(conn, lc_id)
            conn.commit()
            continue
        if days_left < 0:
            type_, severity = "lc_shipment_deadline_passed", "critical"
            title = f"LC latest shipment date passed: {company_name}"
            key = f"lc:{lc_id}:ship:0"
        else:
            type_, severity = "lc_shipment_deadline", "warning"
            when = ("today" if days_left == 0 else f"in {days_left} days")
            title = f"LC shipment deadline {when}: {company_name}"
            key = f"lc:{lc_id}:ship:7"
        body = _lc_body(lc_number, deadline, days_left)
        notification_id = notify(
            conn, type=type_, title=title, body=body, severity=severity,
            entity_type="lc", entity_id=lc_id, dedupe_key=key)
        if notification_id is not None:
            created.append({
                "lc_id": lc_id, "lc_number": lc_number,
                "company_name": company_name, "lc_date": lc_date,
                "expiry_date": deadline.isoformat(), "days_left": days_left,
                "severity": severity, "title": title, "body": body,
            })
    return created
