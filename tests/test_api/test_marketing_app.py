"""Tests for the marketing service surface: draft review, manual send, and
the campaign_jobs consumer."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from api.marketing_app import create_marketing_app, run_campaign_job_once
from emailing.draft_store import CampaignJobQueue, EmailDraftStore


def _seed_draft(hunt_id: str = "hunt_m1", index: int = 0, **overrides) -> str:
    store = EmailDraftStore()
    sequence = {
        "lead": {"company_name": "Acme", "website": "https://acme.com"},
        "locale": "en_US",
        "target": {
            "target_email": "buyer@acme.com",
            "target_name": "Jane",
            "target_title": "Purchasing Manager",
            "target_type": "decision_maker_verified",
        },
        "emails": [
            {"sequence_number": 1, "email_type": "company_intro", "subject": "Hi",
             "body_text": "Body one", "suggested_send_day": 0},
            {"sequence_number": 2, "email_type": "product_showcase", "subject": "Hi2",
             "body_text": "Body two", "suggested_send_day": 3},
            {"sequence_number": 3, "email_type": "partnership_proposal", "subject": "Hi3",
             "body_text": "Body three", "suggested_send_day": 7},
        ],
        "generation_mode": "personalized",
        "manual_review": {"decision": "approved"},
    }
    sequence.update(overrides)
    store.upsert_from_sequences(hunt_id, [sequence])
    return store.get_draft_by_index(hunt_id, index)["id"]


class TestDraftDecisionApi:
    def test_decision_endpoint_approves_draft(self):
        app = create_marketing_app()
        client = TestClient(app)
        draft_id = _seed_draft()

        res = client.post(f"/api/v1/email-drafts/{draft_id}/decision", json={"decision": "approved", "notes": "ok"})
        assert res.status_code == 200
        body = res.json()
        assert body["draft_id"] == draft_id
        assert body["decision"] == "approved"

        refreshed = EmailDraftStore().get_draft(draft_id)
        assert refreshed["status"] == "approved"
        assert refreshed["manual_review"]["notes"] == "ok"

    def test_legacy_hunt_index_path_maps_to_draft(self):
        app = create_marketing_app()
        client = TestClient(app)
        draft_id = _seed_draft(hunt_id="hunt_m2", index=0)

        res = client.post("/api/v1/hunts/hunt_m2/email-sequences/0/decision", json={"decision": "rejected"})
        assert res.status_code == 200
        assert res.json()["draft_id"] == draft_id
        assert EmailDraftStore().get_draft(draft_id)["status"] == "rejected"

    def test_list_drafts_for_hunt(self):
        app = create_marketing_app()
        client = TestClient(app)
        _seed_draft(hunt_id="hunt_m3", index=0)

        res = client.get("/api/v1/hunts/hunt_m3/email-drafts")
        assert res.status_code == 200
        assert len(res.json()) == 1
        assert res.json()[0]["company_name"] == "Acme"


class TestDraftManualSend:
    def test_send_requires_approval(self, monkeypatch):
        app = create_marketing_app()
        client = TestClient(app)
        draft_id = _seed_draft(hunt_id="hunt_m4", manual_review={})

        res = client.post(f"/api/v1/email-drafts/{draft_id}/send", json={"sequence_number": 1})
        assert res.status_code == 409

    def test_send_dispatches_step_via_smtp(self, monkeypatch):
        app = create_marketing_app()
        client = TestClient(app)
        draft_id = _seed_draft(hunt_id="hunt_m5")

        sent: dict = {}
        monkeypatch.setattr(
            "api.email_routes.send_smtp_email",
            lambda settings, *, to_address, subject, body_text: sent.update(
                {"to": to_address, "subject": subject, "body": body_text}
            )
            or {"status": "sent", "to_address": to_address, "subject": subject},
        )

        res = client.post(f"/api/v1/email-drafts/{draft_id}/send", json={"sequence_number": 2})
        assert res.status_code == 200
        body = res.json()
        assert body["sent_to"] == "buyer@acme.com"
        assert body["sequence_number"] == 2
        assert sent["subject"] == "Hi2"
        assert sent["body"] == "Body two"


class TestCampaignJobConsumer:
    @pytest.mark.asyncio
    async def test_job_creates_and_starts_campaign_from_approved_drafts(self, monkeypatch):
        _seed_draft(hunt_id="hunt_m6")

        class FakeSettings:
            email_db_path = ""
            email_provider_type = "smtp"
            email_from_name = "B2Binsights"
            email_from_address = "sales@example.com"
            email_reply_to = "sales@example.com"
            email_smtp_host = "smtp.example.com"
            email_smtp_port = 587
            email_smtp_username = "sales@example.com"
            email_smtp_password = "secret"
            email_smtp_last_test_at = "2026-04-04T10:00:00Z"
            email_imap_host = ""
            email_imap_port = 993
            email_imap_username = ""
            email_imap_password = ""
            email_use_tls = True
            email_daily_send_limit = 50
            email_hourly_send_limit = 10
            email_language_mode = "auto_by_region"
            email_default_language = "en"
            email_fallback_language = "en"
            email_tone = "professional"
            email_step1_delay_days = 0
            email_step2_delay_days = 3
            email_step3_delay_days = 3
            email_min_fit_score_to_send = 0.6
            email_min_contactability_score_to_send = 0.45

        monkeypatch.setattr("api.email_routes.get_settings", lambda: FakeSettings())

        queue = CampaignJobQueue()
        job = queue.enqueue("hunt_m6", {"name": "Auto campaign", "auto_start": True})

        worked = await run_campaign_job_once()
        assert worked is True

        finished = queue.get_job(job["id"])
        assert finished["status"] == "completed"
        assert finished["campaign_id"]

        from emailing.store import EmailStore

        campaign = EmailStore().get_campaign(finished["campaign_id"])
        assert campaign["status"] == "active"
        assert campaign["name"] == "Auto campaign"

    @pytest.mark.asyncio
    async def test_no_jobs_returns_false(self):
        assert await run_campaign_job_once() is False


class TestCampaignJobApprovalDeferral:
    def _fake_settings(self):
        class FakeSettings:
            email_db_path = ""
            email_provider_type = "smtp"
            email_from_name = "B2Binsights"
            email_from_address = "sales@example.com"
            email_reply_to = "sales@example.com"
            email_smtp_host = "smtp.example.com"
            email_smtp_port = 587
            email_smtp_username = "sales@example.com"
            email_smtp_password = "secret"
            email_smtp_last_test_at = "2026-04-04T10:00:00Z"
            email_imap_host = ""
            email_imap_port = 993
            email_imap_username = ""
            email_imap_password = ""
            email_use_tls = True
            email_daily_send_limit = 50
            email_hourly_send_limit = 10
            email_language_mode = "auto_by_region"
            email_default_language = "en"
            email_fallback_language = "en"
            email_tone = "professional"
            email_step1_delay_days = 0
            email_step2_delay_days = 3
            email_step3_delay_days = 3
            email_min_fit_score_to_send = 0.6
            email_min_contactability_score_to_send = 0.45
        return FakeSettings()

    @pytest.mark.asyncio
    async def test_undecided_drafts_defer_the_job(self, monkeypatch):
        monkeypatch.setattr("api.email_routes.get_settings", lambda: self._fake_settings())
        _seed_draft(hunt_id="hunt_d1", manual_review={})  # undecided

        queue = CampaignJobQueue()
        job = queue.enqueue("hunt_d1", {"name": "deferred"})

        worked = await run_campaign_job_once()
        assert worked is True

        deferred = queue.get_job(job["id"])
        assert deferred["status"] == "queued"
        assert deferred["available_at"] != ""
        assert deferred["attempt_count"] == 0

    @pytest.mark.asyncio
    async def test_all_rejected_closes_job_without_campaign(self, monkeypatch):
        monkeypatch.setattr("api.email_routes.get_settings", lambda: self._fake_settings())
        draft_id = _seed_draft(hunt_id="hunt_d2", manual_review={})
        EmailDraftStore().set_decision(draft_id, decision="rejected")

        queue = CampaignJobQueue()
        job = queue.enqueue("hunt_d2", {"name": "rejected-all"})

        await run_campaign_job_once()

        closed = queue.get_job(job["id"])
        assert closed["status"] == "completed"
        assert closed["campaign_id"] == ""

    @pytest.mark.asyncio
    async def test_approval_then_run_builds_and_starts_campaign(self, monkeypatch):
        monkeypatch.setattr("api.email_routes.get_settings", lambda: self._fake_settings())
        _seed_draft(hunt_id="hunt_d3", manual_review={})  # undecided

        queue = CampaignJobQueue()
        job = queue.enqueue("hunt_d3", {"name": "after approval", "auto_start": True})
        await run_campaign_job_once()  # defers
        assert queue.get_job(job["id"])["status"] == "queued"

        drafts = EmailDraftStore().list_drafts_for_hunt("hunt_d3")
        EmailDraftStore().set_decision(drafts[0]["id"], decision="approved")

        # simulate the delayed availability having passed
        from persistence.db import execute as _execute, get_session as _get_session
        with _get_session() as session:
            _execute(session, "UPDATE campaign_jobs SET available_at = '' WHERE id = ?", (job["id"],))

        await run_campaign_job_once()

        done = queue.get_job(job["id"])
        assert done["status"] == "completed"
        assert done["campaign_id"]

        from emailing.store import EmailStore

        campaign = EmailStore().get_campaign(done["campaign_id"])
        assert campaign["status"] == "active"


class TestReviewPage:
    def test_review_page_served(self):
        app = create_marketing_app()
        client = TestClient(app)
        res = client.get("/review")
        assert res.status_code == 200
        assert "邮件草稿审批" in res.text
        assert "email-drafts" in res.text
