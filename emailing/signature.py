"""Single source of truth for the sender identity used in outreach content.

Draft generation, the send path and the repair tooling must substitute the
same values, otherwise a draft reviewed by a human differs from what actually
goes out.
"""

from __future__ import annotations

from typing import Any

from emailing.body_format import apply_sender_placeholders


def _cfg_str(settings: Any, key: str, default: str = "") -> str:
    """Read a signature setting as a plain string.

    Test doubles are often MagicMock objects: getattr(mock, key, default)
    returns an auto-created Mock (truthy) instead of the default, which used
    to render "<MagicMock id=...>" into signatures. Only real strings pass.
    """
    value = getattr(settings, key, default)
    return value if isinstance(value, str) else default


def signature_identity(settings: Any, account: dict[str, Any] | None = None) -> dict[str, str]:
    """Return the sender identity used to replace signature placeholders.

    ``account`` (an email_accounts row) may override name/title/phone to match
    the sending mailbox. The signature contact email is deliberately NOT taken
    from the sending address: ``EMAIL_SIGNATURE_EMAIL`` (falling back to
    ``EMAIL_FROM_ADDRESS``) owns it, so the visible reply-to-me address can
    differ from the SMTP channel.
    """
    identity = {
        "sender_name": (_cfg_str(settings, "email_signature_name") or _cfg_str(settings, "email_from_name")).strip(),
        "sender_title": _cfg_str(settings, "email_signature_title").strip(),
        "sender_phone": _cfg_str(settings, "email_signature_phone").strip(),
        "sender_email": (_cfg_str(settings, "email_signature_email") or _cfg_str(settings, "email_from_address")).strip(),
    }
    if account:
        for key in ("sender_name", "sender_title", "sender_phone"):
            value = account.get(key.replace("sender_", "signature_"))
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


# ── deterministic signature block ────────────────────────────────────────────

from emailing.body_format import _CLOSING_PATTERNS  # noqa: E402


def _signature_lines(settings: Any, account: dict[str, Any] | None = None) -> list[str]:
    """The configured signature block as plain lines (may be empty)."""
    block = _cfg_str(settings, "email_signature_block").strip()
    if block:
        return [line.rstrip() for line in block.splitlines() if line.strip()]

    identity = signature_identity(settings, account)
    company = _cfg_str(settings, "email_signature_company").strip()
    lines: list[str] = []
    if identity["sender_name"]:
        lines.append(identity["sender_name"])
    role = ", ".join(part for part in (identity["sender_title"], company) if part)
    if role:
        lines.append(role)
    contact = " | ".join(part for part in (identity["sender_email"], identity["sender_phone"]) if part)
    if contact:
        lines.append(contact)
    return lines


def _is_configured_signature(region_lines: list[str], signature_lines: list[str]) -> bool:
    """Whether the lines after the closing already carry the configured identity.

    Anchor on the strongest unique tokens (configured email, then phone, then
    the name line verbatim) so a hand-tweaked but genuine signature still
    counts as present, while a fabricated one ("Wendy" / another mailbox)
    does not.
    """
    region = [line.strip() for line in region_lines]
    email = next((l for l in signature_lines if "@" in l), "")
    phone = next((l for l in signature_lines if any(c.isdigit() for c in l) and "@" not in l and l), "")
    name = signature_lines[0] if signature_lines else ""
    if email and any(email in line for line in region):
        return True
    if phone and any(phone in line for line in region):
        return True
    if name and any(line == name for line in region):
        return True
    return False


def _find_closing_end(text: str) -> int | None:
    """Char offset just past the LAST closing phrase (line-based or inline).

    Models emit closings either on their own line or inline at the end of the
    final line ("... Best regards,"); both are valid per the few-shot examples.
    """
    import re

    lower = str(text or "").lower()
    best_end = None
    for pattern in _CLOSING_PATTERNS:
        for m in re.finditer(re.escape(pattern), lower):
            start = m.start()
            if start > 0 and (lower[start - 1].isalnum()):
                continue  # inside a word (e.g. "regardless")
            end = m.end()
            while end < len(text) and text[end] in ",.":   # absorb greeting punctuation
                end += 1
            best_end = end if best_end is None else max(best_end, end)
    return best_end


def _strip_trailing_signature(text: str, signature_lines: list[str]) -> tuple[str, int]:
    """Peel repeated copies of the configured block off the tail; return (text, count)."""
    block = "\n\n" + "\n".join(signature_lines)
    peeled = 0
    while text.rstrip().endswith(block):
        text = text.rstrip()[: -len(block)]
        peeled += 1
    return text.rstrip(), peeled


def ensure_signature_block(body_text: str, settings: Any, account: dict[str, Any] | None = None) -> str:
    """Make the body end with the configured signature, deterministically.

    - closing line present, nothing after it -> append the configured block;
    - closing line present with a fabricated/stale signature after it (lines
      that do not carry the configured identity) -> replace that region;
    - configured signature already there -> return unchanged (idempotent);
    - nothing configured at all -> return the body untouched (the model's own
      sign-off, if any, is kept) so an empty configuration never erases text.
    """
    text = str(body_text or "").rstrip()
    signature_lines = _signature_lines(settings, account)
    if not signature_lines:
        return text

    close_end = _find_closing_end(text)
    if close_end is None:
        # No closing line: normalise the tail to exactly one configured block.
        # Repeated repair passes used to stack copies here; peeling first makes
        # the operation idempotent (0 peeled = first append, N peeled = collapse).
        trimmed, _peeled = _strip_trailing_signature(text, signature_lines)
        return trimmed + "\n\n" + "\n".join(signature_lines)

    region = text[close_end:]
    if _is_configured_signature(region.split("\n"), signature_lines):
        return text

    kept = text[:close_end].rstrip()
    return kept + "\n\n" + "\n".join(signature_lines)


def sanitize_body_with_signature(
    body_text: str,
    settings: Any,
    *,
    recipient_name: str = "",
    account: dict[str, Any] | None = None,
) -> str:
    """One-stop body treatment for persisted drafts and the send path:
    placeholder sanitation followed by the deterministic signature block."""
    body = sanitize_outreach_text(
        body_text, settings, recipient_name=recipient_name, account=account
    )
    return ensure_signature_block(body, settings, account)
