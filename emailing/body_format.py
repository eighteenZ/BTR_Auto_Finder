"""Helpers for formatting plain-text outbound email bodies."""

from __future__ import annotations

import re

_CLOSING_PATTERNS = (
    "kind regards",
    "best regards",
    "regards",
    "sincerely",
    "yours sincerely",
    "yours faithfully",
    "mit freundlichen grüßen",
    "cordiali saluti",
    "atentamente",
    "atenciosamente",
    "z poważaniem",
    "с уважением",
    "此致",
    "敬礼",
    "敬祝",
    "期待您的回复",
)


def _normalize_lines(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    compact: list[str] = []
    blank_run = 0
    for line in lines:
        if line.strip():
            blank_run = 0
            compact.append(line.strip())
        else:
            blank_run += 1
            if blank_run == 1:
                compact.append("")
    return "\n".join(compact).strip()


def _split_sentences(text: str) -> list[str]:
    collapsed = re.sub(r"\s+", " ", text.strip())
    if not collapsed:
        return []
    parts = re.split(r"(?<=[.!?])\s+", collapsed)
    return [part.strip() for part in parts if part.strip()]


def _extract_closing(sentences: list[str]) -> tuple[list[str], str]:
    if not sentences:
        return [], ""
    last = sentences[-1].strip()
    lowered = last.lower()
    if any(lowered.startswith(pattern) for pattern in _CLOSING_PATTERNS):
        return sentences[:-1], last
    return sentences, ""


def format_plaintext_email_body(body_text: str) -> str:
    """Format email body as readable plain text with paragraphs.

    If the model already returned paragraph breaks, keep them.
    Otherwise apply a conservative sentence-based grouping so the
    body reads like a standard outreach email rather than one block.
    """
    normalized = _normalize_lines(str(body_text or ""))
    if not normalized:
        return ""
    if "\n\n" in normalized:
        return normalized

    sentences = _split_sentences(normalized)
    if len(sentences) < 2:
        return normalized

    body_sentences, closing = _extract_closing(sentences)
    if len(body_sentences) >= 5:
        groups = [body_sentences[:2], body_sentences[2:4], body_sentences[4:]]
    elif len(body_sentences) == 4:
        groups = [body_sentences[:2], body_sentences[2:]]
    elif len(body_sentences) == 3:
        groups = [body_sentences[:1], body_sentences[1:2], body_sentences[2:]]
    else:
        groups = [body_sentences[:1], body_sentences[1:]]

    paragraphs = [" ".join(group).strip() for group in groups if group]
    if closing:
        paragraphs.append(closing)
    return "\n\n".join(part for part in paragraphs if part).strip()


def format_email_sequence_bodies(emails: list[dict]) -> list[dict]:
    """Return a copy of emails with normalized plain-text paragraph spacing."""
    formatted: list[dict] = []
    for email in emails:
        if not isinstance(email, dict):
            formatted.append(email)
            continue
        item = dict(email)
        item["body_text"] = format_plaintext_email_body(str(item.get("body_text", "") or ""))
        formatted.append(item)
    return formatted


_SENDER_PLACEHOLDER_PATTERN = re.compile(
    r"\[\s*("
    r"your\s+phone\s+number|your\s+email\s+address|"      # longest "your ..." forms first
    r"your\s+name|your\s+title|your\s+email|your\s+phone|your\s+last\s+name|"
    r"last\s+name|phone\s+number|e-?mail\s+address|company\s+address|"
    r"phone\s*/\s*email|"
    r"title|name|address|e-?mail|phone"                    # bare forms last
    r")\s*\]",
    re.IGNORECASE,
)

_SEPARATOR_ONLY_PATTERN = re.compile(r"^[\|\-\–\—,;:\s]*$")

_LEFTOVER_BRACKET_TOKEN = re.compile(r"\s*\[[^\[\]\n]{1,40}\]")

_CONTACT_LABEL_LINE = re.compile(r"^\s*(phone|tel|e-?mail)\s*[:：]\s*(.*)$", re.IGNORECASE)


def _clean_substituted_line(line: str) -> str:
    """Tidy separator punctuation exposed by placeholder removal."""
    line = re.sub(r"\s*\|\s*(?:\|\s*)+", " | ", line)
    line = re.sub(r"^\s*\|\s*|\s*\|\s*$", "", line)
    line = re.sub(r"^[,;:\-\–\—\s]+", "", line)
    return line


def apply_sender_placeholders(
    text: str,
    *,
    sender_name: str = "",
    sender_title: str = "",
    sender_phone: str = "",
    sender_email: str = "",
) -> str:
    """Replace model-emitted signature placeholders with the configured identity.

    Deterministic send-time safety net: whatever the generation model wrote,
    the outbound body must never contain literal "[Your Name]"-style tokens.
    "[Name]" on a salutation line refers to the recipient and falls back to a
    neutral form of address.
    """
    values = {
        "your name": str(sender_name or "").strip(),
        "your title": str(sender_title or "").strip(),
        "your email": str(sender_email or "").strip(),
        "your phone": str(sender_phone or "").strip(),
        "your email address": str(sender_email or "").strip(),
        "your phone number": str(sender_phone or "").strip(),
        "your last name": "",
        "last name": "",
        "email address": str(sender_email or "").strip(),
        "e-mail address": str(sender_email or "").strip(),
        "phone number": str(sender_phone or "").strip(),
        "company address": "",
        "address": "",
        "phone/email": " | ".join(
            part for part in (str(sender_phone or "").strip(), str(sender_email or "").strip()) if part
        ),
        "title": str(sender_title or "").strip(),
        "email": str(sender_email or "").strip(),
        "phone": str(sender_phone or "").strip(),
    }

    out_lines: list[str] = []
    for line in str(text or "").replace("\r\n", "\n").split("\n"):
        contact_match = _CONTACT_LABEL_LINE.match(line)
        if contact_match:
            label = contact_match.group(1).lower()
            label = "Email" if "mail" in label else "Phone"
            replacement = str(sender_email or "").strip() if label == "Email" else str(sender_phone or "").strip()
            if replacement:
                out_lines.append(f"{label}: {replacement}")
                continue
            # No configured value: drop the model-invented contact line entirely.
            continue

        if _SENDER_PLACEHOLDER_PATTERN.search(line):
            is_salutation = line.strip().lower().startswith("dear")

            def _sub(match: re.Match[str]) -> str:
                key = match.group(1).lower()
                if key == "name":
                    return "Sir/Madam" if is_salutation else values["your name"]
                return values.get(key, "")

            replaced = _SENDER_PLACEHOLDER_PATTERN.sub(_sub, line)
            # Unknown leftover bracket tokens (e.g. "[Address]") on a placeholder line.
            replaced = _LEFTOVER_BRACKET_TOKEN.sub("", replaced)
            replaced = _clean_substituted_line(replaced)
            if replaced.strip() and not _SEPARATOR_ONLY_PATTERN.match(replaced):
                out_lines.append(replaced)
            continue

        if re.fullmatch(r"\[[^\[\]\n]{1,40}\]", line.strip()):
            # A line consisting solely of an unknown bracket token is a model
            # placeholder artifact (e.g. "[Address]"), never real content.
            continue

        out_lines.append(line)

    return "\n".join(out_lines)
