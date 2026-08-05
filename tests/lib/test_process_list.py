"""Tests for lib/process_list.py — the operator's list of GTINs to process.

Fixtures are written with openpyxl (transitional OOXML). The reader is namespace-agnostic
and also handles Strict OOXML, header rows below row 1, and the data sheet sitting beside
other sheets — those irregularities of the real operator export are verified against the
live file during end-to-end checks; here we cover the header auto-detection and sheet-scan
logic with synthetic workbooks.

The behaviour under test is deliberately narrow: **every GTIN in the file is processed.**
No cell values are interpreted, so the tests below assert that extra columns are ignored
regardless of what they contain — including the words the previous status-column reader
silently got backwards.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import openpyxl
import pytest

from lib.config import ProcessListConfig
from lib.errors import ProcessListError
from lib.process_list import load_process_list

_HEADER = ["Artikelnr.", "Omschrijving NL", "Barcode"]

GTIN13 = "8713195004778"  # as written in the file (no leading zero)
GTIN14 = "08713195004778"  # canonical key after GTIN-14 normalisation


def _write_xlsx(
    tmp_path: Path,
    rows: list[list[Any]],
    header: list[str] = _HEADER,
    *,
    header_start_row: int = 1,
    extra_sheet_first: bool = False,
) -> str:
    """Write a process-list workbook; optionally offset the header and prepend a sheet."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    if extra_sheet_first:
        sheet.append(["a pivot / summary sheet with no data columns"])
        sheet = workbook.create_sheet("Blad1")
    for offset, name in enumerate(header, start=1):
        sheet.cell(row=header_start_row, column=offset, value=name)
    for r, row in enumerate(rows, start=header_start_row + 1):
        for offset, value in enumerate(row, start=1):
            sheet.cell(row=r, column=offset, value=value)
    path = tmp_path / "process-list.xlsx"
    workbook.save(path)
    return str(path)


def _config(path: str, gtin_column: str = "Barcode") -> ProcessListConfig:
    return ProcessListConfig(path=path, gtin_column=gtin_column)


def test_every_listed_gtin_is_returned(tmp_path: Path) -> None:
    # Arrange
    path = _write_xlsx(
        tmp_path,
        [["A1", "Widget", GTIN13], ["A2", "Gadget", "8713195004779"]],
    )

    # Act
    listed = load_process_list(_config(path))

    # Assert
    assert listed == frozenset({GTIN14, "08713195004779"})


@pytest.mark.parametrize("marker", ["x", "X", "no", "nee", "FALSE", "0", "", None])
def test_extra_columns_are_ignored_whatever_they_contain(tmp_path: Path, marker: Any) -> None:
    """Membership is the whole rule — a status-looking column must not change the outcome.

    Regression: the previous reader treated any non-blank cell as True, so a file saying
    ``no`` meant the opposite of the word, silently and in both directions.
    """
    # Arrange
    path = _write_xlsx(
        tmp_path,
        [["A1", "Widget", GTIN13, marker]],
        header=[*_HEADER, "Op website"],
    )

    # Act
    listed = load_process_list(_config(path))

    # Assert
    assert listed == frozenset({GTIN14})


def test_thirteen_digit_barcode_normalised_to_gtin14(tmp_path: Path) -> None:
    # Arrange
    path = _write_xlsx(tmp_path, [["A1", "Widget", GTIN13]])

    # Act / Assert
    assert load_process_list(_config(path)) == frozenset({GTIN14})


def test_numeric_barcode_coerced_and_normalised(tmp_path: Path) -> None:
    """A barcode cell typed as a number must not arrive as ``8713195004778.0``."""
    # Arrange
    path = _write_xlsx(tmp_path, [["A1", "Widget", int(GTIN13)]])

    # Act / Assert
    assert load_process_list(_config(path)) == frozenset({GTIN14})


def test_duplicate_rows_collapse(tmp_path: Path) -> None:
    # Arrange: the same product twice, once 13-digit and once 14-digit.
    path = _write_xlsx(tmp_path, [["A1", "Widget", GTIN13], ["A1", "Widget", GTIN14]])

    # Act / Assert
    assert load_process_list(_config(path)) == frozenset({GTIN14})


def test_header_not_on_first_row_is_auto_detected(tmp_path: Path) -> None:
    # Arrange: a report title sits above the table, as in the real export.
    path = _write_xlsx(tmp_path, [["A1", "Widget", GTIN13]], header_start_row=4)

    # Act / Assert
    assert load_process_list(_config(path)) == frozenset({GTIN14})


def test_data_sheet_found_beside_other_sheets(tmp_path: Path) -> None:
    # Arrange: a pivot/summary sheet precedes the data sheet.
    path = _write_xlsx(tmp_path, [["A1", "Widget", GTIN13]], extra_sheet_first=True)

    # Act / Assert
    assert load_process_list(_config(path)) == frozenset({GTIN14})


def test_rows_with_blank_barcode_are_skipped(tmp_path: Path) -> None:
    # Arrange: trailing total/blank rows carry no barcode.
    path = _write_xlsx(
        tmp_path,
        [["A1", "Widget", GTIN13], ["", "Totaal", None], ["", "", "   "]],
    )

    # Act / Assert
    assert load_process_list(_config(path)) == frozenset({GTIN14})


def test_custom_gtin_column_name(tmp_path: Path) -> None:
    """A client may label the column whatever they like — only the name is configured."""
    # Arrange
    path = _write_xlsx(tmp_path, [["A1", "Widget", GTIN13]], header=["Artikelnr.", "Naam", "EAN"])

    # Act / Assert
    assert load_process_list(_config(path, gtin_column="EAN")) == frozenset({GTIN14})


def test_missing_gtin_column_raises(tmp_path: Path) -> None:
    # Arrange
    path = _write_xlsx(tmp_path, [["A1", "Widget"]], header=["Artikelnr.", "Omschrijving NL"])

    # Act / Assert
    with pytest.raises(ProcessListError, match="no sheet with a 'Barcode' column"):
        load_process_list(_config(path))


def test_column_present_but_no_gtins_raises(tmp_path: Path) -> None:
    """An empty list is an error, not an empty run — it would publish nothing silently."""
    # Arrange
    path = _write_xlsx(tmp_path, [["A1", "Widget", None]])

    # Act / Assert
    with pytest.raises(ProcessListError, match="no GTINs under it"):
        load_process_list(_config(path))


def test_missing_file_raises(tmp_path: Path) -> None:
    # Act / Assert
    with pytest.raises(ProcessListError, match="cannot read process list"):
        load_process_list(_config(str(tmp_path / "does_not_exist.xlsx")))
