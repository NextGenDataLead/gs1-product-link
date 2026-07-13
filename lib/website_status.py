"""Website-status control file: the create-only eligibility gate.

Loads the operator-maintained control file (``input/{client_id}/website_status.xlsx``)
that determines which products may have a WordPress page and QR created. A product is
**eligible** only when its GTIN is already registered in GS1 (its resolver record
exists) and it is not yet on the website. This is not part of the datasource export and
not in the original spec — a deliberate, per-client extension for the pilot's
create-only workflow (see :class:`lib.config.WebsiteStatusConfig`).

The file is read with a small, namespace-agnostic XML reader rather than ``openpyxl``,
because the real operator export is irregular in ways ``openpyxl`` does not handle: it is
saved as **Strict Open XML** (``openpyxl`` reads zero sheets from those), the data table
starts several rows down (a report title/pivot sits above it), and the data lives on a
named sheet alongside a pivot summary. So the reader instead scans every worksheet for
the first row that contains all the configured columns (the header), reads the rows
below it, and ignores everything else. Both "on website" and "in GS1" columns are
boolean-by-presence (any non-blank cell is ``True``). Rows are keyed by GTIN-14 (the
barcode zero-padded to 14 digits) so a 13-digit control-file barcode joins to a
14-digit :attr:`lib.records.ProductRecord.gtin14`.
"""

from __future__ import annotations

import logging
import zipfile
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from pydantic import BaseModel, ConfigDict

from lib.errors import WebsiteStatusError

if TYPE_CHECKING:
    from lib.config import WebsiteStatusConfig

_log = logging.getLogger(__name__)

_GTIN14_WIDTH = 14


class WebsiteStatus(BaseModel):
    """One product's website/GS1 status from the control file."""

    model_config = ConfigDict(frozen=True)

    gtin: str
    on_website: bool
    in_gs1: bool
    site_link: str | None = None

    @property
    def eligible(self) -> bool:
        """True when this product may have a page/QR created (in GS1, not yet on site)."""
        return self.in_gs1 and not self.on_website


def _is_filled(value: object) -> bool:
    """Return whether a spreadsheet cell holds a non-blank value."""
    return value is not None and str(value).strip() != ""


def _coerce_gtin(value: object) -> str | None:
    """Coerce a barcode cell to a GTIN-14 digit string, or ``None`` when blank.

    A 13-digit control-file barcode is zero-padded to 14 digits so it joins to a
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


def load_website_status(config: WebsiteStatusConfig) -> dict[str, WebsiteStatus]:
    """Load the website-status control file, keyed by GTIN-14 (create-only gate).

    Args:
        config: The client's ``website_status`` configuration (path + column names).

    Returns:
        Mapping of GTIN-14 to its :class:`WebsiteStatus`. GTINs absent from the file are
        simply absent from the mapping (callers treat them as ineligible/unknown).

    Raises:
        WebsiteStatusError: If the file cannot be opened, or no worksheet contains a
            header row with all of the required columns (GTIN / on-website / in-GS1).
    """
    try:
        with zipfile.ZipFile(config.path) as zf:
            shared = _read_shared_strings(zf)
            required = (config.gtin_column, config.on_website_column, config.in_gs1_column)
            for sheet_path in _worksheet_paths(zf):
                rows = _read_sheet(zf, sheet_path, shared)
                header = _find_header(rows, required)
                if header is not None:
                    header_index, name_to_col = header
                    return _rows_to_statuses(rows[header_index + 1 :], name_to_col, config)
    except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise WebsiteStatusError(
            f"cannot read website-status file at {config.path}: {exc}"
        ) from exc

    raise WebsiteStatusError(
        f"website-status file at {config.path} has no sheet with the required columns "
        f"({config.gtin_column!r}, {config.on_website_column!r}, {config.in_gs1_column!r})"
    )


def _rows_to_statuses(
    data_rows: list[dict[str, str]],
    name_to_col: dict[str, str],
    config: WebsiteStatusConfig,
) -> dict[str, WebsiteStatus]:
    """Build the GTIN-14 → :class:`WebsiteStatus` mapping from the data rows below the header."""
    gtin_col = name_to_col[config.gtin_column]
    on_website_col = name_to_col[config.on_website_column]
    in_gs1_col = name_to_col[config.in_gs1_column]
    site_link_col = name_to_col.get(config.site_link_column) if config.site_link_column else None

    statuses: dict[str, WebsiteStatus] = {}
    for cells in data_rows:
        gtin = _coerce_gtin(cells.get(gtin_col))
        if gtin is None:
            continue
        link_cell = cells.get(site_link_col) if site_link_col else None
        statuses[gtin] = WebsiteStatus(
            gtin=gtin,
            on_website=_is_filled(cells.get(on_website_col)),
            in_gs1=_is_filled(cells.get(in_gs1_col)),
            site_link=str(link_cell).strip() if _is_filled(link_cell) else None,
        )
    _log.info("Loaded %d website-status rows from %s", len(statuses), config.path)
    return statuses


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


def _find_header(
    rows: list[dict[str, str]], required: tuple[str, ...]
) -> tuple[int, dict[str, str]] | None:
    """Find the first row containing all ``required`` column names; return its index + map.

    Returns ``(row_index, {column_name: column_letter})`` for the header row, or ``None``
    when no row in the sheet holds every required column (so the caller tries the next
    sheet).
    """
    wanted = set(required)
    for index, cells in enumerate(rows):
        name_to_col = {text.strip(): col for col, text in cells.items()}
        if wanted <= name_to_col.keys():
            return index, name_to_col
    return None
