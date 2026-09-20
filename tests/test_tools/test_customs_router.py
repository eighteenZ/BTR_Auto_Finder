import pytest

from tools.customs_router import (
    _extract_shipment_count,
    build_customs_queries,
    find_customs_data,
)


class DummyGoogle:
    def __init__(self, results):
        self._results = results

    async def search(self, query, num=5):
        return list(self._results)


class DummyJina:
    def __init__(self, text):
        self._text = text

    async def read(self, url):
        return self._text


def test_build_customs_queries_includes_provider_and_context():
    queries = build_customs_queries(
        "Acme GmbH",
        website="https://acme.de",
        country="Germany",
        product_keywords=["micro switch", "rotary switch"],
    )
    assert any("site:importgenius.com/importers" in q for q in queries)
    assert any("site:importyeti.com" in q for q in queries)
    assert any('"Acme GmbH" "acme.de" import export' == q for q in queries)
    assert any("micro switch rotary switch" in q for q in queries)


def test_extract_shipment_count_picks_largest_and_ignores_noise():
    text = "3 shipments to the U.S. 1,284 total shipments since 2015. 2024 shipments record."
    assert _extract_shipment_count(text) == 1284
    assert _extract_shipment_count("no numbers here") == 0


@pytest.mark.asyncio
async def test_find_customs_data_returns_structured_evidence(monkeypatch):
    google = DummyGoogle([
        {
            "title": "Acme Gmbh | See Full Importer History | ImportGenius",
            "link": "https://www.importgenius.com/importers/acme-gmbh",
            "snippet": "Importer Acme Gmbh shipments from Vietnam to Germany in 2024. HS code 853650.",
        }
    ])
    jina = DummyJina("")

    async def fake_fetch_provider_page(provider, url, jina_reader):
        return (
            "Acme Gmbh importer shipments from Vietnam to Germany in 2024. "
            "HS code 853650. Import records for micro switch.",
            "raw_fetch",
            "",
        )

    monkeypatch.setattr("tools.customs_router._fetch_provider_page", fake_fetch_provider_page)

    result = await find_customs_data(
        company_name="Acme GmbH",
        website="https://acme.de",
        country="Germany",
        product_keywords=["micro switch"],
        google_search=google,
        jina_reader=jina,
    )

    assert result["status"] == "ok"
    assert result["evidence"]
    assert result["evidence"][0]["provider"] == "importgenius"
    assert result["evidence"][0]["trade_direction"] == "import"
    assert "Vietnam" in result["evidence"][0]["partner_countries"]
    assert "853650" in result["evidence"][0]["hs_codes"]


@pytest.mark.asyncio
async def test_find_customs_data_returns_no_data_when_only_invalid_pages(monkeypatch):
    google = DummyGoogle([
        {
            "title": "Volza.com - Global Export Import Trade Data of 203 Countries",
            "link": "https://www.volza.com/company-profile/acme-gmbh/",
            "snippet": "Acme GmbH",
        }
    ])
    jina = DummyJina("")

    async def fake_fetch_provider_page(provider, url, jina_reader):
        return (
            "Warning: Target URL returned error 404: Not Found. "
            "Choose the Largest & Most Trusted Export-Import Trade Data Platform.",
            "jina_reader",
            "",
        )

    monkeypatch.setattr("tools.customs_router._fetch_provider_page", fake_fetch_provider_page)

    result = await find_customs_data(
        company_name="Acme GmbH",
        google_search=google,
        jina_reader=jina,
    )

    assert result["status"] == "no_data"
    assert result["summary"] == "No concrete customs data found"
