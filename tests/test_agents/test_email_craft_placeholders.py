"""Generation-time placeholder sanitation for outreach drafts.

A stored draft is what a human approves, so it must already read like the
final email — no "[Your Phone]" tokens, and no silently unfillable tokens
either.
"""

from __future__ import annotations

from agents.email_craft_agent import _sanitize_sequence_placeholders


class _Settings:
    email_signature_name = "Wendy"
    email_signature_title = "Sales Manager"
    email_signature_phone = "+1 8607978125"
    email_from_name = "B2Binsights"
    email_from_address = "sales@example.com"


def _sequence(body: str, *, subject: str = "Hello", target_name: str = "Jane Doe") -> dict:
    return {
        "lead": {"company_name": "Acme"},
        "target": {"target_name": target_name, "target_email": "jane@acme.com"},
        "emails": [{"sequence_number": 1, "subject": subject, "body_text": body, "suggested_send_day": 0}],
    }


class TestSanitizeSequencePlaceholders:
    def test_known_placeholders_are_filled_from_settings(self):
        sequence = _sequence(
            "Dear [Name],\n\nBody.\n\nBest regards,\n[Your Name]\n[Your Title]\n[Your Email] | [Your Phone]"
        )

        changed = _sanitize_sequence_placeholders(sequence, _Settings())
        body = sequence["emails"][0]["body_text"]

        assert changed == 1
        assert "[" not in body
        assert body.startswith("Dear Jane Doe,")          # 使用收件人姓名
        assert "Wendy" in body
        assert "Sales Manager" in body
        assert "sales@example.com | +1 8607978125" in body
        assert "placeholder_issues" not in sequence

    def test_placeholders_in_subject_are_cleaned_too(self):
        sequence = _sequence("Body only.", subject="Intro from [Your Name]")

        _sanitize_sequence_placeholders(sequence, _Settings())

        assert sequence["emails"][0]["subject"] == "Intro from Wendy"

    def test_unfillable_placeholder_is_removed_and_flagged(self):
        sequence = _sequence("Following up on my note of [date] about freight.\n\nBest regards,\n[Your Name]")

        _sanitize_sequence_placeholders(sequence, _Settings())
        body = sequence["emails"][0]["body_text"]

        assert "[date]" not in body
        assert "my note about freight" in body
        assert sequence["placeholder_issues"] == ["[date]"]
        assert sequence["review_status"] == "needs_review"
        assert sequence["auto_send_eligible"] is False
        assert any("unfillable" in issue.lower() for issue in sequence["review_summary"]["issues"])

    def test_clean_sequence_is_left_untouched(self):
        sequence = _sequence("Dear Jane Doe,\n\nBody.\n\nBest regards,\nWendy")

        changed = _sanitize_sequence_placeholders(sequence, _Settings())

        assert changed == 0
        assert "review_summary" not in sequence
        assert "placeholder_issues" not in sequence

    def test_rerun_is_idempotent(self):
        sequence = _sequence("Dear [Name],\n\nNote of [date].\n\nBest regards,\n[Your Name]")

        _sanitize_sequence_placeholders(sequence, _Settings())
        first_subject = sequence["emails"][0]["subject"]
        first_body = sequence["emails"][0]["body_text"]

        changed_again = _sanitize_sequence_placeholders(sequence, _Settings())

        assert changed_again == 0
        assert sequence["emails"][0]["subject"] == first_subject
        assert sequence["emails"][0]["body_text"] == first_body
        assert sequence["placeholder_issues"] == ["[date]"]   # 记录不被清除

    def test_target_name_may_be_a_dict(self):
        sequence = _sequence("Dear [Name],\n\nBody.")
        sequence["target"] = {"target_name": {"name": "Michael Breen", "title": "Owner"}}

        _sanitize_sequence_placeholders(sequence, _Settings())

        assert sequence["emails"][0]["body_text"].startswith("Dear Michael Breen,")
