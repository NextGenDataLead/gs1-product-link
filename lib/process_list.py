"""Process list: the operator's explicit list of which GTINs a run may touch.

Loads the operator-maintained control file (``input/{client_id}/process-list.xlsx``) and
returns the set of GTINs in it. **Every GTIN in the file is processed.** There is no
eligibility logic here and no interpretation of cell *values* — the file is a list, and
being on it is the whole meaning.

That is deliberate, and it replaced a version that read "already on website" and "already
in GS1" columns with presence-semantics (any non-blank cell meant ``True``). The old
behaviour was correct only for files that mark rows with ``X``: a client whose file said
``no`` got the opposite of what the word meant, silently, because ``"no"`` is non-blank.
It failed in both directions — a wrong "on website" emptied the plan and the run reported
success having published nothing, while a wrong "in GS1" marked a product eligible and
pointed the pipeline at a GTIN with no resolver record. Neither raised anything.

So the judgement moved to the person who has it. **The operator prepares the file by
deleting every row that should not be processed**, by whatever rule their business uses.
The tool no longer guesses what a column means, because it no longer reads one.

The file is read with a small, namespace-agnostic XML reader rather than ``openpyxl``,
because the real operator export is irregular in ways ``openpyxl`` does not handle: it is
saved as **Strict Open XML** (``openpyxl`` reads zero sheets from those), the data table
starts several rows down (a report title or pivot sits above it), and the data lives on a
named sheet alongside a pivot summary. So the reader scans every worksheet for the first
row containing the configured GTIN column (the header), reads the rows below it, and
ignores everything else. GTINs are normalised to 14 digits so a 13-digit barcode joins to
a 14-digit :attr:`lib.records.ProductRecord.gtin14`.

There is one reader, not two. :func:`read_process_list` returns the whole table as a
:class:`ProcessListSheet` — the shape an editing surface needs — and
:func:`load_process_list` is that call plus ``listed_gtins()``. The shell used to carry its
own openpyxl reader, which meant a Strict-OOXML file with a title row loaded in a run and
failed on screen; the two could disagree about the same file, and did.
"""

from __future__ import annotations

import logging
import zipfile
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from lib.errors import ProcessListError

if TYPE_CHECKING:
    from lib.config import ProcessListConfig

_log = logging.getLogger(__name__)

_GTIN14_WIDTH = 14


@dataclass(frozen=True)
class ProcessListSheet:
    """The control file as a grid: its header row, its data rows, and which column holds the GTIN.

    ``rows`` are cell texts, because this is a display and edit surface — the *meaning* of a
    GTIN (13 vs 14 digits, leading zeros) is settled by :meth:`gtin14_at` via the one
    normalisation in this module, and re-deciding it anywhere else would create a second
    opinion about the same value.

    Every column the file carries is present, in spreadsheet order, including columns with a
    blank header cell: a save rewrites the grid, so a column dropped here is a column dropped
    from the operator's file.
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

    def gtin14_at(self, index: int) -> str | None:
        """The GTIN-14 of the row at this position, or ``None`` when its barcode cell is blank."""
        row = self.rows[index]
        if not 0 <= self.gtin_index < len(row):
            return None
        return _coerce_gtin(row[self.gtin_index])

    def listed_gtins(self) -> frozenset[str]:
        """Every GTIN in the sheet, normalised to 14 digits. Duplicates collapse."""
        return frozenset(
            gtin for n in range(len(self.rows)) if (gtin := self.gtin14_at(n)) is not None
        )


def rows_in_export(
    sheet: ProcessListSheet, exported: Collection[str]
) -> tuple[list[int], list[int]]:
    """Split the sheet's row positions by whether the export carries that GTIN.

    Returns ``(matched, unmatched)``, both in sheet order. A row with a blank barcode is
    unmatched — it is on the list and the export has nothing for it, which is the same fact.

    ``exported`` must be GTIN-14s: ``{product.gtin14 for product in products}``, which is exactly
    the pair :func:`lib.preflight.in_scope` joins on. That is the whole reason this is a function
    rather than two lines on a screen. :func:`lib.preflight.check_scope` deliberately emits
    ``ProductRecord.gtin`` and *not* ``gtin14``, because a normalised variant there would silently
    fail to match for any client whose feed carries 13-digit codes; a third normalisation invented
    at a call site would report every good product as missing, and look like bad data rather than
    like a bug.

    Nothing else in the codebase computes this set. A barcode that is on the list and absent from
    the export is invisible today: it produces no error, no plan row and no count, and the
    operator's only evidence is a number that is one smaller than they expected.
    """
    matched: list[int] = []
    unmatched: list[int] = []
    for index in range(len(sheet.rows)):
        gtin = sheet.gtin14_at(index)
        (matched if gtin is not None and gtin in exported else unmatched).append(index)
    return matched, unmatched


def _is_filled(value: object) -> bool:
    """Return whether a spreadsheet cell holds a non-blank value."""
    return value is not None and str(value).strip() != ""


def _coerce_gtin(value: object) -> str | None:
    """Coerce a barcode cell to a GTIN-14 digit string, or ``None`` when blank.

    A 13-digit barcode is zero-padded to 14 digits so it joins to a
    :attr:`lib.records.ProductRecord.gtin14`; whole-number floats (``…905.0``) and ints
    are rendered without a decimal point first.
    """
    if not _is_filled(value):
        return None
    if isinstance(value, bool):  # bool is an int subclass; never a GTIN
        return None
    if isinstance(value, float) and value.is_integer():
        digits = str(int(value))
    elif isinstance(value, int):
        digits = str(value)
    else:
        digits = str(value).strip()
    return digits.zfill(_GTIN14_WIDTH)


def read_process_list(config: ProcessListConfig) -> ProcessListSheet:
    """Read the control file as a grid: header, rows, and the position of the GTIN column.

    Args:
        config: The client's ``process_list`` configuration (path + GTIN column name).

    Returns:
        The whole table below the first row carrying the configured GTIN column, with every
        column the file holds.

    Raises:
        ProcessListError: If the file cannot be opened, or if no worksheet contains the
            configured GTIN column. Deliberately **not** raised for a sheet that yields no
            GTINs at all: an empty grid is displayable and fixable, and refusing it belongs
            to the two callers that would act on it — :func:`load_process_list`, which would
            plan nothing, and ``ui.process_list_edit.save_sheet``, which would write it.
    """
    path = Path(config.path)
    try:
        with zipfile.ZipFile(path) as zf:
            shared = _read_shared_strings(zf)
            for sheet_path in _worksheet_paths(zf):
                rows = _read_sheet(zf, sheet_path, shared)
                header = _find_header(rows, config.gtin_column)
                if header is None:
                    continue
                header_index, header_cells = header
                return _grid(path, rows, header_index, header_cells, config.gtin_column)
    except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise ProcessListError(f"cannot read process list at {config.path}: {exc}") from exc

    raise ProcessListError(
        f"process list at {config.path} has no sheet with a {config.gtin_column!r} column"
    )


def load_process_list(config: ProcessListConfig) -> frozenset[str]:
    """Load the process list, returning every listed GTIN normalised to 14 digits.

    Args:
        config: The client's ``process_list`` configuration (path + GTIN column name).

    Returns:
        The GTIN-14s to process. Duplicate rows collapse; a row with a blank barcode is
        skipped.

    Raises:
        ProcessListError: If the file cannot be opened, if no worksheet contains the
            configured GTIN column, or if the file yields **no** GTINs at all. That last
            case is a structural check rather than an interpretation of values: a file
            that parses to an empty list would otherwise produce an empty plan and a run
            that reports success having published nothing.
    """
    gtins = read_process_list(config).listed_gtins()
    if not gtins:
        raise ProcessListError(
            f"process list at {config.path} has a {config.gtin_column!r} column "
            f"but no GTINs under it — nothing would be processed. Check that the "
            f"rows sit below the header and that the barcodes are not blank."
        )
    _log.info("Loaded %d GTIN(s) to process from %s", len(gtins), config.path)
    return gtins


def _local(tag: str) -> str:
    """Return an XML tag's local name, dropping any ``{namespace}`` prefix."""
    return tag.rsplit("}", 1)[-1]


def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    """Return the workbook's shared-string table (empty when absent)."""
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [
        "".join(t.text or "" for t in si.iter() if _local(t.tag) == "t")
        for si in root
        if _local(si.tag) == "si"
    ]


def _worksheet_paths(zf: zipfile.ZipFile) -> list[str]:
    """Return the archive paths of each worksheet, in workbook (sheet) order."""
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rid_to_target = {rel.get("Id"): rel.get("Target") or "" for rel in rels}
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    paths: list[str] = []
    for el in workbook.iter():
        if _local(el.tag) != "sheet":
            continue
        rid = next((v for k, v in el.attrib.items() if _local(k) == "id"), None)
        target = rid_to_target.get(rid)
        if not target:
            continue
        normalised = target.lstrip("/")
        paths.append(normalised if normalised.startswith("xl/") else f"xl/{normalised}")
    return paths


def _col_letters(ref: str) -> str:
    """Return the column letters of a cell reference (``"C4"`` → ``"C"``)."""
    return "".join(ch for ch in ref if ch.isalpha())


def _column_number(letters: str) -> int:
    """Return a column letter's 1-based position (``"A"`` → 1, ``"Z"`` → 26, ``"AA"`` → 27).

    Sorting the letters as *strings* is the bug this exists to avoid: ``["A", "Z", "AA"]``
    sorts to ``A, AA, Z``, which silently reorders the grid past column Z and leaves
    ``gtin_index`` pointing at some other column. A 28-column operator file is ordinary.
    """
    number = 0
    for char in letters:
        number = number * 26 + (ord(char.upper()) - ord("A") + 1)
    return number


def _read_sheet(zf: zipfile.ZipFile, path: str, shared: list[str]) -> list[dict[str, str]]:
    """Read a worksheet into a list of ``{column-letter: text}`` rows, in order."""
    root = ET.fromstring(zf.read(path))
    rows: list[dict[str, str]] = []
    for row in root.iter():
        if _local(row.tag) != "row":
            continue
        cells: dict[str, str] = {}
        for cell in row:
            if _local(cell.tag) != "c":
                continue
            column = _col_letters(cell.get("r") or "")
            text = _cell_text(cell, shared)
            if column and text is not None:
                cells[column] = text
        rows.append(cells)
    return rows


def _cell_text(cell: ET.Element, shared: list[str]) -> str | None:
    """Return a cell's text, resolving shared and inline strings."""
    cell_type = cell.get("t")
    if cell_type == "inlineStr":
        return "".join(t.text or "" for t in cell.iter() if _local(t.tag) == "t")
    value = next((c for c in cell if _local(c.tag) == "v"), None)
    if value is None or value.text is None:
        return None
    if cell_type == "s":
        index = int(value.text)
        return shared[index] if 0 <= index < len(shared) else None
    return value.text


def _find_header(rows: list[dict[str, str]], gtin_column: str) -> tuple[int, dict[str, str]] | None:
    """Find the first row containing ``gtin_column``; return its index and every cell in it.

    Returns ``None`` when no row in the sheet holds the column, so the caller tries the
    next sheet.
    """
    for index, cells in enumerate(rows):
        if any(text.strip() == gtin_column for text in cells.values()):
            return index, cells
    return None


def _grid(
    path: Path,
    rows: list[dict[str, str]],
    header_index: int,
    header_cells: dict[str, str],
    gtin_column: str,
) -> ProcessListSheet:
    """Assemble the rows below the header into a rectangular grid, in spreadsheet order.

    Columns are the **union** of the header row's letters and every data row's letters. The
    union is what keeps a column whose header cell is blank: it is still the operator's
    column, a save rewrites what this returns, and dropping it here would delete it from
    their file under a message saying the other columns were kept.
    """
    data = rows[header_index + 1 :]
    letters = sorted(
        {*header_cells, *(letter for cells in data for letter in cells)}, key=_column_number
    )
    header = [header_cells.get(letter, "").strip() for letter in letters]
    grid = [[cells.get(letter, "").strip() for letter in letters] for cells in data]
    filled = [row for row in grid if any(row)]
    return ProcessListSheet(path, header, filled, header.index(gtin_column))
