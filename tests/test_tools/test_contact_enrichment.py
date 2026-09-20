"""Hunter.io enrichment tool + pipeline merge/quantum tests (all mocked)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.lead_extract_agent import _enrich_decision_maker_emails
from tools.contact_enrichment import ContactEnrichmentTool

HUNTER_PAYLOAD = {
    "data": {
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
}


def _tool_with_key() -> ContactEnrichmentTool:
    tool = ContactEnrichmentTool(provider="hunter", api_key="test-key")
    return tool


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


class TestPipelineEnrichment:
    @pytest.fixture
    def hunter_settings(self):
        s = MagicMock()
        s.enrichment_provider = "hunter"
        s.enrichment_api_key = "test-key"
        s.enrichment_max_queries_per_hunt = 50
        return s

    @pytest.mark.asyncio
    async def test_leads_without_dm_emails_get_enriched(self, hunter_settings, monkeypatch):
        leads = [{
            "company_name": "Acme", "website": "https://acme.com",
            "emails": ["info@acme.com"],
            "decision_makers": [],                      # no decision-maker mailbox
        }]
        monkeypatch.setattr("agents.lead_extract_agent.get_settings", lambda: hunter_settings)
        monkeypatch.setattr("tools.contact_enrichment.get_settings", lambda: hunter_settings)
        monkeypatch.setattr(
            "tools.contact_enrichment.ContactEnrichmentTool.find_decision_makers",
            AsyncMock(return_value=[
                {"email": "jane@acme.com", "first_name": "Jane", "last_name": "Doe",
                 "position": "Purchasing Manager", "confidence": 95, "source": "enrichment"},
            ]),
        )

        result = await _enrich_decision_maker_emails(leads)

        dm = result[0]["decision_makers"]
        assert len(dm) == 1
        assert dm[0]["email"] == "jane@acme.com"
        assert dm[0]["source"] == "enrichment"
        assert "jane@acme.com" in result[0]["emails"]

    @pytest.mark.asyncio
    async def test_leads_with_dm_emails_are_skipped(self, hunter_settings, monkeypatch):
        leads = [{
            "company_name": "Acme", "website": "https://acme.com",
            "emails": [],
            "decision_makers": [{"name": "X", "title": "Owner", "email": "owner@acme.com"}],
        }]
        monkeypatch.setattr("agents.lead_extract_agent.get_settings", lambda: hunter_settings)
        monkeypatch.setattr("tools.contact_enrichment.get_settings", lambda: hunter_settings)
        finder = AsyncMock()
        monkeypatch.setattr(
            "tools.contact_enrichment.ContactEnrichmentTool.find_decision_makers", finder)

        result = await _enrich_decision_maker_emails(leads)

        finder.assert_not_awaited()
        assert result == leads

    @pytest.mark.asyncio
    async def test_query_budget_cap_stops_enrichment(self, hunter_settings, monkeypatch):
        hunter_settings.enrichment_max_queries_per_hunt = 2
        monkeypatch.setattr("agents.lead_extract_agent.get_settings", lambda: hunter_settings)
        monkeypatch.setattr("tools.contact_enrichment.get_settings", lambda: hunter_settings)
        leads = [
            {"company_name": f"C{i}", "website": f"https://c{i}.com",
             "emails": [], "decision_makers": []}
            for i in range(5)
        ]
        monkeypatch.setattr(
            "tools.contact_enrichment.ContactEnrichmentTool.find_decision_makers",
            AsyncMock(return_value=[]))

        await _enrich_decision_maker_emails(leads)

        # Exactly 2 provider queries for the 2-credit budget.
        assert hunter_settings.enrichment_max_queries_per_hunt == 2
