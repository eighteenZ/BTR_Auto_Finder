"""Tests for the email_drafts / campaign_jobs DB contract stores."""

from __future__ import annotations

from uuid import uuid4

from emailing.draft_store import CampaignJobQueue, EmailDraftStore, now_iso


def _draft_payload(hunt_id: str, index: int = 0, **overrides):
    payload = {
        "id": str(uuid4()),
        "hunt_id": hunt_id,
        "sequence_index": index,
        "lead_id": "lead-1",
        "lead_key": "d:example.com",
        "company_name": "Acme GmbH",
        "website": "https://acme.com",
        "locale": "de_DE",
        "target": {"target_email": "buyer@acme.com", "target_name": "Jane"},
        "targets": [{"target_email": "buyer@acme.com"}],
        "emails": [
            {"sequence_number": 1, "email_type": "company_intro", "subject": "Hi",
             "body_text": "Body", "suggested_send_day": 0},
        ],
        "language_choice": {"chosen_language": "de"},
        "strategy_brief": {"best_value_angles": ["price"]},
        "validation_summary": {"passed": True},
        "review_summary": {"status": "approved", "score": 90},
        "review_status": "approved",
        "generation_mode": "personalized",
        "template_id": "tpl_x",
        "template_group": "de_DE|general|v1",
        "template_usage_index": 1,
        "template_max_send_count": 100,
        "template_seed_source": "pre_generated",
        "status": "draft",
        "manual_review": {},
        "error": "",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    payload.update(overrides)
    return payload


class TestEmailDraftStore:
    def test_upsert_and_fetch_roundtrip(self):
        store = EmailDraftStore()
        hunt_id = str(uuid4())
        store.upsert_draft(_draft_payload(hunt_id))

        drafts = store.list_drafts_for_hunt(hunt_id)
        assert len(drafts) == 1
        assert drafts[0]["company_name"] == "Acme GmbH"
        assert drafts[0]["target"]["target_email"] == "buyer@acme.com"
        assert drafts[0]["emails"][0]["sequence_number"] == 1
        assert drafts[0]["status"] == "draft"

        by_index = store.get_draft_by_index(hunt_id, 0)
        assert by_index["id"] == drafts[0]["id"]

    def test_upsert_is_idempotent_on_hunt_and_index(self):
        store = EmailDraftStore()
        hunt_id = str(uuid4())
        store.upsert_draft(_draft_payload(hunt_id, subject_note="first"))
        store.upsert_draft(_draft_payload(hunt_id, company_name="Acme Renamed"))

        drafts = store.list_drafts_for_hunt(hunt_id)
        assert len(drafts) == 1
        assert drafts[0]["company_name"] == "Acme Renamed"

    def test_hunter_rewrite_preserves_manual_decision(self):
        store = EmailDraftStore()
        hunt_id = str(uuid4())
        store.upsert_draft(_draft_payload(hunt_id))
        draft_id = store.get_draft_by_index(hunt_id, 0)["id"]

        store.set_decision(draft_id, decision="approved", notes="ok")
        # Hunter re-writes the same (hunt_id, index) — approval must survive.
        store.upsert_draft(_draft_payload(hunt_id))

        refreshed = store.get_draft(draft_id)
        assert refreshed["status"] == "approved"
        assert refreshed["manual_review"]["decision"] == "approved"
        assert refreshed["manual_review"]["notes"] == "ok"

    def test_decision_reject_and_listing_filter(self):
        store = EmailDraftStore()
        hunt_id = str(uuid4())
        store.upsert_draft(_draft_payload(hunt_id, index=0))
        store.upsert_draft(_draft_payload(hunt_id, index=1))
        first = store.get_draft_by_index(hunt_id, 0)
        second = store.get_draft_by_index(hunt_id, 1)

        store.set_decision(first["id"], decision="approved")
        store.set_decision(second["id"], decision="rejected")

        assert store.count_drafts_for_hunt(hunt_id) == 2
        assert store.count_drafts_for_hunt(hunt_id, status="approved") == 1
        approved = store.list_drafts(status="approved", hunt_id=hunt_id)
        assert len(approved) == 1
        assert approved[0]["id"] == first["id"]

    def test_set_decision_validates_value(self):
        store = EmailDraftStore()
        try:
            store.set_decision("missing", decision="maybe")
        except ValueError as exc:
            assert "approved" in str(exc)
        else:
            raise AssertionError("expected ValueError")


class TestCampaignJobQueue:
    def test_enqueue_claim_complete_lifecycle(self):
        queue = CampaignJobQueue()
        hunt_id = str(uuid4())
        job = queue.enqueue(hunt_id, {"name": "Test campaign", "auto_start": True})

        assert job["status"] == "queued"

        claimed = queue.claim_next("worker-1")
        assert claimed is not None
        assert claimed["id"] == job["id"]
        assert claimed["status"] == "running"
        assert claimed["claimed_by"] == "worker-1"
        assert claimed["attempt_count"] == 1
        import json
        assert json.loads(claimed["payload_json"])["name"] == "Test campaign"

        assert queue.claim_next("worker-2") is None  # nothing else queued

        queue.mark_completed(job["id"], campaign_id="camp-1")
        finished = queue.get_job(job["id"])
        assert finished["status"] == "completed"
        assert finished["campaign_id"] == "camp-1"
        assert finished["last_error"] == ""

    def test_mark_failed_keeps_error(self):
        queue = CampaignJobQueue()
        job = queue.enqueue(str(uuid4()), {"name": "Broken"})
        queue.claim_next("worker-1")
        queue.mark_failed(job["id"], error="smtp_not_tested")

        failed = queue.get_job(job["id"])
        assert failed["status"] == "failed"
        assert failed["last_error"] == "smtp_not_tested"

    def test_list_jobs_for_hunt(self):
        queue = CampaignJobQueue()
        hunt_id = str(uuid4())
        queue.enqueue(hunt_id, {"name": "a"})
        queue.enqueue(hunt_id, {"name": "b"})
        queue.enqueue(str(uuid4()), {"name": "other"})

        assert len(queue.list_jobs(hunt_id=hunt_id)) == 2


class TestCampaignJobDeferral:
    def test_requeue_delays_claim_until_available(self):
        from datetime import datetime, timedelta, timezone

        queue = CampaignJobQueue()
        job = queue.enqueue(str(uuid4()), {"name": "defer"})

        claimed = queue.claim_next("worker-1")
        assert claimed is not None and claimed["id"] == job["id"]
        # attempt_count incremented by claim
        assert queue.get_job(job["id"])["attempt_count"] == 1

        queue.requeue(job["id"], delay_seconds=300)
        requeued = queue.get_job(job["id"])
        assert requeued["status"] == "queued"
        assert requeued["claimed_by"] == ""
        assert requeued["available_at"] > datetime.now(timezone.utc).isoformat()
        # deferral rolls the attempt back
        assert requeued["attempt_count"] == 0

        assert queue.claim_next("worker-2") is None  # still delayed

    def test_requeued_job_claimable_when_available_passes(self):
        queue = CampaignJobQueue()
        job = queue.enqueue(str(uuid4()), {"name": "soon"})
        queue.claim_next("w")
        queue.requeue(job["id"], delay_seconds=0)  # available immediately

        claimed = queue.claim_next("w2")
        assert claimed is not None and claimed["id"] == job["id"]


class TestDraftStorePlaceholderSanitization:
    """Drafts must never reach review containing raw placeholders."""

    def _settings(self, monkeypatch):
        class S:
            email_signature_name = "Wendy"
            email_signature_title = "Sales Manager"
            email_signature_phone = "+1 8607978125"
            email_from_name = "B2Binsights"
            email_from_address = "sales@example.com"

        monkeypatch.setattr("config.settings.get_settings", lambda: S())
        return S()

    def test_upsert_strips_placeholders_at_the_boundary(self, monkeypatch):
        self._settings(monkeypatch)
        store = EmailDraftStore()
        hunt_id = str(uuid4())
        payload = _draft_payload(hunt_id)
        payload["target"] = {"target_email": "buyer@acme.com", "target_name": "Jane Doe"}
        payload["emails"] = [
            {
                "sequence_number": 1,
                "email_type": "company_intro",
                "subject": "Intro from [Your Name]",
                "body_text": "Dear [Name],\n\nNote of [date].\n\nBest regards,\n[Your Name]\n[Your Phone]",
                "suggested_send_day": 0,
            }
        ]

        store.upsert_draft(payload)
        stored = store.get_draft_by_index(hunt_id, 0)

        subject = stored["emails"][0]["subject"]
        body = stored["emails"][0]["body_text"]
        assert "[" not in subject
        assert "[" not in body
        assert subject == "Intro from Wendy"
        assert body.startswith("Dear Jane Doe,")
        assert "+1 8607978125" in body
        assert "Note of" not in body or "Note" in body

    def test_upsert_from_sequences_also_sanitizes(self, monkeypatch):
        self._settings(monkeypatch)
        store = EmailDraftStore()
        hunt_id = str(uuid4())
        sequences = [
            {
                "lead": {"company_name": "Acme", "website": "https://acme.com"},
                "locale": "en_US",
                "target": {"target_email": "buyer@acme.com", "target_name": "Jane Doe"},
                "emails": [
                    {
                        "sequence_number": 1,
                        "email_type": "company_intro",
                        "subject": "Hello",
                        "body_text": "Dear [Name],\n\nBest regards,\n[Your Name] | [Email Address]",
                        "suggested_send_day": 0,
                    }
                ],
            }
        ]

        store.upsert_from_sequences(hunt_id, sequences)
        body = store.get_draft_by_index(hunt_id, 0)["emails"][0]["body_text"]

        assert "[" not in body
        assert body.startswith("Dear Jane Doe,")
        assert "Wendy" in body and "sales@example.com" in body


class TestUpdateEmailsAndEditProtection:
    def _settings(self, monkeypatch):
        class S:
            email_signature_name = "Wendy"
            email_signature_title = "Sales Manager"
            email_signature_phone = "+1 8607978125"
            email_from_name = "B2Binsights"
            email_from_address = "sales@example.com"

        monkeypatch.setattr("config.settings.get_settings", lambda: S())
        return S()

    def test_update_emails_on_pending_draft(self, monkeypatch):
        self._settings(monkeypatch)
        store = EmailDraftStore()
        hunt_id = str(uuid4())
        store.upsert_draft(_draft_payload(hunt_id))
        draft_id = store.get_draft_by_index(hunt_id, 0)["id"]

        updated = store.update_emails(draft_id, [
            {"sequence_number": 1, "email_type": "company_intro",
             "subject": "Revised subject", "body_text": "Revised body with [Your Phone] inside.",
             "suggested_send_day": 2},
        ])

        assert updated is not None
        assert updated["edited_by_review"] is True
        assert updated["status"] == "draft"                    # 状态不变
        # 净化在写入边界生效：修改稿里的占位符也被洗掉
        assert updated["emails"][0]["subject"] == "Revised subject"
        assert "[Your Phone]" not in updated["emails"][0]["body_text"]
        assert "+1 8607978125" in updated["emails"][0]["body_text"]

    def test_update_rejected_on_decided_draft(self, monkeypatch):
        self._settings(monkeypatch)
        store = EmailDraftStore()
        hunt_id = str(uuid4())
        store.upsert_draft(_draft_payload(hunt_id))
        draft_id = store.get_draft_by_index(hunt_id, 0)["id"]
        store.set_decision(draft_id, decision="approved")

        assert store.update_emails(draft_id, [{"sequence_number": 1, "subject": "x", "body_text": "y"}]) is None

    def test_reviewer_edits_survive_regeneration(self, monkeypatch):
        self._settings(monkeypatch)
        store = EmailDraftStore()
        hunt_id = str(uuid4())
        store.upsert_draft(_draft_payload(hunt_id))
        draft_id = store.get_draft_by_index(hunt_id, 0)["id"]

        edited = [{"sequence_number": 1, "email_type": "company_intro",
                   "subject": "Human wording", "body_text": "Human body", "suggested_send_day": 1}]
        assert store.update_emails(draft_id, edited) is not None
        # 编辑保存即补签名（update_emails 走同一 sanitize+ensure 边界）
        stored_after_edit = store.get_draft(draft_id)
        assert stored_after_edit["emails"][0]["body_text"].startswith("Human body")
        assert stored_after_edit["emails"][0]["body_text"].count("Wendy") == 1

        # 获客侧重写同一 (hunt, index)：emails 被保护，人工版本保留
        store.upsert_draft(_draft_payload(hunt_id, company_name="Acme Regenerated"))
        after = store.get_draft(draft_id)
        assert after["emails"][0]["subject"] == "Human wording"
        assert after["emails"][0]["body_text"] == stored_after_edit["emails"][0]["body_text"]
        assert after["company_name"] == "Acme Regenerated"     # 非内容字段照常更新
