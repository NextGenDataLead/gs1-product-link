"""Website-status control file: the create-only eligibility gate.

Loads the operator-maintained control file (``input/{client_id}/website_status.xlsx``)
that determines which products may have a WordPress page and QR created. A product is
**eligible** only when its GTIN is already registered in GS1 (its resolver record
exists) and it is not yet on the website. This is not part of the datasource export and
not in the original spec — a deliberate, per-client extension for the pilot's
create-only workflow (see :class:`lib.config.WebsiteStatusConfig`).

The file is a flat single-sheet workbook whose columns are named in the client's
``website_status`` config, so a client can relabel them without code changes. Both the
"on website" and "in GS1" columns are treated as booleans: a cell counts as **filled**
(``True``) when it holds any non-blank value, and blank (``None`` or whitespace) is
``False``. Rows are keyed by GTIN, coerced to a digit string to match
:attr:`lib.records.ProductRecord.gtin`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import BaseModel, ConfigDict

from lib.errors import WebsiteStatusError

if TYPE_CHECKING:
    from lib.config import WebsiteStatusConfig

_log = logging.getLogger(__name__)


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
    """Coerce a barcode cell to a digit-string GTIN, or ``None`` when blank.

    Mirrors the export's GTIN handling (edge E1/E2): openpyxl reads numeric barcodes as
    ``int``/``float``, so a whole-number float like ``8712345678905.0`` becomes
    ``"8712345678905"``; text barcodes keep their leading zeros verbatim.
    """
    if not _is_filled(value):
        return None
    if isinstance(value, bool):  # bool is an int subclass; never a GTIN
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    return str(value).strip()


def load_website_status(config: WebsiteStatusConfig) -> dict[str, WebsiteStatus]:
    """Load the website-status control file, keyed by GTIN (create-only gate).

    Args:
        config: The client's ``website_status`` configuration (path + column names).

    Returns:
        Mapping of GTIN to its :class:`WebsiteStatus`. GTINs absent from the file are
        simply absent from the mapping (callers treat them as ineligible/unknown).

    Raises:
        WebsiteStatusError: If the file cannot be opened, is empty, or is missing one
            of the required columns (GTIN / on-website / in-GS1).
    """
    try:
        workbook = openpyxl.load_workbook(config.path, read_only=True, data_only=True)
    except (OSError, InvalidFileException) as exc:
        raise WebsiteStatusError(
            f"cannot read website-status file at {config.path}: {exc}"
        ) from exc

    try:
        sheet = workbook.active
        if sheet is None:
            raise WebsiteStatusError(f"website-status file at {config.path} has no active sheet")
        row_iter = sheet.iter_rows(values_only=True)
        header = [str(c).strip() if c is not None else "" for c in next(row_iter, ())]

        gtin_idx = _require_column(header, config.gtin_column, config.path)
        on_website_idx = _require_column(header, config.on_website_column, config.path)
        in_gs1_idx = _require_column(header, config.in_gs1_column, config.path)
        site_link_idx = (
            header.index(config.site_link_column)
            if config.site_link_column and config.site_link_column in header
            else None
        )

        statuses: dict[str, WebsiteStatus] = {}
        for raw in row_iter:
            gtin = _coerce_gtin(_cell(raw, gtin_idx))
            if gtin is None:
                continue
            site_link_cell = _cell(raw, site_link_idx) if site_link_idx is not None else None
            statuses[gtin] = WebsiteStatus(
                gtin=gtin,
                on_website=_is_filled(_cell(raw, on_website_idx)),
                in_gs1=_is_filled(_cell(raw, in_gs1_idx)),
                site_link=str(site_link_cell).strip() if _is_filled(site_link_cell) else None,
            )
    finally:
        workbook.close()

    _log.info("Loaded %d website-status rows from %s", len(statuses), config.path)
    return statuses


def _require_column(header: list[str], column: str, path: str) -> int:
    """Return the header index of ``column``, or raise if it is absent."""
    try:
        return header.index(column)
    except ValueError as exc:
        raise WebsiteStatusError(
            f"website-status file at {path} is missing required column {column!r}"
        ) from exc


def _cell(row: tuple[object, ...], index: int) -> object:
    """Return the cell at ``index``, or ``None`` when the row is short."""
    return row[index] if index < len(row) else None
