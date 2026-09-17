"""settings_store must read and write the same .env that Settings loads.

Regression: under a wheel install the write side derived .env from __file__
(site-packages) while the read side preferred the CWD, so anything saved
through the settings API (SMTP/IMAP credentials, LLM keys, feature flags,
the SMTP/IMAP test timestamps) silently landed in the venv and was lost on
restart. openclaw-ops hit this in production and worked around it with a
site-packages symlink.
"""

from __future__ import annotations

from pathlib import Path

from config import settings_store
from config.settings import _resolve_env_file, get_settings


def test_env_paths_agree():
    assert settings_store.get_env_path().resolve() == Path(_resolve_env_file()).resolve()


def test_update_settings_persists_where_settings_reads(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("EXISTING=1\n", encoding="utf-8")
    get_settings.cache_clear()
    try:
        settings_store.update_settings({"SMTP_TEST_FLAG": "abc123"})

        saved = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "SMTP_TEST_FLAG=abc123" in saved
        assert "EXISTING=1" in saved                                   # 既有内容不丢
        assert settings_store.read_settings().get("SMTP_TEST_FLAG") == "abc123"
    finally:
        get_settings.cache_clear()


def test_read_settings_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")               # CWD 优先命中空文件
    assert settings_store.read_settings() == {}
