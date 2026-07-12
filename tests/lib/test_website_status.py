"""Tests for lib/website_status.py — the create-only eligibility gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import openpyxl
import pytest

from lib.config import WebsiteStatusConfig
from lib.errors import WebsiteStatusError
from lib.website_status import WebsiteStatus, load_website_status

_HEADER = [
    "Artikelnr.",
    "Omschrijving NL",
    "Barcode",
    "Momenteel op Website",
    "Al in Gs1",
    "Link naar site",
]


def _write_xlsx(tmp_path: Path, rows: list[list[Any]], header: list[str] = _HEADER) -> str:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    path = tmp_path / "website_status.xlsx"
    workbook.save(path)
    return str(path)


def _config(path: str) -> WebsiteStatusConfig:
    return WebsiteStatusConfig(path=path)


def test_blank_and_filled_cells_parse_as_booleans(tmp_path: Path) -> None:
    # Arrange: on-website blank, in-GS1 filled -> eligible.
    path = _write_xlsx(tmp_path, [["A1", "Widget", "8712345678905", None, "x", "https://x/1"]])

    # Act
    statuses = load_website_status(_config(path))

    # Assert
    status = statuses["8712345678905"]
    assert status == WebsiteStatus(
        gtin="8712345678905", on_website=False, in_gs1=True, site_link="https://x/1"
    )
    assert status.eligible is True


@pytest.mark.parametrize(
    ("on_website", "in_gs1", "eligible"),
    [
        (None, "yes", True),  # not on site, in GS1 -> eligible
        ("yes", "yes", False),  # already on site -> skip
        (None, None, False),  # not in GS1 -> skip
        ("yes", None, False),  # on site and not in GS1 -> skip
    ],
)
def test_eligibility_requires_in_gs1_and_not_on_website(
    tmp_path: Path, on_website: Any, in_gs1: Any, eligible: bool
) -> None:
    path = _write_xlsx(tmp_path, [["A1", "Widget", "8712345678905", on_website, in_gs1, None]])

    statuses = load_website_status(_config(path))

    assert statuses["8712345678905"].eligible is eligible


def test_whitespace_only_cell_counts_as_blank(tmp_path: Path) -> None:
    path = _write_xlsx(tmp_path, [["A1", "Widget", "8712345678905", "   ", "x", None]])

    status = load_website_status(_config(path))["8712345678905"]

    assert status.on_website is False
    assert status.eligible is True


def test_numeric_barcode_coerced_to_digit_string(tmp_path: Path) -> None:
    # openpyxl stores an unquoted barcode as an int; it must key as a digit string.
    path = _write_xlsx(tmp_path, [["A1", "Widget", 8712345678905, None, "x", None]])

    statuses = load_website_status(_config(path))

    assert "8712345678905" in statuses


def test_rows_with_blank_barcode_are_skipped(tmp_path: Path) -> None:
    path = _write_xlsx(
        tmp_path,
        [
            ["A1", "Widget", "8712345678905", None, "x", None],
            ["A2", "No barcode", None, None, "x", None],
        ],
    )

    statuses = load_website_status(_config(path))

    assert list(statuses) == ["8712345678905"]


def test_missing_required_column_raises(tmp_path: Path) -> None:
    header = ["Artikelnr.", "Omschrijving NL", "Barcode", "Al in Gs1"]  # no "Momenteel op Website"
    path = _write_xlsx(tmp_path, [["A1", "Widget", "8712345678905", "x"]], header=header)

    with pytest.raises(WebsiteStatusError, match="Momenteel op Website"):
        load_website_status(_config(path))


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(WebsiteStatusError, match="cannot read"):
        load_website_status(_config(str(tmp_path / "does_not_exist.xlsx")))


def test_optional_site_link_column_absent(tmp_path: Path) -> None:
    header = ["Artikelnr.", "Omschrijving NL", "Barcode", "Momenteel op Website", "Al in Gs1"]
    path = _write_xlsx(tmp_path, [["A1", "Widget", "8712345678905", None, "x"]], header=header)

    status = load_website_status(_config(path))["8712345678905"]

    assert status.site_link is None
    assert status.eligible is True
