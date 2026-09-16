"""Single source of truth for the sender identity used in outreach content.

Draft generation, the send path and the repair tooling must substitute the
same values, otherwise a draft reviewed by a human differs from what actually
goes out.
"""

from __future__ import annotations

from typing import Any

from emailing.body_format import apply_sender_placeholders


def signature_identity(settings: Any, account: dict[str, Any] | None = None) -> dict[str, str]:
    """Return the sender identity used to replace signature placeholders.

    ``account`` (an email_accounts row) may override the settings-derived
    values, matching how the scheduler sends through a specific account.
    """
    identity = {
        "sender_name": str(getattr(settings, "email_signature_name", "") or getattr(settings, "email_from_name", "") or "").strip(),
        "sender_title": str(getattr(settings, "email_signature_title", "") or "").strip(),
        "sender_phone": str(getattr(settings, "email_signature_phone", "") or "").strip(),
        "sender_email": str(getattr(settings, "email_from_address", "") or "").strip(),
    }
    if account:
        overrides = {
            "sender_name": account.get("signature_name"),
            "sender_title": account.get("signature_title"),
            "sender_phone": account.get("signature_phone"),
            "sender_email": account.get("from_email"),
        }
        for key, value in overrides.items():
            if str(value or "").strip():
                identity[key] = str(value).strip()
    return identity


def recipient_display_name(target: Any) -> str:
    """Best-effort recipient name for a salutation.

    The pipeline stores the chosen recipient in several shapes: a plain name,
    ``{"target_name": "Jane"}``, or a nested ``{"target_name": {"name": ...}}``
    produced when the extractor returned a person object.
    """
    if not isinstance(target, dict):
        return str(target or "").strip()

    for key in ("name", "target_name"):
        value = target.get(key)
        if isinstance(value, dict):
            nested = str(value.get("name", "") or "").strip()
            if nested:
                return nested
        elif str(value or "").strip():
            return str(value).strip()
    return ""


def sanitize_outreach_text(
    text: str,
    settings: Any,
    *,
    recipient_name: str = "",
    account: dict[str, Any] | None = None,
    strip_unknown: bool = True,
) -> str:
    """Apply the configured identity to any placeholders left in the text.

    ``strip_unknown`` (default) also removes tokens that cannot be filled, so
    stored drafts and sent mail never carry a raw placeholder.
    """
    return apply_sender_placeholders(
        text,
        recipient_name=recipient_name,
        strip_unknown=strip_unknown,
        **signature_identity(settings, account),
    )
