from emailing.body_format import apply_sender_placeholders, format_plaintext_email_body


def test_format_plaintext_email_body_adds_paragraph_breaks():
    raw = (
        "Dear Sir/Madam, I noticed Denney Electric Supply serves contractors and industrial customers with electrical "
        "components in the Pennsylvania area. We are Guangdong Yushun Electrical Co., Ltd., a specialized manufacturer "
        "of micro switches, rotary selectors, and toggle switches with over 10 years of experience. Given your focus on "
        "supplying reliable electrical components to local contractors, there may be a natural fit. If this product "
        "category is of interest, I would be happy to share an overview of the relevant models and certifications. "
        "Kind regards,"
    )

    formatted = format_plaintext_email_body(raw)

    assert "\n\n" in formatted
    assert "Kind regards," in formatted.split("\n\n")[-1]


def test_format_plaintext_email_body_keeps_existing_paragraphs():
    raw = "Dear Sir/Madam,\n\nWe manufacture micro switches for industrial controls.\n\nKind regards,"

    assert format_plaintext_email_body(raw) == raw


class TestApplySenderPlaceholders:
    def test_signature_placeholders_are_filled(self):
        body = (
            "Dear Ms. Grayson,\n\n"
            "We are a switch manufacturer.\n\n"
            "Best regards,\n"
            "[Your Name]\n"
            "[Title], Gdushun | gdushun.com\n"
            "[Phone] | [Email]"
        )

        result = apply_sender_placeholders(
            body,
            sender_name="Zhang San",
            sender_title="Sales Manager",
            sender_phone="+86-137-0000-0000",
            sender_email="sales@gdushun.com",
        )

        assert "[Your Name]" not in result
        assert "[Title]" not in result
        assert "[Phone]" not in result
        assert "[Email]" not in result
        assert "Zhang San" in result
        assert "Sales Manager, Gdushun | gdushun.com" in result
        assert "+86-137-0000-0000 | sales@gdushun.com" in result

    def test_salutation_name_placeholder_becomes_neutral_address(self):
        body = "Dear [Name],\n\nBody text.\n\nBest regards,\n[Your Name]"

        result = apply_sender_placeholders(body, sender_name="Zhang San")

        assert result.startswith("Dear Sir/Madam,")
        assert result.rstrip().endswith("Zhang San")

    def test_empty_values_drop_placeholder_lines(self):
        body = "Hello,\n\nBody.\n\nBest regards,\n[Your Name]\n[Title]\n[Phone]"

        result = apply_sender_placeholders(body, sender_name="", sender_title="", sender_phone="")

        assert "[Your Name]" not in result
        assert "[Title]" not in result
        assert "[Phone]" not in result
        assert result.rstrip().endswith("Best regards,")

    def test_text_without_placeholders_is_untouched(self):
        body = "Dear Ms. Grayson,\n\nNo placeholders here.\n\nBest regards,\nZhang San"

        assert apply_sender_placeholders(body, sender_name="Zhang San") == body

    def test_fabricated_contact_lines_are_rewritten(self):
        body = (
            "Dear Mr. Ngo,\n\n"
            "Body text.\n\n"
            "Best regards,\n"
            "Wendy Chen\n"
            "Account Manager\n"
            "Phone: (123) 456-7890\n"
            "Email: wendy.chen@btrlgts.com"
        )

        result = apply_sender_placeholders(
            body,
            sender_phone="+1 8607978125",
            sender_email="sales@email.btrlgts.com",
        )

        assert "Phone: +1 8607978125" in result
        assert "Email: sales@email.btrlgts.com" in result
        assert "(123) 456-7890" not in result
        assert "wendy.chen@" not in result

    def test_last_name_and_compound_placeholders(self):
        body = "Hello,\n\nBody.\n\nBest regards,\nWendy [Last Name]\n[phone/email]"

        result = apply_sender_placeholders(
            body, sender_name="Wendy", sender_phone="+1 8607978125", sender_email="sales@email.btrlgts.com"
        )

        assert "[Last Name]" not in result
        assert "[phone/email]" not in result
        assert "Wendy" in result
        assert "+1 8607978125 | sales@email.btrlgts.com" in result

    def test_leftover_unknown_bracket_tokens_removed_on_placeholder_lines(self):
        body = "Hello,\n\nBody.\n\nBest regards,\nWendy [Last Name]\n[Address]"

        result = apply_sender_placeholders(body, sender_name="Wendy")

        assert "[Address]" not in result
        assert "Wendy" in result

    def test_contact_line_dropped_when_no_value_configured(self):
        body = "Hello,\n\nBody.\n\nBest regards,\nWendy\nPhone: (123) 456-7890"

        result = apply_sender_placeholders(body, sender_name="Wendy", sender_phone="")

        assert "Phone" not in result
        assert "(123)" not in result

    def test_two_word_placeholder_variants(self):
        body = (
            "Hello,\n\nBody.\n\nBest regards,\n"
            "Wendy\nSales Manager, Better International Logistics\n"
            "https://btrlgts.com\n"
            "[Phone Number] | [Email Address]\n"
            "[Company Address]"
        )

        result = apply_sender_placeholders(
            body, sender_name="Wendy", sender_phone="+1 8607978125", sender_email="sales@email.btrlgts.com"
        )

        assert "[Phone Number]" not in result
        assert "[Email Address]" not in result
        assert "[Company Address]" not in result
        assert "+1 8607978125 | sales@email.btrlgts.com" in result
