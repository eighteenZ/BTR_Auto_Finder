"""Tests for the customs pipeline repository (ingest, classify, runs, watchlist)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from persistence import customs_repo
from tools.customs_sources.importyeti_source import SOURCE_NAME, TradeRecord


TODAY = date(2026, 9, 20)


def _record(company: str = "Acme Import Co", *, arrival: str = "", supplier: str = "Bright Factory",
            hs: str = "853650", quantity: str = "100 PCS") -> TradeRecord:
    return TradeRecord(
        company_name=company,
        consignee_name=company,
        supplier_name=supplier,
        country="China",
        country_code="cn",
        hs_code=hs,
        product_description="switches",
        arrival_date=arrival,
        quantity=quantity,
        weight="",
        source=SOURCE_NAME,
        source_ref="test.csv",
    ).finalize()


def test_classify_procurement_windows():
    in_window = [{"arrival_date": (TODAY - timedelta(days=30)).isoformat()}]
    mid = [{"arrival_date": (TODAY - timedelta(days=200)).isoformat()}]
    stale = [{"arrival_date": (TODAY - timedelta(days=500)).isoformat()}]

    active = customs_repo.classify_procurement(in_window, today=TODAY, active_months=6)
    assert active["procurement_status"] == "active"
    assert active["last_import_at"] == (TODAY - timedelta(days=30)).isoformat()

    recent = customs_repo.classify_procurement(mid, today=TODAY, active_months=6)
    assert recent["procurement_status"] == "recent"

    old = customs_repo.classify_procurement(stale, today=TODAY, active_months=6)
    assert old["procurement_status"] == "stale"

    empty = customs_repo.classify_procurement([], today=TODAY)
    assert empty["procurement_status"] == "none"

    undated = customs_repo.classify_procurement([{"arrival_date": ""}], today=TODAY)
    assert undated["procurement_status"] == "none"


def test_classify_counts_90d_shipments():
    records = [
        {"arrival_date": (TODAY - timedelta(days=10)).isoformat()},
        {"arrival_date": (TODAY - timedelta(days=80)).isoformat()},
        {"arrival_date": (TODAY - timedelta(days=120)).isoformat()},
    ]
    result = customs_repo.classify_procurement(records, today=TODAY)
    assert result["import_count_90d"] == 2


def test_ingest_creates_leads_records_and_dedups():
    first = customs_repo.ingest_trade_records([_record(arrival="2026-08-01"), _record(arrival="2026-08-02")])
    assert first["records_ingested"] == 2
    assert first["new_leads"] == 1

    # same records again -> all duplicates, no new lead
    second = customs_repo.ingest_trade_records([_record(arrival="2026-08-01")])
    assert second["records_ingested"] == 0
    assert second["records_skipped"] == 1
    assert second["new_leads"] == 0

    leads = customs_repo.list_procurement_leads(status="unknown")
    assert len(leads) == 1
    lead = leads[0]
    assert lead["company_name"] == "Acme Import Co"
    assert lead["procurement_status"] == "unknown"

    # a different company creates its own lead
    customs_repo.ingest_trade_records([_record("Beta Trading", arrival="2026-09-01")])
    assert len(customs_repo.list_procurement_leads()) == 2


def test_recompute_sets_active_and_bumps_priority():
    customs_repo.ingest_trade_records([
        _record(arrival=(TODAY - timedelta(days=20)).isoformat()),
        _record(arrival=(TODAY - timedelta(days=50)).isoformat(), hs="853690"),
        _record(arrival=(TODAY - timedelta(days=70)).isoformat(), hs="853610"),
    ])
    lead = customs_repo.list_procurement_leads()[0]

    result = customs_repo.recompute_lead_procurement(
        lead["lead_id"], today=TODAY, active_months=6, min_shipments_90d=2
    )
    assert result["procurement_status"] == "active"
    assert result["import_count_90d"] == 3

    refreshed = customs_repo.list_procurement_leads(status="active")
    assert len(refreshed) == 1
    assert refreshed[0]["priority_tier"] == "high"
    assert refreshed[0]["last_import_at"] == (TODAY - timedelta(days=20)).isoformat()


def test_recompute_stale_does_not_bump_priority():
    customs_repo.ingest_trade_records([_record(arrival=(TODAY - timedelta(days=900)).isoformat())])
    lead = customs_repo.list_procurement_leads()[0]
    lead_row = customs_repo.list_procurement_leads()[0]
    assert lead_row["priority_tier"] != "high"

    result = customs_repo.recompute_lead_procurement(
        lead["lead_id"], today=TODAY, active_months=6
    )
    assert result["procurement_status"] == "stale"


def test_save_lead_check_keeps_status_on_empty():
    customs_repo.ingest_trade_records([_record(arrival="2026-08-01")])
    lead = customs_repo.list_procurement_leads()[0]
    customs_repo.recompute_lead_procurement(lead["lead_id"], today=TODAY)

    assert customs_repo.save_lead_check(lead["lead_id"], status="", summary="No concrete customs data found") is True
    after = customs_repo.list_procurement_leads()[0]
    assert after["procurement_status"] == "active"  # not downgraded by an empty lookup
    assert after["customs_data"] == "No concrete customs data found"
    assert after["customs_last_checked_at"]

    assert customs_repo.save_lead_check("missing-lead", status="active") is False


def test_list_leads_for_check_orders_least_recently_checked():
    customs_repo.ingest_trade_records([_record("Alpha Co"), _record("Beta Co")])
    leads = customs_repo.list_leads_for_check(limit=5)
    assert len(leads) == 2
    assert all(lead["company_name"] for lead in leads)
    customs_repo.save_lead_check(leads[0]["lead_id"], status="none")
    again = customs_repo.list_leads_for_check(limit=5)
    assert again[-1]["lead_id"] == leads[0]["lead_id"]


def test_runs_bookkeeping_and_daily_idempotency():
    run_date = TODAY.isoformat()
    assert customs_repo.completed_run_for_date(run_date) is None

    run_id = customs_repo.start_run(run_date, trigger="scheduled")
    customs_repo.finish_run(
        run_id, status="ok", stats={"k": 1},
        files_processed=2, records_ingested=10, leads_checked=3, leads_active=2, new_leads=1,
    )
    assert customs_repo.completed_run_for_date(run_date) == run_id

    runs = customs_repo.list_runs(limit=5)
    assert runs[0]["id"] == run_id
    assert runs[0]["status"] == "ok"
    assert runs[0]["records_ingested"] == 10
    assert runs[0]["stats"] == {"k": 1}


def test_watchlist_crud_and_upsert():
    item = customs_repo.add_watch_item(hs_code="853650", product_keywords=["micro switch"], countries=["us"])
    assert item["hs_code"] == "853650"

    updated = customs_repo.add_watch_item(hs_code="853650", product_keywords=["rocker switch"], note="updated")
    assert updated["id"] == item["id"]
    assert updated["product_keywords"] == ["rocker switch"]
    assert len(customs_repo.list_watch_items()) == 1

    customs_repo.add_watch_item(hs_code="940320")
    enabled = customs_repo.list_watch_items(enabled_only=True)
    assert len(enabled) == 2

    assert customs_repo.remove_watch_item(item["id"]) is True
    assert customs_repo.remove_watch_item(item["id"]) is False
    assert len(customs_repo.list_watch_items()) == 1


def test_list_lead_ids_with_records():
    customs_repo.ingest_trade_records([_record("Alpha Co"), _record("Beta Co"), _record("Alpha Co", arrival="2026-07-01")])
    ids = customs_repo.list_lead_ids_with_records()
    assert len(ids) == 2
