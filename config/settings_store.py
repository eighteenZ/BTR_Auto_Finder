"""Read and write the user .env configuration file."""

from __future__ import annotations

import platform
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


_LLM_KEYS = {
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "ZAI_API_KEY",
    "MOONSHOT_API_KEY",
    "MINIMAX_API_KEY",
}


def get_env_path() -> Path:
    """Return the effective .env file path for dev or packaged mode.

    Delegates to ``config.settings._resolve_env_file`` so the write path and
    the read path always resolve to the same file. They used to disagree under
    a wheel install (write side derived site-packages/.env from __file__,
    read side preferred the CWD): settings saved from the API silently landed
    in the venv and were lost on restart. Ops workaround for that bug was a
    site-packages symlink — with this fix the symlink is unnecessary and can
    be removed.
    """
    if getattr(sys, "frozen", False):
        system = platform.system()
        if system == "Darwin":
            base = Path.home() / "Library" / "Application Support" / "AIHunter"
        elif system == "Windows":
            import os

            base = Path(os.environ.get("APPDATA", str(Path.home()))) / "AIHunter"
        else:
            base = Path.home() / ".config" / "AIHunter"
        base.mkdir(parents=True, exist_ok=True)
        return base / ".env"

    from config.settings import _resolve_env_file

    return Path(_resolve_env_file())


def read_settings() -> dict[str, str]:
    """Parse the .env file into a KEY -> value mapping."""
    path = get_env_path()
    if not path.exists():
        return {}

    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def write_settings(data: dict[str, str]) -> None:
    """Overwrite the .env file with the provided mapping."""
    path = get_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{key}={value}\n" for key, value in data.items()), encoding="utf-8")


def update_settings(updates: dict[str, str]) -> None:
    """Merge updates into the existing .env file."""
    existing = read_settings()
    existing.update(updates)
    write_settings(existing)


def is_configured() -> bool:
    """Return True when at least one LLM key is configured."""
    settings = read_settings()
    return any(settings.get(key, "").strip() for key in _LLM_KEYS)
