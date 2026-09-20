"""Tests for the ImportYeti file-import adapter (CSV/XLSX -> TradeRecord)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from tools.customs_sources.importyeti_source import (
    TradeRecord,
    detect_column_mapping,
    normalize_country_code,
    normalize_date,
    normalize_hs_code,
    parse_file,
    record_from_row,
)

CSV_HEADERS = [
    "Consignee Name",
    "Shipper Name",
    "Country of Origin",
    "HS Code",
    "Arrival Date",
    "Quantity",
    "Gross Weight",
    "Product Description",
]

def _row(**overrides):
    base = dict(
        zip(
            CSV_HEADERS,
            [
                "Acme Import Co., LLC",
                "Shenzhen Bright Factory",
                "China",
                "853650",
                "06/15/2025",
                "1,200 PCS",
                "3,400 KG",
                "PUSH BUTTON SWITCHES",
            ],
        )
    )
    base.update(overrides)
    return base


def _write_csv(path: Path, rows: list[dict], headers: list[str] = CSV_HEADERS) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_detect_column_mapping_exact_and_contains():
    mapping = detect_column_mapping(CSV_HEADERS)
    assert mapping["consignee_name"] == "Consignee Name"
    assert mapping["supplier_name"] == "Shipper Name"
    assert mapping["country"] == "Country of Origin"
    assert mapping["arrival_date"] == "Arrival Date"
    assert mapping["weight"] == "Gross Weight"


def test_normalize_date_formats():
    assert normalize_date("2025-06-15") == "2025-06-15"
    assert normalize_date("2025-06-15 00:00:00") == "2025-06-15"
    assert normalize_date("06/15/2025") == "2025-06-15"
    assert normalize_date("Jun 15, 2025") == "2025-06-15"
    assert normalize_date("") == ""
    assert normalize_date("not a date") == ""


def test_normalize_country_and_hs():
    assert normalize_country_code("China") == "cn"
    assert normalize_country_code("UNITED STATES") == "us"
    assert normalize_country_code("Vietnam") == "vn"
    assert normalize_country_code("us") == "us"
    assert normalize_country_code("Atlantis") == ""
    assert normalize_hs_code("8536.50") == "853650"
    assert normalize_hs_code("8536500000") == "8536500000"
    assert normalize_hs_code("123") == ""


def test_record_from_row_requires_consignee():
    row = _row()
    row["Consignee Name"] = ""
    assert record_from_row(row) is None


def test_record_from_row_maps_and_finalizes_hash():
    record = record_from_row(_row(), source_ref="orders.csv")
    assert record.company_name == "Acme Import Co., LLC"
    assert record.supplier_name == "Shenzhen Bright Factory"
    assert record.country_code == "cn"
    assert record.hs_code == "853650"
    assert record.arrival_date == "2025-06-15"
    assert record.source_ref == "orders.csv"
    assert record.record_hash


def test_identical_rows_share_hash():
    first = record_from_row(_row(), source_ref="orders.csv")
    second = record_from_row(_row(), source_ref="orders.csv")
    assert first.record_hash == second.record_hash
    changed = record_from_row(_row(**{"Arrival Date": "07/01/2025"}), source_ref="orders.csv")
    assert changed.record_hash != first.record_hash


def test_explicit_mapping_overrides_detection(tmp_path):
    path = tmp_path / "odd.csv"
    headers = ["Buyer", "Seller", "Origin", "Tariff", "ETA"]
    _write_csv(path, [{"Buyer": "Acme", "Seller": "Bright", "Origin": "CN", "Tariff": "853650", "ETA": "2025-06-15"}], headers=headers)
    records = parse_file(path, column_mapping={
        "consignee_name": "Buyer",
        "supplier_name": "Seller",
        "country": "Origin",
        "hs_code": "Tariff",
        "arrival_date": "ETA",
    })
    assert len(records) == 1
    assert records[0].company_name == "Acme"
    assert records[0].arrival_date == "2025-06-15"


def test_parse_file_csv(tmp_path):
    path = _write_csv(tmp_path / "export.csv", [_row(), _row(), _row(**{"Consignee Name": ""})])
    records = parse_file(path)
    assert len(records) == 2
    assert all(isinstance(item, TradeRecord) for item in records)


def test_parse_file_xlsx(tmp_path):
    from openpyxl import Workbook

    path = tmp_path / "export.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(CSV_HEADERS)
    ws.append(list(_row().values()))
    ws.append([None] * len(CSV_HEADERS))  # blank row must be skipped
    ws.append(list(_row(**{"Consignee Name": "Beta Trading Inc"}).values()))
    wb.save(path)

    records = parse_file(path)
    names = [item.company_name for item in records]
    assert names == ["Acme Import Co., LLC", "Beta Trading Inc"]


def test_parse_file_reports_unparseable_dates(tmp_path):
    path = _write_csv(tmp_path / "bad-dates.csv", [_row(**{"Arrival Date": "unknown"})])
    records = parse_file(path)
    assert len(records) == 1
    assert records[0].arrival_date == ""


def test_trade_record_finalize_is_stable():
    rec = TradeRecord(company_name="Acme", consignee_name="Acme", supplier_name="B", country="China",
                      country_code="cn", hs_code="853650", product_description="", arrival_date="2025-06-15",
                      quantity="", weight="", source_ref="f.csv")
    assert rec.finalize().record_hash == rec.finalize().record_hash


@pytest.mark.parametrize("header", ["Consignee Name ", "consignee  name", "CONSIGNEE_NAME"])
def test_header_normalization_variants(header):
    mapping = detect_column_mapping([header])
    assert "consignee_name" in mapping
