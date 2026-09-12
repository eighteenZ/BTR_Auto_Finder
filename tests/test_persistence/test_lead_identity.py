"""Tests for canonical lead identity / dedupe-key generation."""

from __future__ import annotations

from persistence.lead_identity import (
    compute_lead_key,
    normalize_domain,
    normalize_email,
    official_domain,
)


def test_normalize_domain():
    assert normalize_domain("https://www.Example.com:8443/path") == "example.com"
    assert normalize_domain("http://sub.example.com/") == "sub.example.com"
    assert normalize_domain("acme.com") == "acme.com"
    assert normalize_domain("") == ""


def test_official_domain_excludes_platforms():
    assert official_domain("https://www.acme.com/about") == "acme.com"
    assert official_domain("https://linkedin.com/company/acme") == ""


def test_normalize_email():
    assert normalize_email("Info@Acme.com") == "info@acme.com"
    assert normalize_email("sales@acme.com (inferred)") == "sales@acme.com"
    assert normalize_email("not-an-email") == ""


def test_compute_lead_key_prefers_domain():
    key = compute_lead_key({"website": "https://www.acme.com", "emails": ["a@acme.com"]})
    assert key == "d:acme.com"


def test_compute_lead_key_falls_back_to_email():
    key = compute_lead_key(
        {"website": "https://linkedin.com/company/acme", "emails": ["A@Acme.com"]}
    )
    assert key == "e:a@acme.com"


def test_compute_lead_key_hash_fallback():
    key = compute_lead_key({"company_name": "Acme GmbH", "country_code": "DE"})
    assert key.startswith("x:")
    assert compute_lead_key({"company_name": "Acme GmbH", "country_code": "DE"}) == key


def test_compute_lead_key_empty():
    assert compute_lead_key({}) == ""
