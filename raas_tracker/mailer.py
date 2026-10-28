"""Outbound reminder email over SMTP. Env-configured, never raises to callers.

Recipients are the company's own directors (``NOTIFY_EMAILS``). A client
address is never read, passed in, or stored: reminders are internal only.
An unconfigured mailer is a clean no-op so the in-app bell keeps working
until the SMTP credentials and the director list are supplied.
"""

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import List

logger = logging.getLogger("raas")

SMTP_TIMEOUT_SECONDS = 10
_warned_unconfigured = False


def _recipients() -> List[str]:
    raw = os.getenv("NOTIFY_EMAILS", "")
    return [addr.strip() for addr in raw.split(",") if addr.strip()]


def send_email(subject: str, body: str) -> bool:
    """Send one plain-text email to ``NOTIFY_EMAILS``.

    Returns True only when SMTP accepted the message. Missing config logs once
    and returns False; any SMTP/network failure is logged and swallowed so a
    cron run can never fail because the mail server is down.
    """
    global _warned_unconfigured
    host = os.getenv("SMTP_HOST")
    sender = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER")
    recipients = _recipients()
    if not host or not sender or not recipients:
        if not _warned_unconfigured:
            logger.warning(
                "email not configured (SMTP_HOST, SMTP_FROM/SMTP_USER and "
                "NOTIFY_EMAILS are required); reminders stay in-app only")
            _warned_unconfigured = True
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(body)

    try:
        port = int(os.getenv("SMTP_PORT", "587"))
        use_tls = os.getenv("SMTP_USE_TLS", "1").lower() not in ("0", "false", "no")
        user, password = os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD")
        with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            if use_tls:
                smtp.starttls()
            if user and password:
                smtp.login(user, password)
            smtp.send_message(message)
        return True
    except Exception:
        logger.exception("reminder email failed to send")
        return False
