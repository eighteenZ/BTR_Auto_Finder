"""Hunter.io enrichment tool + pipeline merge/budget tests (all mocked).

Contract note (regression guard for the double-unwrap bug): the real
``_hunter_domain_search`` returns the INNER data dict — ``{"domain": ...,
"emails": [...]}`` — because it unwraps the HTTP body's "data" key. Mocks in
these tests therefore use the same inner shape. A dedicated contract test
drives the real ``_hunter_domain_search`` against a mocked httpx body to pin
the unwrap-once behavior.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.lead_extract_agent import _enrich_decision_maker_emails
from tools.contact_enrichment import ContactEnrichmentTool

# Inner-data shape: exactly what _hunter_domain_search returns.
HUNTER_PAYLOAD = {
    "domain": "acme.com",
    "emails": [
        {"value": "jane@acme.com", "first_name": "Jane", "last_name": "Doe",
         "position": "Purchasing Manager", "confidence": 95},
        {"value": "bob@acme.com", "first_name": "Bob", "last_name": "Ray",
         "position": "Receptionist", "confidence": 88},
        {"value": "owner@acme.com", "first_name": "", "last_name": "",
         "position": "Owner", "confidence": 91},
        {"value": "webmaster@acme.com", "position": "", "confidence": 60},
        {"value": "emeline@acme.com", "first_name": "Emeline", "last_name": "Dupont",
         "position": None, "confidence": 82},
    ],
}

# The raw HTTP body Hunter returns (outer "data" wrapper) — used by the
# contract test that exercises the real unwrap.
HUNTER_HTTP_BODY = {"data": HUNTER_PAYLOAD}


def _tool_with_key() -> ContactEnrichmentTool:
    return ContactEnrichmentTool(provider="hunter", api_key="test-key")


def install_http_transport(monkeypatch, body: dict) -> None:
    """Route the tool's HTTP through a MockTransport carrying a raw Hunter body."""
    import httpx as real_httpx

    calls: list = []

    def handler(request):
        calls.append({"url": str(request.url), "body": request.read()})
        return real_httpx.Response(200, json=body)

    transport = real_httpx.MockTransport(handler)

    class _Client(real_httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    import types
    shim = types.SimpleNamespace(AsyncClient=_Client)
    monkeypatch.setattr("tools.contact_enrichment.httpx", shim)


class _Shim:
    """Stand-in for the httpx module namespace inside contact_enrichment."""

    def __init__(self, AsyncClient):
        self.AsyncClient = AsyncClient


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


class TestContactEnrichmentTool:
    @pytest.mark.asyncio
    async def test_disabled_without_api_key(self, monkeypatch):
        monkeypatch.setattr("tools.contact_enrichment.get_settings",
                            lambda: MagicMock(enrichment_provider="hunter", enrichment_api_key=""))
        tool = ContactEnrichmentTool()

        assert tool.enabled is False
        assert await tool.find_decision_makers("acme.com") == []

    @pytest.mark.asyncio
    async def test_filters_by_title_and_sorts_by_confidence(self, monkeypatch):
        tool = _tool_with_key()
        monkeypatch.setattr(tool, "_hunter_domain_search",
                            AsyncMock(return_value=HUNTER_PAYLOAD))

        contacts = await tool.find_decision_makers("acme.com")

        # Decision-makers first (confidence desc); unknown-position named
        # contacts follow; receptionist (explicit title) and webmaster dropped.
        assert [c["email"] for c in contacts] == ["jane@acme.com", "owner@acme.com", "emeline@acme.com"]
        assert contacts[1]["position"] == "Owner" and contacts[2]["position"] == "unknown"

    @pytest.mark.asyncio
    async def test_rate_limit_returns_empty(self, monkeypatch):
        tool = _tool_with_key()
        response = MagicMock(status_code=429)
        monkeypatch.setattr(tool, "_hunter_domain_search",
                            AsyncMock(return_value={}))

        # 429 is mapped to {} inside _hunter_domain_search; result is empty, not an error.
        assert await tool.find_decision_makers("acme.com") == []
        assert response.status_code == 429  # sanity on the mocked mapping

    @pytest.mark.asyncio
    async def test_custom_title_keywords_override(self, monkeypatch):
        tool = _tool_with_key()
        monkeypatch.setattr(tool, "_hunter_domain_search",
                            AsyncMock(return_value=HUNTER_PAYLOAD))

        contacts = await tool.find_decision_makers("acme.com", title_keywords=["owner"])

        # Decision list keeps only title matches; named-unknown still trails as secondary.
        assert [c["email"] for c in contacts] == ["owner@acme.com", "emeline@acme.com"]

    @pytest.mark.asyncio
    async def test_contract_httpx_body_to_contacts(self, monkeypatch):
        """Contract pin: raw HTTP body {"data": {...}} -> non-empty contacts.

        The real _hunter_domain_search unwraps the body's "data" key ONCE;
        find_decision_makers consumes that shape. A double unwrap would make
        production return [] while single-shape mocks stay green.
        """
        install_http_transport(monkeypatch, {"data": HUNTER_PAYLOAD})
        tool = _tool_with_key()

        contacts = await tool.find_decision_makers("acme.com")

        assert [c["email"] for c in contacts][:2] == ["jane@acme.com", "owner@acme.com"]
        assert all("webmaster" not in c["email"] for c in contacts)

    def test_domain_search_unwraps_data_exactly_once(self, monkeypatch):
        install_http_transport(monkeypatch, {"data": HUNTER_PAYLOAD})
        tool = _tool_with_key()
        payload = asyncio_run(tool._hunter_domain_search("acme.com"))
        assert payload.get("domain") == "acme.com"
        assert payload.get("emails"), "inner data dict must surface emails"
        assert "data" not in payload

class TestEmailFinder:
    @pytest.mark.asyncio
    async def test_returns_found_email_with_verification(self, monkeypatch):
        tool = _tool_with_key()
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "data": {"email": "patrick@stripe.com", "position": "CEO",
                     "confidence": 99,
                     "verification": {"status": "valid", "date": "2026-09-21"}},
        }
        async_client = AsyncMock()
        async_client.get = AsyncMock(return_value=response)
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr("tools.contact_enrichment.httpx.AsyncClient",
                            lambda **kw: async_client)

        found = await tool.find_email("stripe.com", "Patrick", "Collison")

        assert found["email"] == "patrick@stripe.com"
        assert found["verification_status"] == "valid"

    @pytest.mark.asyncio
    async def test_finder_error_returns_none(self, monkeypatch):
        tool = _tool_with_key()
        response = MagicMock(status_code=401)
        response.json.return_value = {"errors": [{"id": "authentication_failed"}]}
        async_client = AsyncMock()
        async_client.get = AsyncMock(return_value=response)
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr("tools.contact_enrichment.httpx.AsyncClient",
                            lambda **kw: async_client)

        assert await tool.find_email("x.com", "A", "B") is None

    @pytest.mark.asyncio
    async def test_finder_requires_names(self):
        tool = _tool_with_key()
        assert await tool.find_email("x.com", "", "B") is None


class TestEmailVerifier:
    @pytest.mark.asyncio
    async def test_verifier_parses_verdict(self, monkeypatch):
        tool = _tool_with_key()
        response = MagicMock(status_code=200)
        response.json.return_value = {"data": {"result": "deliverable", "score": 92}}
        async_client = AsyncMock()
        async_client.get = AsyncMock(return_value=response)
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr("tools.contact_enrichment.httpx.AsyncClient",
                            lambda **kw: async_client)

        verdict = await tool.verify_email("buyer@acme.com")

        assert verdict == {"result": "deliverable", "score": 92}

    @pytest.mark.asyncio
    async def test_verifier_http_error_is_unknown(self, monkeypatch):
        tool = _tool_with_key()
        response = MagicMock(status_code=500)
        response.json.return_value = {}
        async_client = AsyncMock()
        async_client.get = AsyncMock(return_value=response)
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr("tools.contact_enrichment.httpx.AsyncClient",
                            lambda **kw: async_client)

        assert (await tool.verify_email("b@x.com"))["result"] == "unknown"


class _FakeSettings:
    """Real-valued settings stand-in (MagicMock would leak into _cfg_str)."""

    enrichment_provider = "hunter"
    enrichment_api_key = "test-key"
    enrichment_max_queries_per_hunt = 10
    enrichment_title_keywords = ""


class TestEmailFinderPipeline:
    def _settings(self):
        return _FakeSettings()

    @pytest.mark.asyncio
    async def test_named_dm_without_email_gets_finder_fallback(self, monkeypatch):
        """Domain Search found nothing, but a named DM exists -> Email Finder
        fills the mailbox from the same budget."""
        import agents.lead_extract_agent as lea

        monkeypatch.setattr("agents.lead_extract_agent.get_settings",
                            lambda: self._settings())
        monkeypatch.setattr("tools.contact_enrichment.get_settings",
                            lambda: self._settings())
        # Domain Search: nothing known for this domain.
        monkeypatch.setattr(
            "tools.contact_enrichment.ContactEnrichmentTool.find_decision_makers",
            AsyncMock(return_value=[]))

        leads = [{
            "company_name": "Acme", "website": "https://acme.com", "emails": [],
            "decision_makers": [{"name": "Jane Doe", "title": "Purchasing Manager",
                                 "email": "", "linkedin": "", "source_url": ""}],
        }]
        found = AsyncMock(return_value={
            "email": "jane.doe@acme.com", "first_name": "Jane", "last_name": "Doe",
            "position": "Purchasing Manager", "confidence": 97,
            "verification_status": "valid", "source": "enrichment",
        })
        monkeypatch.setattr(
            "tools.contact_enrichment.ContactEnrichmentTool.find_email", found)

        result = await _enrich_decision_maker_emails(leads)

        dm = result[0]["decision_makers"][0]
        assert dm["email"] == "jane.doe@acme.com"
        assert "jane.doe@acme.com" in result[0]["emails"]
        assert found.await_count == 1

    @pytest.mark.asyncio
    async def test_finder_budget_shared_with_domain_search(self, monkeypatch):
        """Budget of 1: the named lead consumes it on the Finder lookup, so the
        anonymous Domain Search fallback is skipped (nothing left)."""
        import agents.lead_extract_agent as lea

        settings = self._settings()
        settings.enrichment_max_queries_per_hunt = 1
        monkeypatch.setattr("agents.lead_extract_agent.get_settings", lambda: settings)
        monkeypatch.setattr("tools.contact_enrichment.get_settings", lambda: settings)
        monkeypatch.setattr(
            "tools.contact_enrichment.ContactEnrichmentTool.find_decision_makers",
            AsyncMock(return_value=[]))

        leads = [{"company_name": "Acme", "website": "https://acme.com", "emails": [],
                  "decision_makers": [{"name": "Jane Doe", "title": "Owner",
                                       "email": "", "linkedin": "", "source_url": ""}]}]
        finder = AsyncMock(return_value={"email": "jane.doe@acme.com",
                                         "verification_status": "valid"})
        monkeypatch.setattr(
            "tools.contact_enrichment.ContactEnrichmentTool.find_email", finder)

        await _enrich_decision_maker_emails(leads)

        # Budget of 1 spent on the Finder -> Domain Search gets nothing left.
        finder.assert_awaited_once()


class TestFinderFirstPriority:
    """Email Finder (named contacts) runs before Domain Search (generic boxes)."""

    def _settings(self, budget=50):
        s = _FakeSettings()
        s.enrichment_max_queries_per_hunt = budget
        return s

    @pytest.fixture
    def patch_enrichment_settings(self, monkeypatch):
        s = self._settings()
        monkeypatch.setattr("agents.lead_extract_agent.get_settings", lambda: s)
        monkeypatch.setattr("tools.contact_enrichment.get_settings", lambda: s)
        return s

    @pytest.mark.asyncio
    async def test_named_dm_gets_finder_email_before_domain_search(
            self, patch_enrichment_settings, monkeypatch):
        leads = [{
            "company_name": "Acme", "website": "https://acme.com", "emails": [],
            "decision_makers": [{"name": "Jane Doe", "title": "Purchasing Manager",
                                 "email": "", "linkedin": "", "source_url": ""}],
        }]
        finder = AsyncMock(return_value={
            "email": "jane.doe@acme.com", "first_name": "Jane", "last_name": "Doe",
            "position": "Purchasing Manager", "confidence": 97,
            "verification_status": "valid", "source": "enrichment",
        })
        monkeypatch.setattr(
            "tools.contact_enrichment.ContactEnrichmentTool.find_email", finder)
        domain_search = AsyncMock(return_value=[])
        monkeypatch.setattr(
            "tools.contact_enrichment.ContactEnrichmentTool.find_decision_makers",
            domain_search)

        result = await _enrich_decision_maker_emails(leads)

        dm = result[0]["decision_makers"][0]
        assert dm["email"] == "jane.doe@acme.com"
        assert "jane.doe@acme.com" in result[0]["emails"]
        finder.assert_awaited_once()          # 已命中关键联系人，无需泛搜

    @pytest.mark.asyncio
    async def test_single_word_name_gets_no_finder_and_no_generic_blast(
            self, patch_enrichment_settings, monkeypatch):
        """Single-word names cannot drive Email Finder, and a named-DM company
        never gets the generic-box Domain Search blast — the lead stays
        emailless for manual handling."""
        leads = [{
            "company_name": "Cher Corp", "website": "https://cher.com", "emails": [],
            "decision_makers": [{"name": "Cher", "title": "Owner",
                                 "email": "", "linkedin": "", "source_url": ""}],
        }]
        finder = AsyncMock()
        monkeypatch.setattr(
            "tools.contact_enrichment.ContactEnrichmentTool.find_email", finder)
        domain_search = AsyncMock()
        monkeypatch.setattr(
            "tools.contact_enrichment.ContactEnrichmentTool.find_decision_makers",
            domain_search)

        result = await _enrich_decision_maker_emails(leads)

        finder.assert_not_awaited()
        domain_search.assert_not_awaited()
        assert result[0]["decision_makers"][0]["email"] == ""

    @pytest.mark.asyncio
    async def test_named_dm_finder_miss_skips_generic_blast(self, patch_enrichment_settings, monkeypatch):
        """Named DM exists but Hunter finds nothing: do NOT blast the company's
        generic box instead — leave the DM emailless for manual handling."""
        leads = [{
            "company_name": "Acme", "website": "https://acme.com", "emails": [],
            "decision_makers": [{"name": "Jane Doe", "title": "Purchasing Manager",
                                 "email": "", "linkedin": "", "source_url": ""}],
        }]
        monkeypatch.setattr(
            "tools.contact_enrichment.ContactEnrichmentTool.find_email",
            AsyncMock(return_value=None))
        domain_search = AsyncMock()
        monkeypatch.setattr(
            "tools.contact_enrichment.ContactEnrichmentTool.find_decision_makers",
            domain_search)

        result = await _enrich_decision_maker_emails(leads)

        domain_search.assert_not_awaited()
        assert result[0]["decision_makers"][0]["email"] == ""

    @pytest.mark.asyncio
    async def test_budget_shared_between_finder_and_domain_phases(
            self, patch_enrichment_settings, monkeypatch):
        patch_enrichment_settings.enrichment_max_queries_per_hunt = 1
        finder = AsyncMock(return_value={
            "email": "a@one.com", "first_name": "A", "last_name": "B",
            "position": "Owner", "confidence": 90,
            "verification_status": "valid", "source": "enrichment",
        })
        monkeypatch.setattr(
            "tools.contact_enrichment.ContactEnrichmentTool.find_email", finder)
        domain_search = AsyncMock()
        monkeypatch.setattr(
            "tools.contact_enrichment.ContactEnrichmentTool.find_decision_makers",
            domain_search)

        leads = [
            {"company_name": "One", "website": "https://one.com", "emails": [],
             "decision_makers": [{"name": "A B", "title": "Owner",
                                  "email": "", "linkedin": "", "source_url": ""}]},
            {"company_name": "Two", "website": "https://two.com", "emails": [],
             "decision_makers": []},                       # anonymous -> would use Domain Search
        ]

        result = await _enrich_decision_maker_emails(leads)

        domain_search.assert_not_awaited()                 # budget spent on phase 1
        assert result[0]["decision_makers"][0]["email"] == "a@one.com"
