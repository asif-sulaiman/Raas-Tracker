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
"""

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import psycopg

from .notifications import notify

AT_SIGHT_GRACE_DAYS = 7
ESCALATION_AFTER_DAYS = 7
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
