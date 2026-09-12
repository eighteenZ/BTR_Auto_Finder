"""Tests for the direct-import hunter service facade."""

from __future__ import annotations

import pytest

from services import hunter_service
from services.hunter_service import HuntOutcome, HuntRequest, dedupe_leads, run_hunt


class _FakeGraph:
    async def astream(self, state):
        yield {
            "lead_extract": {
                "leads": [
                    {
                        "company_name": "Acme",
                        "website": "https://acme.com",
                        "emails": ["a@acme.com"],
                    },
                    {
                        "company_name": "Acme",
                        "website": "https://acme.com",
                        "emails": ["b@acme.com"],
                    },
                    {"company_name": "Beta", "website": "https://beta.com", "emails": []},
                ],
                "hunt_round": 1,
                "current_stage": "evaluate",
            }
        }
        yield {
            "evaluate": {
                "hunt_round": 2,
                "current_stage": "done",
                "insight": {"company_name": "X"},
            }
        }


def test_dedupe_leads_priority():
    leads = [
        {"website": "https://a.com", "company_name": "A"},
        {"company_name": "B"},
        {"emails": ["c@x.com"]},
        {"company_name": "B", "website": ""},
    ]

    result = dedupe_leads(leads)

    assert len(result) == 3


def test_hunt_request_defaults():
    request = HuntRequest()

    assert request.target_lead_count == 200
    assert request.max_rounds == 10
    assert request.min_new_leads_threshold == 5
    assert request.enable_email_craft is False


@pytest.mark.asyncio
async def test_run_hunt_accumulates_and_dedupes(monkeypatch):
    monkeypatch.setattr(hunter_service, "build_graph", lambda **_: _FakeGraph())

    outcome = await run_hunt(HuntRequest(website_url="https://x.com", target_lead_count=5))

    assert isinstance(outcome, HuntOutcome)
    assert outcome.status == "completed"
    assert outcome.hunt_round == 2
    assert len(outcome.leads) == 2
    assert outcome.insight == {"company_name": "X"}
    assert outcome.error is None


@pytest.mark.asyncio
async def test_run_hunt_reports_failure(monkeypatch):
    class _BoomGraph:
        async def astream(self, state):
            raise RuntimeError("boom")
            yield  # pragma: no cover

    monkeypatch.setattr(hunter_service, "build_graph", lambda **_: _BoomGraph())

    outcome = await run_hunt(HuntRequest())

    assert outcome.status == "failed"
    assert outcome.error == "boom"
