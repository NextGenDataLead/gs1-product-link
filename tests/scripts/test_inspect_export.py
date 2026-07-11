"""Tests for scripts/inspect_export.py (IMPLEMENTATION_SPEC §8.5)."""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from scripts import inspect_export


def _write_gdsn_xlsx(tmp_path: Path) -> str:
    header = [
        [
            "Gtin",
            "TargetMarketCountryCode",
            "InformationProviderOfTradeItem",
            "TradeItemUnitDescriptorCode",
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
            "BrandNameInformation",
        ],
        [None, None, None, None, "LanguageCode", "Value", "BrandName"],
        [None] * 7,
        [None] * 7,
        [None] * 7,
        [
            "GTIN (3059)",
            "Country (3179)",
            "Provider (3088)",
            "Unit (3074)",
            "Short product name (3297)",
            "Short product name (3297)",
            "Brand Name (3336)",
        ],
    ]
    data = [["08713195007359", "528", "GLN", "BASE_UNIT_OR_EACH", "nl", "Rugsteun", "Noviplast"]]
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    sheet = workbook.create_sheet("TradeItemDescription")
    for row in [*header, *data]:
        sheet.append(row)
    path = tmp_path / "gdsn.xlsx"
    workbook.save(path)
    return str(path)


def test_reports_sheets_and_suggests_map(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = inspect_export.main([_write_gdsn_xlsx(tmp_path)])

    out = capsys.readouterr().out
    assert code == 0
    assert "### TradeItemDescription" in out
    assert "Short product name" in out
    # A ready-to-paste suggestion recognising the known attributes.
    assert "gdsn_map:" in out
    assert "product_name:" in out
    assert "3297" in out
    assert "brand:" in out


def test_missing_file_returns_error(capsys: pytest.CaptureFixture[str]) -> None:
    code = inspect_export.main(["/no/such/file.xlsx"])

    assert code == 1
    assert "cannot read export" in capsys.readouterr().err


def test_wrong_arg_count_returns_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert inspect_export.main([]) == 2
    assert "usage:" in capsys.readouterr().err
