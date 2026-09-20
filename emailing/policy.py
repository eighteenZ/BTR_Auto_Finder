"""Lead -> email target selection rules."""

from __future__ import annotations

import re
from typing import Any

_GENERIC_LOCAL_PARTS = {
    "info", "sales", "contact", "office", "hello", "support", "admin", "service",
    "careers", "hr", "jobs", "recruiting", "recruitment", "employment",
}

# Local parts that are documentation/format placeholders, never real people.
# Pages often say "our email format is firstname.lastname@company.com" — the
# scraper transcribes those verbatim and they bounce on send.
_PLACEHOLDER_LOCAL_EXACT = {
    "jdoe", "john.doe", "jane.doe",
    "flast", "last", "lastname", "firstname", "first.last", "firstname.lastname",
    "yourname", "your.email", "youremail", "your.name",
    "email", "e-mail", "name", "user", "username",
    "test", "sample", "fname", "lname",
}

_PLACEHOLDER_LOCAL_PATTERNS = [
    re.compile(r"^first(?:name)?\.(?:last(?:name)?)?$"),
    re.compile(r"^f?last$"),
    re.compile(r"^(?:f|l)name$"),
    re.compile(r"^your\.?(?:name|email)$"),
]

# RFC 2606 reserved / documentation domains, plus common test domains.
_PLACEHOLDER_DOMAIN_EXACT = {
    "example.com", "example.org", "example.net",
    "test.com", "test.org", "test.net",
    "example", "test", "invalid",
}


def is_sendable_email(email: str) -> bool:
    """Whether an address is plausible as a real, sendable recipient.

    Rejects malformed strings, documentation/format-placeholder local parts
    ("jdoe@", "firstname.lastname@", "last@", "youremail@"...), and reserved
    example/test domains. Real human-style addresses (j.smith@, jhughes@,
    masaru.fujikura@) and role mailboxes (sales@, info@, dpo@) all pass.
    """
    normalized = _normalize_email(email)
    if not normalized or " " in normalized:
        return False
    if normalized.count("@") != 1:
        return False
    local, _, domain = normalized.partition("@")
    if not local or not domain or "." not in domain:
        return False

    if local in _PLACEHOLDER_LOCAL_EXACT:
        return False
    # Mangled URL artifacts: a local part that itself contains a TLD label
    # ("sub.example.com@x.com") comes from pasted URLs, never real mailboxes.
    if re.search(r"\.(?:com|org|net|io|edu|gov|co|uk)(?:\.|$)", local):
        return False
    if any(pattern.match(local) for pattern in _PLACEHOLDER_LOCAL_PATTERNS):
        return False

    if domain in _PLACEHOLDER_DOMAIN_EXACT:
        return False
    labels = domain.split(".")
    if labels[-1] in {"example", "test", "invalid"}:  # RFC 2606 reserved TLDs
        return False
    if "example" in labels[:-1]:                      # example.com, mail.example.com, ...
        return False
    return True

_TITLE_PRIORITIES = [
    "purchasing",
    "procurement",
    "sourcing",
    "owner",
    "ceo",
    "sales director",
    "sales manager",
    "product",
    "engineering",
    "general manager",
]


def _normalize_email(email: str) -> str:
    return re.sub(r"\s*\(inferred\)\s*$", "", str(email or ""), flags=re.I).strip().lower()


def _email_status(email: str) -> str:
    text = str(email or "").strip()
    if not text:
        return "none"
    if re.search(r"\(inferred\)\s*$", text, flags=re.I) or text.lower() == "inferred":
        return "inferred-from-pattern"
    return "verified"


def _title_rank(title: str) -> int:
    normalized = str(title or "").lower()
    for idx, keyword in enumerate(_TITLE_PRIORITIES):
        if keyword in normalized:
            return idx
    return len(_TITLE_PRIORITIES)


def choose_email_target(lead: dict[str, Any]) -> dict[str, str]:
    """Choose the best outbound target email for a lead."""
    targets = expand_email_targets(lead)
    return targets[0] if targets else {"target_email": "", "target_name": "", "target_title": "", "target_type": "none"}


def expand_email_targets(lead: dict[str, Any]) -> list[dict[str, str]]:
    """Return all sendable recipient targets for a lead in stable priority order."""
    decision_makers = lead.get("decision_makers") or []
    ranked_dm: list[tuple[int, int, dict[str, Any], str, str]] = []
    for dm in decision_makers:
        if not isinstance(dm, dict):
            continue
        email = _normalize_email(str(dm.get("email", "") or ""))
        if not email or "@" not in email:
            continue
        if not is_sendable_email(email):
            continue
        status = _email_status(str(dm.get("email", "") or ""))
        status_rank = 0 if status == "verified" else 1
        ranked_dm.append((status_rank, _title_rank(str(dm.get("title", "") or "")), dm, email, status))

    targets: list[dict[str, str]] = []
    seen_emails: set[str] = set()

    if ranked_dm:
        ranked_dm.sort(key=lambda item: (item[0], item[1], item[3], item[2].get("name", "")))
        for _, _, dm, email, status in ranked_dm:
            if email in seen_emails:
                continue
            seen_emails.add(email)
            targets.append({
                "target_email": email,
                "target_name": str(dm.get("name", "") or ""),
                "target_title": str(dm.get("title", "") or ""),
                "target_type": f"decision_maker_{status.replace('-', '_')}",
            })

    company_emails = []
    for email in lead.get("emails") or []:
        normalized = _normalize_email(str(email or ""))
        if "@" not in normalized:
            continue
        if not is_sendable_email(normalized):
            continue
        local = normalized.split("@", 1)[0]
        company_emails.append((0 if local in _GENERIC_LOCAL_PARTS else 1, normalized))
    if company_emails:
        company_emails.sort(key=lambda item: (item[0], item[1]))
        for priority, email in company_emails:
            if email in seen_emails:
                continue
            seen_emails.add(email)
            targets.append({
                "target_email": email,
                "target_name": str(lead.get("contact_person", "") or ""),
                "target_title": "",
                "target_type": "generic_company_email" if priority == 0 else "company_email",
            })

    if not targets:
        return [{"target_email": "", "target_name": "", "target_title": "", "target_type": "none"}]
    return targets
