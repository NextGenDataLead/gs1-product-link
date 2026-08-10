"""Tests for ui/process_list_edit.py — pruning the control file.

The operator's recurring job, and the one step where the shell writes to a file they authored.
The three properties worth asserting are the three that make that safe: other columns survive,
the previous version survives, and an empty result is refused.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from lib.config import ProcessListConfig
from lib.errors import ProcessListError
from lib.process_list import load_process_list
from ui.process_list_edit import read_sheet, save_sheet

GTIN_A = "8713195007359"
GTIN_B = "8713195007360"


def _write(tmp_path: Path, rows: list[list[object]], header: list[str] | None = None) -> Path:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(header or ["Artikelnr.", "Barcode", "Omschrijving"])
    for row in rows:
        sheet.append(row)
    path = tmp_path / "process-list.xlsx"
    workbook.save(path)
    return path


def _config(path: Path) -> ProcessListConfig:
    return ProcessListConfig(path=str(path), gtin_column="Barcode")


def test_reading_keeps_every_column(tmp_path: Path) -> None:
    """Only the GTIN column is configured; the rest are the operator's working notes."""
    path = _write(tmp_path, [["1079", GTIN_A, "Drain saver"]])

    sheet = read_sheet(_config(path))

    assert sheet.header == ["Artikelnr.", "Barcode", "Omschrijving"]
    assert sheet.rows == [["1079", GTIN_A, "Drain saver"]]
    assert sheet.gtin_index == 1


def test_an_integer_barcode_keeps_its_digits(tmp_path: Path) -> None:
    """openpyxl hands back a float for a numeric cell; ``8.7e+12`` is not a barcode."""
    path = _write(tmp_path, [["1079", int(GTIN_A), "Drain saver"]])

    sheet = read_sheet(_config(path))

    assert sheet.rows[0][1] == GTIN_A


def test_a_missing_gtin_column_says_so_the_way_the_cli_does(tmp_path: Path) -> None:
    path = _write(tmp_path, [["1079", GTIN_A]], header=["Artikelnr.", "EAN"])

    with pytest.raises(ProcessListError, match="Barcode"):
        read_sheet(_config(path))


def test_saving_keeps_the_other_columns_and_the_previous_version(tmp_path: Path) -> None:
    path = _write(tmp_path, [["1079", GTIN_A, "Drain saver"], ["1080", GTIN_B, "Airfryer basket"]])
    sheet = read_sheet(_config(path))

    backup = save_sheet(sheet.without({1}))

    assert backup.exists()  # there is no undo in a web form
    assert read_sheet(_config(backup)).rows[1][1] == GTIN_B  # the removed row is still recoverable
    kept = read_sheet(_config(path))
    assert kept.rows == [["1079", GTIN_A, "Drain saver"]]


def test_the_pruned_file_is_what_the_pipeline_then_reads(tmp_path: Path) -> None:
    """The point of the whole screen: the CLI must agree with what the operator just saved."""
    path = _write(tmp_path, [["1079", GTIN_A, "keep"], ["1080", GTIN_B, "drop"]])
    sheet = read_sheet(_config(path))

    save_sheet(sheet.without({1}))

    assert load_process_list(_config(path)) == frozenset({f"0{GTIN_A}"})


def test_saving_an_empty_list_is_refused(tmp_path: Path) -> None:
    """An empty control file yields an empty plan and a run that reports success publishing nothing.

    Refused here rather than at ``load_process_list``, because by then the operator's pruning is
    already lost and they have to redo it to find out.
    """
    path = _write(tmp_path, [["1079", GTIN_A, "Drain saver"]])
    sheet = read_sheet(_config(path))

    with pytest.raises(ProcessListError, match="report success"):
        save_sheet(sheet.without({0}))

    assert read_sheet(_config(path)).rows  # the file on disk is untouched


def test_without_does_not_mutate_the_original(tmp_path: Path) -> None:
    path = _write(tmp_path, [["1079", GTIN_A, "a"], ["1080", GTIN_B, "b"]])
    sheet = read_sheet(_config(path))

    pruned = sheet.without({0})

    assert len(sheet.rows) == 2
    assert len(pruned.rows) == 1


# --- Pruning in more than one pass --------------------------------------------
#
# The Data screen's grid keys each row by its position when the grid was built, and that key never
# changes. A sheet renumbers on every edit. Feeding fixed keys into a renumbered sheet is correct
# once and wrong from the second removal onward — and wrong in the way that matters least visibly:
# the grid shows one set of rows, the file receives another, and the save reports success. That is
# a live page and a permanent GS1 record for a product the operator did not choose.


def _rows(n: int) -> list[list[object]]:
    return [[f"art-{i}", f"871319500{i:04d}", f"name-{i}"] for i in range(n)]


def test_keeping_selects_by_original_position_and_holds_the_order(tmp_path: Path) -> None:
    sheet = read_sheet(_config(_write(tmp_path, _rows(5))))

    kept = sheet.keeping({0, 2, 4})

    assert [row[1] for row in kept.rows] == [sheet.rows[i][1] for i in (0, 2, 4)]
    assert len(sheet.rows) == 5, "the original is untouched"


def test_two_removals_leave_the_file_agreeing_with_the_grid(tmp_path: Path) -> None:
    """The regression. Remove one row, then another, exactly as the screen does it."""
    sheet = read_sheet(_config(_write(tmp_path, _rows(5))))
    grid = [{"_row": n, "gtin": row[1]} for n, row in enumerate(sheet.rows)]

    for selection in ({0}, {3}):  # two passes, keys taken from the original grid both times
        grid = [row for row in grid if row["_row"] not in selection]
        pruned = sheet.keeping({int(row["_row"]) for row in grid})

    assert [row[1] for row in pruned.rows] == [row["gtin"] for row in grid], (
        "the file would receive rows other than the ones left on screen"
    )


def test_the_incremental_form_is_the_one_that_drifts(tmp_path: Path) -> None:
    """Why ``keeping`` exists, asserted rather than described — ``without`` renumbers."""
    sheet = read_sheet(_config(_write(tmp_path, _rows(5))))
    grid = [{"_row": n, "gtin": row[1]} for n, row in enumerate(sheet.rows)]

    drifting = sheet
    for selection in ({0}, {3}):
        grid = [row for row in grid if row["_row"] not in selection]
        drifting = drifting.without(selection)

    assert [row[1] for row in drifting.rows] != [row["gtin"] for row in grid]
