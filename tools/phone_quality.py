"""Phone quality: line-type classification and contact-value ranking.

Scraped phone numbers are mostly company landlines — a fixed line can never
be a decision-maker's WhatsApp, while a mobile line plausibly is. This module
classifies numbers offline (libphonenumber metadata, no network) and ranks
them so mobile lines surface first.
"""

from __future__ import annotations

from typing import Any

import phonenumbers
from phonenumbers import PhoneNumberType

# Line types that can belong to a person (and plausibly carry WhatsApp).
_PERSONAL_TYPES = {PhoneNumberType.MOBILE}

_LABELS = {
    PhoneNumberType.MOBILE: "手机",
    PhoneNumberType.FIXED_LINE: "固话",
}


def _parse(raw: str, country: str):
    try:
        return phonenumbers.parse(str(raw or ""), country or None)
    except (phonenumbers.NumberParseException, TypeError, ValueError):
        return None


def line_type(raw: str, country: str = "") -> str:
    """Return 'mobile' | 'fixed' | '' (unknown/unparseable) for one number."""
    number = _parse(raw, country)
    if number is None or not phonenumbers.is_valid_number(number):
        return ""
    ntype = phonenumbers.number_type(number)
    if ntype in _PERSONAL_TYPES:
        return "mobile"
    if ntype == PhoneNumberType.FIXED_LINE:
        return "fixed"
    return ""


def label_for(raw: str, country: str = "") -> str:
    """Chinese label for the line type ('' when unknown/unparseable)."""
    number = _parse(raw, country)
    if number is None or not phonenumbers.is_valid_number(number):
        return ""
    return _LABELS.get(phonenumbers.number_type(number), "")


def classify_and_rank_phones(
    phones: list[str], country: str = "",
) -> tuple[list[str], list[dict[str, str]]]:
    """Sort phones mobile-first and classify each.

    Returns ``(sorted_numbers, meta)`` where meta is aligned with the input
    order and carries ``{"number", "line_type", "label"}`` per phone.
    """
    meta: list[dict[str, str]] = []
    for raw in phones or []:
        lt = line_type(raw, country)
        label = label_for(raw, country)
        meta.append({"number": str(raw), "line_type": lt, "label": label})

    def _rank(item: dict[str, str]) -> int:
        return {"mobile": 0, "fixed": 1}.get(item["line_type"], 2)

    ranked = [m["number"] for m in sorted(meta, key=_rank)]
    return ranked, meta
