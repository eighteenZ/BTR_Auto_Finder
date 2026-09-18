"""EMAIL_SIGNATURE_EMAIL: signature contact mailbox decoupled from the sender."""

from __future__ import annotations

from fastapi.testclient import TestClient

import api.auth as auth
from api.marketing_app import create_marketing_app
from emailing.signature import sanitize_outreach_text, signature_identity


class _Settings:
    email_signature_name = "Wendy"
    email_signature_title = "Sales Manager"
    email_signature_phone = "+1 8607978125"
    email_signature_email = ""                       # unset by default
    email_from_name = "B2Binsights"
    email_from_address = "sales@email.btrlgts.com"


class TestSignatureEmailPriority:
    def test_unset_falls_back_to_from_address(self):
        identity = signature_identity(_Settings())
        assert identity["sender_email"] == "sales@email.btrlgts.com"

    def test_set_takes_priority_over_from_address(self, monkeypatch):
        s = _Settings()
        s.email_signature_email = "zion@btrlgts.com"
        assert signature_identity(s)["sender_email"] == "zion@btrlgts.com"

    def test_account_from_email_does_not_override_signature_email(self):
        """The SMTP channel must not leak back into the visible contact line."""
        s = _Settings()
        s.email_signature_email = "zion@btrlgts.com"
        account = {"from_email": "sales@email.btrlgts.com"}

        identity = signature_identity(s, account)
        assert identity["sender_email"] == "zion@btrlgts.com"

    def test_sanitize_renders_signature_email_in_contact_lines(self):
        s = _Settings()
        s.email_signature_email = "zion@btrlgts.com"

        body = "Dear [Name],\n\nBody.\n\nBest regards,\n[Your Name]\n[Your Email] | [Your Phone]"
        result = sanitize_outreach_text(body, s, recipient_name="Jane")

        assert "zion@btrlgts.com | +1 8607978125" in result
        assert "sales@email.btrlgts.com" not in result


class TestSettingsApiSignatureEmail:
    def test_round_trip_via_settings_api_and_preserves_smtp_test_stamp(self):
        """POST EMAIL_SIGNATURE_EMAIL must not clear the SMTP test timestamp."""
        import api.email_routes  # noqa: F401
        import api.auth as auth

        auth.create_user("admin@btrlgts.com", role="admin", verify_password=None)
        client = TestClient(create_marketing_app())
        client.post("/api/auth/login", json={"email": "admin@btrlgts.com", "password": "x"})

        from config.settings_store import read_settings

        before = read_settings().get("EMAIL_SMTP_LAST_TEST_AT", "")
        assert before  # pre-condition: a test stamp exists

        res = client.post("/api/settings", json={"email_signature_email": "zion@btrlgts.com"})
        assert res.status_code in {200, 204}   # 204 = saved, no body

        # GET masks values containing '@'; verify the real persisted values via the env file.
        saved = read_settings()
        assert saved["EMAIL_SIGNATURE_EMAIL"] == "zion@btrlgts.com"
        assert saved["EMAIL_SMTP_LAST_TEST_AT"] == before
        # API read shows a masked form (same policy as EMAIL_FROM_ADDRESS).
        shown = client.get("/api/settings").json()["settings"]
        assert shown["EMAIL_SIGNATURE_EMAIL"] != "zion@btrlgts.com"
        assert "zion" in shown["EMAIL_SIGNATURE_EMAIL"]
