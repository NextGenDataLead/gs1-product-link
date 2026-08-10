"""Read and rewrite the process list, which is the operator's actual recurring job.

The control file is a list of GTINs and nothing more: being on it is the whole meaning, the tool
reads no cell values, and the operator prepares a batch by **deleting the rows that should not
run**. That is a spreadsheet task today, and it is the one step of the loop where a mis-click is
expensive in the ordinary way — the wrong rows publish, or the right rows do not.

So this module keeps three properties:

* **Every other column is preserved verbatim.** Only the GTIN column is configured; the rest are
  the operator's working notes, and a tool that dropped them would be taking away the reason they
  keep the file.
* **The previous version is kept.** A save writes ``{name}.bak.xlsx`` first. There is no undo in
  a web form, and losing a pruned list means redoing the pruning.
* **An empty result is refused.** ``load_process_list`` already treats zero GTINs as an error
  rather than an empty run, for the reason this project keeps designing against: an empty plan
  and a successful-looking no-op are indistinguishable. Saving an empty file here would just move
  that failure one step earlier.

Pure I/O over openpyxl, no NiceGUI — so it is testable without a browser.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import openpyxl

from lib.config import ProcessListConfig
from lib.errors import ProcessListError


@dataclass(frozen=True)
class ProcessListSheet:
    """The control file as a grid: its header row, its data rows, and which column holds the GTIN.

    ``rows`` are raw cell values coerced to strings, because this is a display and edit surface —
    the *meaning* of a GTIN (13 vs 14 digits, leading zeros) is settled by
    :func:`lib.process_list.load_process_list`, and re-deciding it here would create a second
    opinion about the same value.
    """

    path: Path
    header: list[str]
    rows: list[list[str]]
    gtin_index: int

    def without(self, indices: set[int]) -> ProcessListSheet:
        """A copy with the given row positions removed. Immutable, like everything else here.

        Positions are **into this sheet**, so the result is renumbered. Applying it twice with
        positions taken from the original sheet removes the wrong rows the second time — see
        :meth:`keeping`, which is what an editing surface wants.
        """
        kept = [row for n, row in enumerate(self.rows) if n not in indices]
        return ProcessListSheet(self.path, self.header, kept, self.gtin_index)

    def keeping(self, indices: set[int]) -> ProcessListSheet:
        """A copy holding only these row positions, in their original order.

        The counterpart to :meth:`without`, and the one a grid should use. A grid identifies a row
        by a key fixed when it was built; this sheet renumbers on every edit. Feeding those fixed
        keys back into a renumbered sheet is silently wrong from the second edit onward, and the
        symptom is the worst kind: the screen shows one set of rows and the file receives another,
        with a success message either way. Deriving the sheet from the surviving keys against the
        *original* cannot drift, because nothing accumulates.
        """
        kept = [row for n, row in enumerate(self.rows) if n in indices]
        return ProcessListSheet(self.path, self.header, kept, self.gtin_index)


def read_sheet(config: ProcessListConfig) -> ProcessListSheet:
    """Load the control file for display.

    Raises:
        ProcessListError: If the file cannot be opened, or no worksheet carries the configured
            GTIN column — the same two failures ``load_process_list`` reports, phrased the same
            way, so the shell and the CLI do not disagree about what is wrong.
    """
    path = Path(config.path)
    try:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except OSError as exc:
        raise ProcessListError(f"cannot open the process list at {path}: {exc}") from exc

    for sheet in workbook.worksheets:
        values = list(sheet.iter_rows(values_only=True))
        if not values:
            continue
        header = [_text(cell) for cell in values[0]]
        if config.gtin_column not in header:
            continue
        index = header.index(config.gtin_column)
        rows = [
            [_text(cell) for cell in row] for row in values[1:] if any(c is not None for c in row)
        ]
        workbook.close()
        return ProcessListSheet(path, header, rows, index)

    workbook.close()
    raise ProcessListError(
        f"no worksheet in {path} has a {config.gtin_column!r} column — "
        f"found: {', '.join(s.title for s in workbook.worksheets)}"
    )


def save_sheet(sheet: ProcessListSheet) -> Path:
    """Write the sheet back, keeping the previous version beside it. Returns the backup path.

    Raises:
        ProcessListError: If no row carries a GTIN. Saving an empty control file would produce an
            empty plan and a run that reports success having published nothing — the exact
            failure the zero-GTIN check in ``load_process_list`` exists to prevent, one step
            earlier and with the operator's own pruning already lost.
    """
    if not any(_gtin_of(row, sheet.gtin_index) for row in sheet.rows):
        raise ProcessListError(
            "refusing to save a process list with no GTINs: the next run would plan nothing "
            "and report success"
        )

    backup = sheet.path.with_suffix(f".bak{sheet.path.suffix}")
    if sheet.path.exists():
        backup.write_bytes(sheet.path.read_bytes())

    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append(sheet.header)
    for row in sheet.rows:
        worksheet.append(row)
    workbook.save(sheet.path)
    workbook.close()
    return backup


def _text(cell: object) -> str:
    """Coerce a cell to a display string, keeping an integer barcode's digits (not ``8.7e+12``)."""
    if cell is None:
        return ""
    if isinstance(cell, float) and cell.is_integer():
        return str(int(cell))
    return str(cell).strip()


def _gtin_of(row: list[str], index: int) -> str:
    return row[index].strip() if index < len(row) else ""
