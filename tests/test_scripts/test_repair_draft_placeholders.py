"""Repair-script cleaner: salutation fix must not depend on placeholder tokens."""

from __future__ import annotations

from scripts.repair_draft_placeholders import _clean_emails


class _Settings:
    email_signature_name = "Zion"
    email_signature_title = "Sales Manager"
    email_signature_phone = "+1 8607978125"
    email_signature_email = "zion@btrlgts.com"
    email_from_name = "B2Binsights"
    email_from_address = "sales@email.btrlgts.com"


def test_salutation_fixed_even_without_placeholders():
    """A body with no placeholder tokens but a generic salutation must still
    be rewritten — the salutation fix is unconditional for body_text."""
    emails = [{"sequence_number": 1, "subject": "Hi",
               "body_text": "Dear Sir/Madam,\n\nBody.\n\nBest regards,\nWendy"}]

    cleaned, report = _clean_emails(emails, {"target_email": "b@x.com"}, _Settings(), company_name="Acme")

    body = cleaned[0]["body_text"]
    assert "Dear Sir/Madam" not in body
    assert body.startswith("Dear Acme team,")
    assert "salutation:generic->company_team" in report


def test_subject_without_placeholders_untouched():
    emails = [{"sequence_number": 1, "subject": "Plain subject", "body_text": "Dear Sir/Madam,\n\nBody."}]

    cleaned, report = _clean_emails(emails, {"target_email": "b@x.com"}, _Settings(), company_name="Acme")

    assert cleaned[0]["subject"] == "Plain subject"
    assert "salutation:generic->company_team" in report


def test_named_salutation_and_clean_body_untouched():
    emails = [{"sequence_number": 1, "subject": "Hi", "body_text": "Dear Ms. Grayson,\n\nBody."}]

    cleaned, report = _clean_emails(emails, {"target_email": "b@x.com"}, _Settings(), company_name="Acme")

    assert cleaned == emails
    assert report == []
