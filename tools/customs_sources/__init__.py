"""Pluggable customs-data sources.

Each source normalizes raw provider data into ``TradeRecord`` rows that the
customs pipeline ingests. Today: ImportYeti file exports; later: provider
APIs behind the same interface.
"""

from tools.customs_sources.importyeti_source import SOURCE_NAME, TradeRecord, parse_file

__all__ = ["SOURCE_NAME", "TradeRecord", "parse_file"]
