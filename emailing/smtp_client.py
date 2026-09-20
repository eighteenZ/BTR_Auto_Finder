"""SMTP helpers for connection checks and outbound email sends.

``open_smtp_connection`` is the single authoritative transport: both the
settings-based helpers here and the scheduler's send path (email_sender.py)
go through it, so the 465 implicit-SSL branch can never drift out of sync
with the STARTTLS path again.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from config.settings import Settings
from emailing.body_format import format_plaintext_email_body
from emailing.signature import sanitize_body_with_signature

SMTP_TIMEOUT_SECONDS = 20


def _ensure_smtp_config(settings: Settings) -> None:
    required = {
        "EMAIL_SMTP_HOST": settings.email_smtp_host,
        "EMAIL_SMTP_PORT": str(settings.email_smtp_port or ""),
        "EMAIL_SMTP_USERNAME": settings.email_smtp_username,
        "EMAIL_SMTP_PASSWORD": settings.email_smtp_password,
        "EMAIL_FROM_ADDRESS": settings.email_from_address,
    }
    missing = [key for key, value in required.items() if not str(value or "").strip()]
    if missing:
        raise ValueError(f"Missing SMTP settings: {', '.join(missing)}")


def open_smtp_connection(
    host: str,
    port: int,
    username: str,
    password: str,
    *,
    use_tls: bool = True,
    timeout: int = SMTP_TIMEOUT_SECONDS,
    ssl_context: ssl.SSLContext | None = None,
) -> smtplib.SMTP:
    """Open an authenticated SMTP connection using the right transport.

    - ``use_tls`` and port 465 -> implicit SSL (SMTP_SSL), connected in TLS;
    - ``use_tls`` on any other port -> plaintext connect then STARTTLS;
    - ``use_tls`` false -> plaintext, no upgrade.
    """
    if use_tls and port == 465:
        context = ssl_context or ssl.create_default_context()
        client: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
        client.ehlo()
    else:
        client = smtplib.SMTP(host, port, timeout=timeout)
        client.ehlo()
        if use_tls:
            client.starttls(context=ssl_context or ssl.create_default_context())
            client.ehlo()

    client.login(username, password)
    return client


def _connect(settings: Settings) -> smtplib.SMTP:
    _ensure_smtp_config(settings)
    return open_smtp_connection(
        settings.email_smtp_host,
        int(settings.email_smtp_port or 0),
        settings.email_smtp_username,
        settings.email_smtp_password,
        use_tls=settings.email_use_tls,
    )


def test_smtp_connection(settings: Settings) -> dict[str, str]:
    """Validate that SMTP settings can connect and authenticate."""
    client = _connect(settings)
    try:
        return {
            "status": "ok",
            "host": settings.email_smtp_host,
            "username": settings.email_smtp_username,
        }
    finally:
        client.quit()


def send_smtp_email(
    settings: Settings,
    *,
    to_address: str,
    subject: str,
    body_text: str,
) -> dict[str, str]:
    """Send a plain-text email using the configured SMTP account."""
    client = _connect(settings)
    try:
        message = EmailMessage()
        message["From"] = (
            f"{settings.email_from_name} <{settings.email_from_address}>"
            if settings.email_from_name.strip()
            else settings.email_from_address
        )
        message["To"] = to_address
        message["Subject"] = subject
        if settings.email_reply_to.strip():
            message["Reply-To"] = settings.email_reply_to
        message.set_content(
            format_plaintext_email_body(sanitize_body_with_signature(body_text, settings))
        )
        client.send_message(message)
        return {
            "status": "sent",
            "to_address": to_address,
            "subject": subject,
        }
    finally:
        client.quit()
