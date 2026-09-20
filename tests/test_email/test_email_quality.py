"""Placeholder-email filtering and decision-maker enrichment tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from emailing.policy import expand_email_targets, is_sendable_email


class TestIsSendableEmail:
    @pytest.mark.parametrize("email", [
        "jdoe@x.com",
        "flast@x.com",
        "last@x.com",
        "firstname.lastname@x.com",
        "jane.doe@x.com",
        "JDOE@X.COM",
        "youremail@x.com",
        "lname@x.com",
        "fname@x.com",
        "user@x.com",
        "name@x.com",
        "anything@example.com",
        "a@example.com",
        "a@test.com",
        "sub.example.com@x.com",
        "someone@example.co.uk",
        "no-at-sign.com",
        "double@@x.com",
        "",
        "   ",
    ])
    def test_rejects(self, email):
        assert is_sendable_email(email) is False

    @pytest.mark.parametrize("email", [
        "sales@x.com",
        "info@x.com",
        "contact@x.com",
        "dpo@x.com",
        "j.smith@x.com",
        "jhughes@x.com",
        "masaru.fujikura@x.com",
        "JOHN@x.com",                      # 真名（非占位符模式）保留
        "jane@real-company.io",
        "procurement@bigcorp.com",
    ])
    def test_keeps(self, email):
        assert is_sendable_email(email) is True

    def test_reserved_subdomains_and_tlds(self):
        assert is_sendable_email("real@mail.example.com") is False
        assert is_sendable_email("real@sub.example.org") is False
        assert is_sendable_email("real@foo.test") is False
        assert is_sendable_email("real@foo.invalid") is False

    def test_inferred_suffix_tolerated(self):
        assert is_sendable_email("sales@x.com (inferred)") is True


class TestExpandEmailTargetsPlaceholderFilter:
    def test_placeholder_emails_never_become_targets(self):
        lead = {
            "company_name": "Acme",
            "emails": ["sales@a.com", "jdoe@a.com", "last@a.com", "firstname.lastname@a.com"],
        }

        targets = expand_email_targets(lead)

        emails = [t["target_email"] for t in targets]
        assert emails == ["sales@a.com"]

    def test_placeholder_decision_maker_skipped(self):
        lead = {
            "company_name": "Acme",
            "decision_makers": [
                {"name": "Placeholder", "title": "Owner", "email": "jdoe@acme.com"},
                {"name": "Real Person", "title": "Purchasing Manager", "email": "real@acme.com"},
            ],
            "emails": [],
        }

        targets = expand_email_targets(lead)

        assert [t["target_email"] for t in targets] == ["real@acme.com"]

    def test_all_placeholders_fall_back_to_none(self):
        lead = {
            "company_name": "Acme",
            "decision_makers": [{"name": "P", "title": "Owner", "email": "jdoe@acme.com"}],
            "emails": ["last@acme.com"],
        }

        targets = expand_email_targets(lead)

        assert targets == [{"target_email": "", "target_name": "", "target_title": "", "target_type": "none"}]

    def test_real_business_emails_unaffected(self):
        lead = {"company_name": "Acme", "emails": ["info@acme.com", "sales@acme.com", "buyer@acme.com"]}

        targets = expand_email_targets(lead)

        assert [t["target_email"] for t in targets] == ["info@acme.com", "sales@acme.com", "buyer@acme.com"]
