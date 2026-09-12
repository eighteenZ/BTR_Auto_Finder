"""Canonical lead identity / dedupe-key helpers.

A lead is identified by (in priority order):
1. its official company website domain  -> ``d:<domain>``
2. its first valid email address        -> ``e:<email>``
3. a stable hash of company name + country -> ``x:<hash>``
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

from tools.url_filter import classify_url

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_domain(url: str) -> str:
    """Normalize a URL (or bare host) to its bare lowercase host."""
    candidate = str(url or "").strip()
    if candidate and "//" not in candidate:
        candidate = "//" + candidate
    domain = urlparse(candidate).netloc.lower().strip()
    if domain.startswith("www."):
        domain = domain[4:]
    if ":" in domain:
        domain = domain.split(":", 1)[0]
    return domain.rstrip(".")


def official_domain(url: str) -> str:
    """Return the domain only for official company-site URLs.

    Platform/profile/content URLs (LinkedIn, Thomasnet, ...) are excluded so
    different companies hosted on the same platform never collide.
    """
    if classify_url(url or "") != "company_site":
        return ""
    return normalize_domain(url)


def normalize_email(email: str) -> str:
    cleaned = str(email or "").replace("(inferred)", "").strip().lower()
    return cleaned if _EMAIL_RE.match(cleaned) else ""


def normalize_company(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "", str(name or "").lower())
    return cleaned


def compute_lead_key(lead: dict) -> str:
    """Return the canonical dedupe key for a lead dict."""
    domain = official_domain(lead.get("website", ""))
    if domain:
        return f"d:{domain}"

    for raw_email in lead.get("emails") or []:
        email = normalize_email(raw_email)
        if email:
            return f"e:{email}"

    company = normalize_company(lead.get("company_name", ""))
    if company:
        country = str(lead.get("country_code", "") or "").strip().lower()
        digest = hashlib.sha1(f"{company}|{country}".encode("utf-8")).hexdigest()[:16]
        return f"x:{digest}"
    return ""
