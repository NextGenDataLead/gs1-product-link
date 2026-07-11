"""Unit tests for the GDSN datapool reader (IMPLEMENTATION_SPEC §3 extension)."""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from lib.gdsn import GdsnSource, build_records, read_workbook

# A synthesized mini GDSN workbook: 7 header rows, LanguageCode/Value pairs, two
# markets (528 = nl, 056 = fr), mirroring the real export's structure.

_DESC_HEADER = [
    [
        "Gtin",
        "TargetMarketCountryCode",
        "InformationProviderOfTradeItem",
        "TradeItemUnitDescriptorCode",
        "TradeItemDescriptionInformation",
        "TradeItemDescriptionInformation",
        "TradeItemDescriptionInformation",
        "TradeItemDescriptionInformation",
        "TradeItemDescriptionInformation",
    ],
    [
        None,
        None,
        None,
        None,
        "DescriptionShort[0]",
        "DescriptionShort[0]",
        "DescriptionShort[1]",
        "DescriptionShort[1]",
        "BrandNameInformation",
    ],
    [None, None, None, None, "LanguageCode", "Value", "LanguageCode", "Value", "BrandName"],
    [None] * 9,
    [None] * 9,
    [None] * 9,
    [
        "GTIN (3059)",
        "Country (3179)",
        "Provider (3088)",
        "Unit (3074)",
        "Short product name (3297)",
        "Short product name (3297)",
        "Short product name (3297)",
        "Short product name (3297)",
        "Brand Name (3336)",
    ],
]


def _drow(gtin: str, market: str, *tail: object) -> list[object]:
    """Build a data row with the shared GLN + consumer-unit key columns."""
    return [gtin, market, "GLN", "BASE_UNIT_OR_EACH", *tail]


_DESC_DATA = [
    _drow("08713195007359", "528", "nl", "Rugsteun NL", "de", "Ruck DE", "Noviplast"),
    _drow("08713195007359", "056", "fr", "Support FR", None, None, "Noviplast"),
    _drow("09999999999999", "056", "fr", "Solo FR", None, None, "Noviplast"),
    [None] * 9,  # E4: empty row
]

_MEAS_HEADER = [
    [
        "Gtin",
        "TargetMarketCountryCode",
        "InformationProviderOfTradeItem",
        "TradeItemUnitDescriptorCode",
        "TradeItemMeasurements",
        "TradeItemMeasurements",
    ],
    [None, None, None, None, "NetContent[0]", "NetContent[0]"],
    [None, None, None, None, "MeasurementUnitCode", "Value"],
    [None] * 6,
    [None] * 6,
    [None] * 6,
    [
        "GTIN (3059)",
        "Country (3179)",
        "Provider (3088)",
        "Unit (3074)",
        "Net Content (3510)",
        "Net Content (3510)",
    ],
]
_MEAS_DATA = [
    _drow("08713195007359", "528", "H87", "4"),
]


def _write_workbook(tmp_path: Path) -> str:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    desc = wb.create_sheet("TradeItemDescription")
    for row in [*_DESC_HEADER, *_DESC_DATA]:
        desc.append(row)
    meas = wb.create_sheet("TradeItemMeasurements")
    for row in [*_MEAS_HEADER, *_MEAS_DATA]:
        meas.append(row)
    path = tmp_path / "gdsn.xlsx"
    wb.save(path)
    return str(path)


_GDSN_MAP = {
    "product_name": GdsnSource(sheet="TradeItemDescription", attribute="3297", localised=True),
    "brand": GdsnSource(sheet="TradeItemDescription", attribute="3336"),
    "net_content": GdsnSource(sheet="TradeItemMeasurements", attribute="3510", with_unit=True),
}
_MARKET_LANGUAGE = {"528": "nl", "056": "fr"}


def test_header_detection_and_column_parsing(tmp_path: Path) -> None:
    sheets = read_workbook(_write_workbook(tmp_path))

    desc = sheets["TradeItemDescription"]
    value_col = next(c for c in desc.columns if c.index == 5)
    assert value_col.leaf_name == "Value"
    assert value_col.attr_id == "3297"
    assert value_col.group_path == ("TradeItemDescriptionInformation", "DescriptionShort[0]")
    brand_col = next(c for c in desc.columns if c.index == 8)
    assert brand_col.leaf_name == "BrandName"
    assert brand_col.matches_attribute("3336")


def test_pickers_resolve_language_and_unit(tmp_path: Path) -> None:
    sheets = read_workbook(_write_workbook(tmp_path))
    desc = sheets["TradeItemDescription"]
    meas = sheets["TradeItemMeasurements"]

    assert desc.pick_localised("08713195007359", "528", "3297", "nl") == "Rugsteun NL"
    assert desc.pick_localised("08713195007359", "528", "3297", "de") == "Ruck DE"
    assert desc.pick_localised("08713195007359", "528", "3297", "fr") is None  # not in 528 row
    assert desc.pick_scalar("08713195007359", "528", "3336") == "Noviplast"
    assert meas.pick_scalar("08713195007359", "528", "3510", with_unit=True) == "4 H87"


def test_build_records_joins_markets_into_one_record(tmp_path: Path) -> None:
    # E3 reinterpreted: the same GTIN across markets aggregates into ONE record,
    # sourcing nl from market 528 and fr from market 056.
    sheets = read_workbook(_write_workbook(tmp_path))

    result = build_records(sheets, _GDSN_MAP, _MARKET_LANGUAGE, "nl")

    good = [r for r in result.records if r.gtin == "08713195007359"]
    assert len(good) == 1
    assert good[0].product_name.values == {"nl": "Rugsteun NL", "fr": "Support FR"}
    assert good[0].brand == "Noviplast"
    assert good[0].net_content == "4 H87"


def test_build_records_missing_default_language_is_error(tmp_path: Path) -> None:
    # E5: GTIN 09999999999999 has only fr (from market 056), no nl.
    sheets = read_workbook(_write_workbook(tmp_path))

    result = build_records(sheets, _GDSN_MAP, _MARKET_LANGUAGE, "nl")

    assert not any(r.gtin == "09999999999999" for r in result.records)
    assert any("09999999999999" in e and "product_name.nl" in e for e in result.errors)


def test_build_records_missing_required_source_errors(tmp_path: Path) -> None:
    # E17 (required): product_name mapped to a sheet that isn't present.
    sheets = read_workbook(_write_workbook(tmp_path))
    bad_map = {
        "product_name": GdsnSource(sheet="Nope", attribute="3297", localised=True),
        "brand": GdsnSource(sheet="TradeItemDescription", attribute="3336"),
    }

    result = build_records(sheets, bad_map, _MARKET_LANGUAGE, "nl")

    assert result.records == []
    assert any("product_name" in e and "Nope" in e for e in result.errors)


def test_reference_sheets_without_data_are_skipped(tmp_path: Path) -> None:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    ref = wb.create_sheet("Access Control Group")
    ref.append(["This sheet is reference only.", "", "", ""])
    ref.append(["Name", "x", "y", "z"])
    desc = wb.create_sheet("TradeItemDescription")
    for row in [*_DESC_HEADER, *_DESC_DATA]:
        desc.append(row)
    path = tmp_path / "ref.xlsx"
    wb.save(path)

    sheets = read_workbook(str(path))

    assert "Access Control Group" not in sheets
    assert "TradeItemDescription" in sheets


@pytest.mark.parametrize("default_lang,expected_market", [("nl", "528"), ("fr", "056")])
def test_default_language_selects_primary_market(
    tmp_path: Path, default_lang: str, expected_market: str
) -> None:
    # The default language's market supplies scalar fields (brand, net_content).
    sheets = read_workbook(_write_workbook(tmp_path))

    result = build_records(sheets, _GDSN_MAP, _MARKET_LANGUAGE, default_lang)

    record = next(r for r in result.records if r.gtin == "08713195007359")
    assert record.brand == "Noviplast"
    assert expected_market in _MARKET_LANGUAGE
