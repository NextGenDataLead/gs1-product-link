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
"""

from __future__ import annotations

import logging
import zipfile
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from lib.errors import ProcessListError

if TYPE_CHECKING:
    from lib.config import ProcessListConfig

_log = logging.getLogger(__name__)

_GTIN14_WIDTH = 14


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
    try:
        with zipfile.ZipFile(config.path) as zf:
            shared = _read_shared_strings(zf)
            for sheet_path in _worksheet_paths(zf):
                rows = _read_sheet(zf, sheet_path, shared)
                header = _find_header(rows, config.gtin_column)
                if header is None:
                    continue
                header_index, gtin_col = header
                gtins = frozenset(
                    gtin
                    for cells in rows[header_index + 1 :]
                    if (gtin := _coerce_gtin(cells.get(gtin_col))) is not None
                )
                if not gtins:
                    raise ProcessListError(
                        f"process list at {config.path} has a {config.gtin_column!r} column "
                        f"but no GTINs under it — nothing would be processed. Check that the "
                        f"rows sit below the header and that the barcodes are not blank."
                    )
                _log.info("Loaded %d GTIN(s) to process from %s", len(gtins), config.path)
                return gtins
    except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise ProcessListError(f"cannot read process list at {config.path}: {exc}") from exc

    raise ProcessListError(
        f"process list at {config.path} has no sheet with a {config.gtin_column!r} column"
    )


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


def _find_header(rows: list[dict[str, str]], gtin_column: str) -> tuple[int, str] | None:
    """Find the first row containing ``gtin_column``; return its index and column letter.

    Returns ``None`` when no row in the sheet holds the column, so the caller tries the
    next sheet.
    """
    for index, cells in enumerate(rows):
        for col, text in cells.items():
            if text.strip() == gtin_column:
                return index, col
    return None
