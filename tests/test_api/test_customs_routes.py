"""Tests for the customs pipeline API routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.hunter_app import create_hunter_app
from services import customs_daily_service as svc


@pytest.fixture
def client():
    return TestClient(create_hunter_app())


def test_watchlist_crud(client):
    res = client.post("/api/v1/customs/watchlist", json={
        "hs_code": "8536.50",
        "product_keywords": ["micro switch"],
        "countries": ["us"],
        "note": "switches",
    })
    assert res.status_code == 200
    item = res.json()
    assert item["hs_code"] == "853650"  # digits only
    assert item["product_keywords"] == ["micro switch"]

    res = client.post("/api/v1/customs/watchlist", json={"hs_code": "abc"})
    assert res.status_code == 422  # too short / non-digit rejected by validation

    assert client.get("/api/v1/customs/watchlist").json()[0]["id"] == item["id"]

    assert client.delete(f"/api/v1/customs/watchlist/{item['id']}").json() == {"deleted": True}
    assert client.delete(f"/api/v1/customs/watchlist/{item['id']}").status_code == 404


def test_imports_endpoint_ingests_csv(client):
    csv_bytes = (
        "Consignee Name,Shipper Name,Country of Origin,Arrival Date\n"
        "Acme Import Co,Bright Factory,China,2026-08-01\n"
    )
    res = client.post(
        "/api/v1/customs/imports",
        files={"file": ("orders.csv", csv_bytes.encode("utf-8"), "text/csv")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["records_ingested"] == 1
    assert body["new_leads"] == 1

    res = client.post(
        "/api/v1/customs/imports",
        files={"file": ("orders.txt", b"junk", "text/plain")},
    )
    assert res.status_code == 400

    res = client.post(
        "/api/v1/customs/imports",
        files={"file": ("broken.csv", b"Consignee Name\n\x00\xff\xfe", "text/csv")},
    )
    assert res.status_code == 400


def test_runs_endpoint_executes_pipeline(client, monkeypatch, tmp_path):
    monkeypatch.setattr(svc, "ingest_import_dir", lambda import_dir: {
        "files_processed": 1, "files_failed": 0, "records_ingested": 3,
        "records_skipped": 0, "new_leads": 2, "failed_files": [],
    })
    monkeypatch.setattr(svc, "recompute_all", lambda **kw: {"leads_recomputed": 2, "leads_active": 1})
    monkeypatch.setattr(svc, "verify_leads_via_provider", _fake_verify)
    monkeypatch.setattr("api.customs_routes.run_customs_pipeline", svc.run_customs_pipeline)

    res = client.post("/api/v1/customs/runs", json={"force": True})
    assert res.status_code == 200
    body = res.json()
    assert body["trigger"] == "manual"
    assert body["ingest"]["records_ingested"] == 3

    runs = client.get("/api/v1/customs/runs").json()
    assert runs[0]["status"] == "ok"
    assert runs[0]["records_ingested"] == 3


def test_procurement_leads_and_export(client):
    res = client.get("/api/v1/customs/procurement-leads?status=active")
    assert res.status_code == 200
    assert res.json() == []

    res = client.get("/api/v1/customs/procurement-leads?status=bogus")
    assert res.status_code == 400

    res = client.get("/api/v1/customs/procurement-leads/export?status=active")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert int(res.headers["X-Lead-Count"]) == 0


async def _fake_verify(limit):
    return {"leads_checked": 2, "lookups_failed": 0, "importer_confirmed": 1}
