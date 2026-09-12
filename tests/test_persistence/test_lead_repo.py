"""Tests for the persistent lead repository (global dedup / reuse)."""

from __future__ import annotations

from persistence import lead_repo


def _lead(**overrides):
    base = {
        "company_name": "Acme GmbH",
        "website": "https://www.acme.com/about",
        "emails": ["info@acme.com"],
        "phone_numbers": [],
    }
    base.update(overrides)
    return base


def test_upsert_creates_then_reuses_and_merges():
    first = lead_repo.upsert_leads([_lead()], hunt_id="h1", source_keyword="kw1")
    assert first[0]["reused"] is False
    assert first[0]["lead_key"] == "d:acme.com"

    second = lead_repo.upsert_leads(
        [_lead(website="https://acme.com/", emails=["sales@acme.com"], phone_numbers=["+1 555"])],
        hunt_id="h2",
        source_keyword="kw2",
    )
    assert second[0]["reused"] is True
    assert second[0]["seen_count"] == 2
    assert set(second[0]["emails"]) == {"info@acme.com", "sales@acme.com"}
    assert second[0]["phone_numbers"] == ["+1 555"]


def test_skip_mode_excludes_existing():
    lead_repo.upsert_leads([_lead()], hunt_id="h1")

    result = lead_repo.upsert_leads([_lead()], hunt_id="h2", dedup_mode="skip")

    assert result == []


def test_off_mode_returns_all_as_new():
    lead_repo.upsert_leads([_lead()], hunt_id="h1")

    result = lead_repo.upsert_leads([_lead()], hunt_id="h2", dedup_mode="off")

    assert len(result) == 1
    assert result[0]["reused"] is False
    assert result[0]["seen_count"] == 2


def test_batch_dedup_within_call():
    result = lead_repo.upsert_leads(
        [_lead(), _lead(emails=["other@acme.com"])],
        hunt_id="h1",
    )
    assert len(result) == 1
    assert set(result[0]["emails"]) == {"info@acme.com", "other@acme.com"}


def test_find_by_domains_and_sightings_and_hunt_links():
    created = lead_repo.upsert_leads([_lead()], hunt_id="h1", source_keyword="kw1")
    lead_id = created[0]["lead_id"]

    by_domain = lead_repo.find_by_domains(["acme.com"])
    assert "acme.com" in by_domain

    sightings = lead_repo.list_sightings(lead_id)
    assert any(s["hunt_id"] == "h1" for s in sightings)

    hunt_leads = lead_repo.list_hunt_leads("h1")
    assert len(hunt_leads) == 1
    assert hunt_leads[0]["lead_id"] == lead_id


def test_get_lead_and_list_leads():
    created = lead_repo.upsert_leads([_lead()], hunt_id="h1")
    lead_id = created[0]["lead_id"]

    assert lead_repo.get_lead(lead_id)["company_name"] == "Acme GmbH"
    assert lead_repo.get_lead("missing") is None
    assert [lead["lead_id"] for lead in lead_repo.list_leads()] == [lead_id]


def test_filter_known():
    lead_repo.upsert_leads([_lead()], hunt_id="h1")

    known, unknown = lead_repo.filter_known(
        [_lead(), _lead(website="https://other.com", company_name="Other")]
    )

    assert "d:acme.com" in known
    assert len(unknown) == 1
    assert unknown[0]["website"] == "https://other.com"
