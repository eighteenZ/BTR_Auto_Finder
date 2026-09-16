"""Job execution control primitives shared by the queue consumers.

These live in a shipped package (``automation``) rather than in
``scripts/headless_worker.py``: the API service imports them, and
``scripts/`` is deliberately not part of the distributed wheel, so a
library import from there breaks wheel installs.
"""

from __future__ import annotations


class JobCancelledError(RuntimeError):
    """Raised when a queue job was cancelled while it was running."""


def campaign_name(prefix: str, hunt_id: str) -> str:
    """Derive a campaign label from the configured prefix and the hunt id."""
    return f"{prefix} {hunt_id[:8]}".strip()
