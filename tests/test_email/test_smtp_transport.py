"""SMTP transport tests: port/TLS branch selection and error classification.

All smtplib I/O is mocked — no real network. The scheduler's send path and
the connection-test path must share one transport implementation
(open_smtp_connection); these tests pin the 465/587/no-TLS behaviour and the
error_type mapping that the review UI and retry logic rely on.
"""

from __future__ import annotations

import socket
import ssl
from unittest.mock import MagicMock, patch

import pytest

from emailing import email_sender, smtp_client
from emailing.email_sender import send_email


def _account(port: int, *, use_tls: bool = True) -> dict:
    return {
        "provider_type": "smtp",
        "from_name": "B2Binsights",
        "from_email": "sales@example.com",
        "smtp_host": "smtp.example.com",
        "smtp_port": port,
        "smtp_username": "sales@example.com",
        "smtp_secret_encrypted": "secret",
        "use_tls": 1 if use_tls else 0,
    }


class _FakeSMTP:
    """Context-manager SMTP double recording the calls the sender makes."""

    instances: list = []

    def __init__(self, host, port, timeout=0, context=None):
        self.host, self.port = host, port
        self.init_context = context        # context given at construction (SSL branch)
        self.ehlo_calls = 0
        self.starttls_calls = 0
        self.sent: list = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        self.ehlo_calls += 1
        return (250, b"ok")

    def starttls(self, context=None):
        self.starttls_calls += 1
        self.context = context
        return (220, b"ready")

    def login(self, user, password):
        return (235, b"ok")

    def quit(self):
        return (221, b"bye")

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture
def fake_smtp(monkeypatch):
    _FakeSMTP.instances = []
    ssl_cls = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(email_sender.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(email_sender.smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr(email_sender.ssl, "create_default_context", ssl_cls)
    monkeypatch.setattr(smtp_client.ssl, "create_default_context", ssl_cls)
    return _FakeSMTP


@pytest.mark.asyncio
async def test_port_465_with_tls_uses_implicit_ssl(fake_smtp):
    result = await send_email(_account(465), to_email="buyer@example.com",
                              subject="s", body_text="b")

    assert result["ok"] is True and result["error_type"] == ""
    assert len(fake_smtp.instances) == 1
    client = fake_smtp.instances[0]
    assert client.port == 465 and client.init_context is not None   # SSL from the start
    assert client.starttls_calls == 0                          # never STARTTLS on 465
    assert client.ehlo_calls >= 1


@pytest.mark.asyncio
async def test_port_587_with_tls_uses_starttls(fake_smtp):
    result = await send_email(_account(587), to_email="buyer@example.com",
                              subject="s", body_text="b")

    assert result["ok"] is True
    client = fake_smtp.instances[0]
    assert client.port == 587 and client.init_context is None  # plaintext connect
    assert client.starttls_calls == 1                          # then STARTTLS


@pytest.mark.asyncio
async def test_no_tls_stays_plaintext(fake_smtp):
    result = await send_email(_account(25, use_tls=False), to_email="buyer@example.com",
                              subject="s", body_text="b")

    assert result["ok"] is True
    client = fake_smtp.instances[0]
    assert client.starttls_calls == 0 and client.init_context is None


@pytest.mark.asyncio
async def test_test_path_and_send_path_share_transport(fake_smtp):
    """The settings-based test helper must pick the same branch for 465."""
    class _S:
        email_smtp_host = "smtp.example.com"
        email_smtp_port = 465
        email_smtp_username = "sales@example.com"
        email_smtp_password = "secret"
        email_use_tls = True
        email_from_address = "sales@example.com"
        email_from_name = "B2Binsights"
        email_reply_to = ""

    smtp_client.test_smtp_connection(_S())

    assert len(fake_smtp.instances) == 1
    client = fake_smtp.instances[0]
    assert client.port == 465 and client.init_context is not None
    assert client.starttls_calls == 0


class TestErrorClassification:
    @pytest.mark.asyncio
    async def test_tls_handshake_failure_maps_to_tls_error(self, monkeypatch):
        err = ssl.SSLError(1, "WRONG_VERSION_NUMBER")
        monkeypatch.setattr(email_sender, "open_smtp_connection",
                            MagicMock(side_effect=err))

        result = await send_email(_account(80), to_email="b@example.com",
                                  subject="s", body_text="b")

        assert result["error_type"] == "tls_error"
        assert "WRONG_VERSION_NUMBER" in result["error"]

    @pytest.mark.asyncio
    async def test_server_disconnect_maps_to_connection_error(self, monkeypatch):
        import smtplib as sm

        monkeypatch.setattr(email_sender, "open_smtp_connection",
                            MagicMock(side_effect=sm.SMTPServerDisconnected("Connection unexpectedly closed")))

        result = await send_email(_account(465), to_email="b@example.com",
                                  subject="s", body_text="b")

        assert result["error_type"] == "connection_error"

    @pytest.mark.asyncio
    async def test_timeout_maps_to_timeout(self, monkeypatch):
        monkeypatch.setattr(email_sender, "open_smtp_connection",
                            MagicMock(side_effect=TimeoutError("timed out")))

        result = await send_email(_account(465), to_email="b@example.com",
                                  subject="s", body_text="b")

        assert result["error_type"] == "timeout"

    @pytest.mark.asyncio
    async def test_connection_refused_maps_to_connection_error(self, monkeypatch):
        monkeypatch.setattr(email_sender, "open_smtp_connection",
                            MagicMock(side_effect=ConnectionRefusedError(111)))

        result = await send_email(_account(465), to_email="b@example.com",
                                  subject="s", body_text="b")

        assert result["error_type"] == "connection_error"

    @pytest.mark.asyncio
    async def test_auth_failure_maps_to_auth_error(self, monkeypatch):
        import smtplib as sm

        monkeypatch.setattr(email_sender, "open_smtp_connection",
                            MagicMock(side_effect=sm.SMTPAuthenticationError(535, b"bad credentials")))

        result = await send_email(_account(465), to_email="b@example.com",
                                  subject="s", body_text="b")

        assert result["error_type"] == "auth_error"

    @pytest.mark.asyncio
    async def test_generic_os_error_still_network_error(self, monkeypatch):
        monkeypatch.setattr(email_sender, "open_smtp_connection",
                            MagicMock(side_effect=socket.gaierror("dns broken")))

        result = await send_email(_account(465), to_email="b@example.com",
                                  subject="s", body_text="b")

        assert result["error_type"] == "network_error"
