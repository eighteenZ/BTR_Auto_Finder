"""Deterministic signature block: missing / fabricated / idempotent cases."""

from __future__ import annotations

from agents.email_craft_agent import _rule_validate_emails_payload, _expected_signature
from emailing.signature import ensure_signature_block, sanitize_body_with_signature


class _Settings:
    email_signature_block = ""
    email_signature_name = "Zion"
    email_signature_title = "Sales Manager"
    email_signature_phone = "+1 8607978125"
    email_signature_email = "zion@btrlgts.com"
    email_signature_company = "Better International Logistics"
    email_from_name = "B2Binsights"
    email_from_address = "sales@email.btrlgts.com"


class _EmptySettings:
    email_signature_block = ""
    email_signature_name = ""
    email_signature_title = ""
    email_signature_phone = ""
    email_signature_email = ""
    email_signature_company = ""
    email_from_name = ""
    email_from_address = ""


class TestEnsureSignatureBlock:
    def test_missing_signature_is_appended(self):
        body = "Dear Ms. Grayson,\n\nBody paragraph about switches.\n\nBest regards,"

        result = ensure_signature_block(body, _Settings())

        assert result.endswith(
            "Best regards,\n\nZion\nSales Manager, Better International Logistics\n"
            "zion@btrlgts.com | +1 8607978125"
        )

    def test_correct_signature_is_idempotent(self):
        body = (
            "Dear Ms. Grayson,\n\nBody paragraph about switches.\n\nBest regards,\n\n"
            "Zion\nSales Manager, Better International Logistics\n"
            "zion@btrlgts.com | +1 8607978125"
        )

        assert ensure_signature_block(body, _Settings()) == body
        assert ensure_signature_block(ensure_signature_block(body, _Settings()), _Settings()) == body

    def test_fabricated_signature_is_replaced_with_configured_identity(self):
        body = (
            "Dear Ms. Grayson,\n\nBody paragraph about switches.\n\nBest regards,\n"
            "Wendy\nSales Manager, BTRLGTS\n+1 8607978125\nsales@email.btrlgts.com"
        )

        result = ensure_signature_block(body, _Settings())

        assert "Wendy" not in result
        assert "BTRLGTS" not in result
        assert "sales@email.btrlgts.com" not in result
        assert result.endswith(
            "Best regards,\n\nZion\nSales Manager, Better International Logistics\n"
            "zion@btrlgts.com | +1 8607978125"
        )

    def test_signature_block_config_takes_priority(self):
        s = _Settings()
        s.email_signature_block = "Zion | Outreach Team\nzion@btrlgts.com"

        result = ensure_signature_block("Dear X,\n\nBody.\n\nBest regards,", s)

        assert "Zion | Outreach Team" in result
        assert "Sales Manager" not in result

    def test_nothing_configured_keeps_model_signoff(self):
        body = "Dear X,\n\nBody.\n\nBest regards,\nWendy"

        assert ensure_signature_block(body, _EmptySettings()) == body

    def test_no_closing_line_appends_after_body(self):
        body = "Dear Ms. Grayson,\n\nBody paragraph without a closing."

        result = ensure_signature_block(body, _Settings())

        assert result.startswith(body)
        assert result.count("Zion") == 1

    def test_stacked_duplicate_signatures_collapse_to_one(self):
        block = "\n\nZion\nSales Manager, Better International Logistics\nzion@btrlgts.com | +1 8607978125"
        body = "Second touch: consolidated pricing sheet attached." + block * 3

        result = ensure_signature_block(body, _Settings())

        assert result.count("Zion") == 1
        assert result.startswith("Second touch: consolidated pricing sheet attached.")

    def test_closingless_body_already_signed_is_untouched(self):
        body = "Second touch: consolidated pricing sheet attached.\n\nZion\nSales Manager, Better International Logistics\nzion@btrlgts.com | +1 8607978125"

        result = ensure_signature_block(body, _Settings())

        assert result == body

    def test_send_path_body_gets_signature_via_sanitize_wrapper(self):
        body = "Dear Ms. Grayson,\n\nBody.\n\nBest regards,"

        result = sanitize_body_with_signature(body, _Settings())

        assert result.count("Zion") == 1
        assert "zion@btrlgts.com" in result


class TestReviewSignatureRule:
    def _rule(self, bodies):
        emails = [
            {"sequence_number": i + 1, "email_type": t, "subject": "s", "body_text": b,
             "suggested_send_day": d}
            for i, (t, d, b) in enumerate(zip(
                ("company_intro", "product_showcase", "partnership_proposal"), (0, 3, 7), bodies))
        ]
        return _rule_validate_emails_payload(emails, _expected_signature(_Settings()))

    def test_missing_signature_recorded_as_suggestion(self):
        result = self._rule([
            "Dear X,\n\n" + "word " * 60 + "\n\nBest regards,",
            "Dear X,\n\n" + "word " * 60 + "\n\nBest regards,\n\nZion\nSales Manager, Better International Logistics\nzion@btrlgts.com | +1 8607978125",
            "Dear X,\n\n" + "word " * 60 + "\n\nBest regards,\n\nZion\nSales Manager, Better International Logistics\nzion@btrlgts.com | +1 8607978125",
        ])

        assert any("no signature after the closing" in sug for sug in result["suggestions"])
        assert not any("signature" in i.lower() for i in result["issues"])

    def test_fabricated_signature_flagged(self):
        result = self._rule([
            "Dear X,\n\n" + "word " * 60 + "\n\nBest regards,\nWendy\nSales Manager, BTRLGTS",
            "Dear X,\n\n" + "word " * 60 + "\n\nBest regards,\n\nZion\nSales Manager, Better International Logistics\nzion@btrlgts.com | +1 8607978125",
            "Dear X,\n\n" + "word " * 60 + "\n\nBest regards,\n\nZion\nSales Manager, Better International Logistics\nzion@btrlgts.com | +1 8607978125",
        ])

        assert any("fabricated signature" in i for i in result["issues"])

    def test_configured_signature_passes(self):
        result = self._rule([
            "Dear X,\n\n" + "word " * 60 + "\n\nBest regards,\n\nZion\nSales Manager, Better International Logistics\nzion@btrlgts.com | +1 8607978125",
        ] * 3)

        assert not any("signature" in i for i in result["issues"])
