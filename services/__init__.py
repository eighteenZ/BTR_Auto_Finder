"""Service facade package — importable entry points for the acquisition engine."""

from services.hunter_service import HuntOutcome, HuntRequest, dedupe_leads, run_hunt

__all__ = ["HuntRequest", "HuntOutcome", "run_hunt", "dedupe_leads"]
