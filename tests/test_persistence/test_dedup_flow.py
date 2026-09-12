"""Integration tests for lead reuse / skip inside the extraction flow."""

from __future__ import annotations

import pytest

from agents.lead_extract_agent import lead_extract_node


def _state(search_results, dedup_mode="reuse"):
    return {
        "search_results": search_results,
        "leads": [],
        "target_lead_count": 10,
        "insight": {},
        "keyword_search_stats": {},
        "hunt_id": "h1",
        "dedup_mode": dedup_mode,
    }


_KNOWN = {
    "company_name": "Acme GmbH",
    "website": "https://acme.com",
    "emails": ["info@acme.com"],
    "lead_id": "lead-1",
    "lead_key": "d:acme.com",
}


def _lookup(_domains):
    return {"acme.com": _KNOWN}


@pytest.mark.asyncio
async def test_known_lead_is_reused_and_not_scraped():
    state = _state([{"link": "https://www.acme.com/", "title": "Acme"}])

    result = await lead_extract_node(state, lead_lookup=_lookup, dedup_mode="reuse")

    assert len(result["leads"]) == 1
    assert result["leads"][0]["reused"] is True
    assert result["leads"][0]["lead_id"] == "lead-1"


@pytest.mark.asyncio
async def test_skip_mode_drops_known_lead():
    state = _state([{"link": "https://www.acme.com/", "title": "Acme"}])

    result = await lead_extract_node(state, lead_lookup=_lookup, dedup_mode="skip")

    assert result["leads"] == []


@pytest.mark.asyncio
async def test_lookup_failure_falls_back_to_fresh(caplog):
    def _boom(_domains):
        raise RuntimeError("db down")

    state = _state([{"link": "https://www.acme.com/", "title": "Acme"}], dedup_mode="reuse")
    state["target_lead_count"] = 1
    state["leads"] = [
        {
            "company_name": "Existing",
            "website": "https://existing.com",
            "emails": ["a@existing.com"],
        }
    ]

    result = await lead_extract_node(state, lead_lookup=_boom, dedup_mode="reuse")

    assert result["current_stage"] == "lead_extract"
    assert len(result["leads"]) == 1
    assert result["leads"][0]["company_name"] == "Existing"
