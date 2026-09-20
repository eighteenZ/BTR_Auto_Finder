"""Decision-maker contact enrichment via data providers (Hunter.io first).

Given a company domain, asks the provider for known mailboxes with names and
positions, keeps the decision-maker titles we target, and returns them ranked
by the provider's confidence. Without an API key the tool is a no-op so the
pipeline runs unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from config.settings import get_settings

logger = logging.getLogger(__name__)

_HUNTER_DOMAIN_SEARCH_URL = "https://api.hunter.io/v2/domain-search"
_HUNTER_EMAIL_FINDER_URL = "https://api.hunter.io/v2/email-finder"
_HUNTER_EMAIL_VERIFIER_URL = "https://api.hunter.io/v2/email-verifier"
_REQUEST_TIMEOUT_SECONDS = 15.0
_EMPTY_RESULT_RETRY_DELAY_SECONDS = 8.0

# Role mailboxes that are never outreach contacts (signature/dept boxes).
_ROLE_LOCAL_WORDS = (
    "noreply", "no-reply", "no_reply", "mailer", "postmaster", "webmaster",
    "hostmaster", "abuse", "legal", "press", "privacy", "abuse",
    "support", "careers", "job", "recruit", "help", "billing",
)

# Defaults for ENRICHMENT_TITLE_KEYWORDS: the roles worth messaging for
# foreign-trade component/supplier outreach.
_DEFAULT_TITLE_KEYWORDS = (
    "purchasing", "procurement", "supply chain", "sourcing", "owner",
    "ceo", "president", "buyer", "vice president", "head of",
    "general manager", "imports",
)


class ContactEnrichmentTool:
    """Query a data provider for decision-maker contacts of a company domain."""

    def __init__(self, provider: str = "", api_key: str = "") -> None:
        settings = get_settings()
        self.provider = (provider or settings.enrichment_provider or "hunter").strip().lower()
        self.api_key = (api_key or settings.enrichment_api_key or "").strip()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) and self.provider == "hunter"

    async def find_decision_makers(
        self, domain: str, title_keywords: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return outreach contacts for the domain, best first.

        Title-keyword matches (purchasing/procurement/...) come first; other
        NAMED personal mailboxes follow as secondary targets — real people are
        worth messaging even when the provider lacks their position. Role
        boxes (webmaster/legal/support...) are dropped. Empty list when
        disabled, unusable domain, or provider failure — best-effort by design.
        """
        domain = str(domain or "").strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
        if not self.enabled or not domain or "." not in domain:
            return []

        settings = get_settings()
        keywords = self._title_keywords(settings, title_keywords)

        try:
            payload = await self._hunter_domain_search(domain)
        except Exception as exc:  # noqa: BLE001 - enrichment must not break the pipeline
            logger.warning("[Enrichment] Hunter query failed for %s: %s", domain, exc)
            return []

        emails = (payload or {}).get("emails", []) or []
        if not emails:
            # Hunter soft-throttles rapid repeated queries with 200 + empty
            # data; one spaced retry recovers the real result.
            await asyncio.sleep(_EMPTY_RESULT_RETRY_DELAY_SECONDS)
            try:
                payload = await self._hunter_domain_search(domain)
            except Exception:  # noqa: BLE001
                return []
            emails = (payload or {}).get("emails", []) or []

        decision: list[dict[str, Any]] = []
        secondary: list[dict[str, Any]] = []
        for entry in emails:
            if not isinstance(entry, dict):
                continue
            email = str(entry.get("value", "") or "").strip().lower()
            position = str(entry.get("position", "") or "").strip()
            if not email or "@" not in email:
                continue
            local = email.split("@", 1)[0]
            contact = {
                "email": email,
                "first_name": str(entry.get("first_name", "") or "").strip(),
                "last_name": str(entry.get("last_name", "") or "").strip(),
                "position": position,
                "confidence": int(entry.get("confidence", 0) or 0),
                "source": "enrichment",
            }
            if any(kw in position.lower() for kw in keywords):
                decision.append(contact)
            elif (
                (contact["first_name"] or contact["last_name"])
                and not position                     # known non-target titles stay out
                and not any(word in local for word in _ROLE_LOCAL_WORDS)
            ):
                secondary.append({**contact, "position": "unknown"})

        decision.sort(key=lambda item: -item["confidence"])
        secondary.sort(key=lambda item: -item["confidence"])
        result = decision + secondary
        logger.info("[Enrichment] %s: %d decision-maker(s) + %d other personal contact(s) "
                    "from %d known emails", domain, len(decision), len(secondary), len(emails))
        return result

    async def find_email(self, domain: str, first_name: str, last_name: str) -> dict[str, Any] | None:
        """Email Finder: name + domain -> verified personal mailbox (1 search).

        Returns {email, first_name, last_name, position, confidence,
        verification} or None when disabled/failed/not found.
        """
        if not self.enabled:
            return None
        first = str(first_name or "").strip()
        last = str(last_name or "").strip()
        domain = str(domain or "").strip().lower()
        if not domain or not first or not last:
            return None
        params = {"domain": domain, "first_name": first, "last_name": last,
                  "api_key": self.api_key}
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(_HUNTER_EMAIL_FINDER_URL, params=params)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Enrichment] Email Finder request failed for %s: %s", domain, exc)
            return None
        if response.status_code != 200:
            logger.warning("[Enrichment] Email Finder returned %s for %s %s",
                           response.status_code, first, last)
            return None
        data = (response.json() or {}).get("data") or {}
        email = str(data.get("email", "") or "").strip()
        if not email:
            return None
        verification = data.get("verification") or {}
        return {
            "email": email,
            "first_name": first,
            "last_name": last,
            "position": str(data.get("position", "") or ""),
            "confidence": int(data.get("confidence", 0) or 0),
            "verification_status": str(verification.get("status", "") or ""),
            "source": "enrichment",
        }

    async def verify_email(self, email: str) -> dict[str, Any]:
        """Deliverability verdict for one mailbox (1 verification)."""
        email = str(email or "").strip()
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(_HUNTER_EMAIL_VERIFIER_URL,
                                            params={"email": email, "api_key": self.api_key})
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Enrichment] Verifier request failed for %s: %s", email, exc)
            return {"result": "unknown", "score": 0}
        if response.status_code != 200:
            logger.warning("[Enrichment] Verifier returned %s for %s", response.status_code, email)
            return {"result": "unknown", "score": 0}
        data = (response.json() or {}).get("data") or {}
        return {
            "result": str(data.get("result", "") or "unknown"),
            "score": int(data.get("score", 0) or 0),
        }

    async def _hunter_domain_search(self, domain: str) -> dict[str, Any]:
        # No "limit" param: it is plan-restricted and returns 400 on free accounts.
        params = {"domain": domain, "api_key": self.api_key}
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(_HUNTER_DOMAIN_SEARCH_URL, params=params)

        if response.status_code == 429:
            logger.warning("[Enrichment] Hunter rate limit hit (429) for %s", domain)
            return {}
        if response.status_code != 200:
            logger.warning("[Enrichment] Hunter returned %s for %s", response.status_code, domain)
            return {}

        body = response.json()
        return body.get("data") or {}

    @staticmethod
    def _title_keywords(settings: Any, override: list[str] | None = None) -> list[str]:
        if override:
            return [kw.strip().lower() for kw in override if kw.strip()]
        raw = str(getattr(settings, "enrichment_title_keywords", "") or "").strip()
        source = raw if raw else ", ".join(_DEFAULT_TITLE_KEYWORDS)
        return [kw.strip().lower() for kw in source.split(",") if kw.strip()]
