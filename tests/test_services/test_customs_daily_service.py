"""Tests for the daily customs pipeline service (scheduling + dir ingestion)."""

from __future__ import annotations

import csv
from datetime import datetime

import pytest

from automation.notifier import render_customs_daily_text
from services import customs_daily_service as svc


def _write_csv(path, rows, headers):
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


HEADERS = ["Consignee Name", "Shipper Name", "Country of Origin", "Arrival Date"]


def test_run_time_reached():
    now = datetime(2026, 9, 20, 8, 0)
    assert svc.run_time_reached("08:00", now) is True
    assert svc.run_time_reached("07:59", now) is True
    assert svc.run_time_reached("08:01", now) is False
    assert svc.run_time_reached("", now) is True  # invalid -> default 08:00
    assert svc.run_time_reached("bad", now) is True


def test_ingest_import_dir_processes_and_archives(tmp_path):
    _write_csv(tmp_path / "orders.csv", [
        {"Consignee Name": "Acme Import Co", "Shipper Name": "Bright", "Country of Origin": "China", "Arrival Date": "2026-08-01"},
        {"Consignee Name": "Acme Import Co", "Shipper Name": "Bright", "Country of Origin": "China", "Arrival Date": "2026-08-02"},
    ], HEADERS)

    summary = svc.ingest_import_dir(str(tmp_path))

    assert summary["files_processed"] == 1
    assert summary["records_ingested"] == 2
    assert summary["new_leads"] == 1
    assert not (tmp_path / "orders.csv").exists()
    assert (tmp_path / "processed" / "orders.csv").exists()

    # re-dropping the same file: records dedup by hash, file still archived
    _write_csv(tmp_path / "orders2.csv", [
        {"Consignee Name": "Acme Import Co", "Shipper Name": "Bright", "Country of Origin": "China", "Arrival Date": "2026-08-01"},
    ], HEADERS)
    again = svc.ingest_import_dir(str(tmp_path))
    assert again["records_ingested"] == 0
    assert again["records_skipped"] == 1


def test_ingest_import_dir_moves_broken_file_to_failed(tmp_path):
    (tmp_path / "broken.xlsx").write_bytes(b"this is not a real xlsx")

    summary = svc.ingest_import_dir(str(tmp_path))

    assert summary["files_processed"] == 0
    assert summary["files_failed"] == 1
    assert summary["failed_files"] == ["broken.xlsx"]
    assert (tmp_path / "failed" / "broken.xlsx").exists()


def test_ingest_import_dir_missing_dir(tmp_path):
    summary = svc.ingest_import_dir(str(tmp_path / "nope"))
    assert summary["files_processed"] == 0


def test_render_customs_daily_text():
    text = render_customs_daily_text({
        "run_date": "2026-09-20",
        "ingest": {"files_processed": 2, "records_ingested": 120, "records_skipped": 5, "new_leads": 40, "failed_files": ["bad.xlsx"]},
        "recompute": {"leads_recomputed": 200, "leads_active": 31},
        "verify": {"leads_checked": 10, "importer_confirmed": 4, "lookups_failed": 1},
    })
    assert "2026-09-20" in text
    assert "120" in text
    assert "采购活跃" in text
    assert "bad.xlsx" in text


@pytest.mark.asyncio
async def test_verify_leads_via_provider_monkeypatched(monkeypatch):
    leads = [
        {"lead_id": "l1", "company_name": "Acme", "website": "", "country_code": "us", "procurement_status": "unknown", "customs_data": ""},
    ]
    saved = []

    monkeypatch.setattr(svc.customs_repo, "list_leads_for_check", lambda limit: leads)
    monkeypatch.setattr(svc.customs_repo, "save_lead_check", lambda *a, **kw: saved.append((a, kw)) or True)

    async def fake_find(**kwargs):
        return {
            "status": "ok",
            "summary": "importyeti: 1284 shipments",
            "evidence": [{"provider": "importyeti", "shipment_count": 1284}],
        }

    monkeypatch.setattr(svc, "find_customs_data", fake_find)

    summary = await svc.verify_leads_via_provider(limit=5)

    assert summary == {"leads_checked": 1, "lookups_failed": 0, "importer_confirmed": 1}
    assert saved[0][1]["status"] == "recent"


@pytest.mark.asyncio
async def test_verify_keeps_status_when_no_evidence(monkeypatch):
    leads = [
        {"lead_id": "l2", "company_name": "Beta", "website": "", "country_code": "us", "procurement_status": "active", "customs_data": ""},
    ]
    saved = []
    monkeypatch.setattr(svc.customs_repo, "list_leads_for_check", lambda limit: leads)
    monkeypatch.setattr(svc.customs_repo, "save_lead_check", lambda *a, **kw: saved.append(kw) or True)

    async def fake_find(**kwargs):
        return {"status": "no_data", "summary": "No concrete customs data found", "evidence": []}

    monkeypatch.setattr(svc, "find_customs_data", fake_find)

    summary = await svc.verify_leads_via_provider(limit=5)

    assert summary["leads_checked"] == 1
    assert summary["importer_confirmed"] == 0
    assert saved[0]["status"] == ""  # must not downgrade an active lead
