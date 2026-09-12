"""Tests for PostgreSQL-backed hunt storage."""

from __future__ import annotations

from api.hunt_store import delete_hunt, load_all_hunts, load_hunt, save_hunt


def test_save_load_delete_roundtrip():
    save_hunt(
        "hunt-1",
        {"status": "running", "website_url": "https://acme.com", "result": {"leads": []}},
    )

    loaded = load_hunt("hunt-1")
    assert loaded is not None
    assert loaded["status"] == "running"
    assert loaded["website_url"] == "https://acme.com"

    delete_hunt("hunt-1")
    assert load_hunt("hunt-1") is None


def test_save_hunt_upserts():
    save_hunt("hunt-2", {"status": "pending"})
    save_hunt("hunt-2", {"status": "completed", "leads_count": 3})

    loaded = load_hunt("hunt-2")
    assert loaded["status"] == "completed"

    all_hunts = load_all_hunts()
    assert all_hunts["hunt-2"]["status"] == "completed"


def test_load_missing_returns_none():
    assert load_hunt("does-not-exist") is None
