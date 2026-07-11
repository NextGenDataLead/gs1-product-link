"""Reader for GS1 Data Source / GDSN datapool Excel exports.

This is a spec extension (see ``docs/IMPLEMENTATION_SPEC.md`` §3 notes). Where
``lib.records.parse_excel_row`` handles a flat single-sheet export, this module
handles the rich multi-worksheet GDSN datapool export the pilot client (Noviplast)
actually produces.

Structure of a GDSN export:

* One worksheet per GDSN module (``TradeItemDescription``, ``MarketingInformation``,
  ``TradeItemMeasurements``, ``ReferencedFileDetailInformation``, ...).
* Seven header rows per sheet (data starts on the eighth). Each column's identity is
  a nested attribute *path* (e.g. ``TradeItemDescriptionInformation > DescriptionShort[0]
  > Value``) plus a human *label* carrying the stable GDSN attribute number, e.g.
  ``"Short product name (3297)"``.
* Every sheet is keyed on ``Gtin`` + ``TargetMarketCountryCode`` +
  ``TradeItemUnitDescriptorCode``; the same GTIN recurs once per target market.
* Localised text is stored as adjacent ``LanguageCode`` / ``Value`` column pairs within
  a repeated group; measurements as ``MeasurementUnitCode`` / ``Value`` pairs.

The client declares, in ``clients.yml``, which GDSN attribute feeds each
:class:`~lib.records.ProductRecord` field (a :class:`GdsnSource`) and which market
supplies each language (``market_language``). :func:`build_records` joins across sheets
by GTIN and produces the canonical records.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, NamedTuple

import openpyxl
from pydantic import BaseModel, ConfigDict

from lib.errors import ExportParseError
from lib.records import ProductRecord, _coerce_cell, build_product_record

# --- Constants ---------------------------------------------------------------

#: The four key columns present at the start of every GDSN module sheet.
_GTIN_SEGMENT: Final = "Gtin"
_MARKET_SEGMENT: Final = "TargetMarketCountryCode"
_UNIT_SEGMENT: Final = "TradeItemUnitDescriptorCode"

#: Only consumer base units carry the product-page content we publish.
CONSUMER_UNIT: Final = "BASE_UNIT_OR_EACH"

#: Header leaf names with special pairing/interpretation semantics.
_LEAF_VALUE: Final = "Value"
_LEAF_LANGUAGE: Final = "LanguageCode"
_LEAF_UNIT: Final = "MeasurementUnitCode"
_LEAF_URI: Final = "UniformResourceIdentifier"
_LEAF_IS_PRIMARY: Final = "IsPrimaryFile"
_FILE_GROUP_PREFIX: Final = "ReferencedFileHeader"
_FILE_TYPE_SEGMENT: Final = "ReferencedFileTypeCode"
_PRODUCT_IMAGE_TYPE: Final = "PRODUCT_IMAGE"

#: ProductRecord fields that must resolve for a record to be publishable (E5/E17).
REQUIRED_FIELDS: Final[frozenset[str]] = frozenset({"brand", "product_name"})

#: Truthy spellings of the ``IsPrimaryFile`` flag.
_TRUE_VALUES: Final[frozenset[str]] = frozenset({"true", "1", "yes"})

#: How far to scan for the first data row before giving up on a sheet.
_MAX_HEADER_SCAN: Final = 40

_ATTR_ID_RE: Final = re.compile(r"\((\d+)\)\s*$")
_INDEX_RE: Final = re.compile(r"\[\d+\]$")


def _strip_index(segment: str) -> str:
    """Drop a trailing repeated-group index, e.g. ``"DescriptionShort[0]"`` → base."""
    return _INDEX_RE.sub("", segment)


# --- Mapping source (declared per client in clients.yml) ---------------------


class GdsnSource(BaseModel):
    """Where a :class:`~lib.records.ProductRecord` field is sourced in a GDSN export.

    Attributes:
        sheet: The worksheet (GDSN module) holding the attribute.
        attribute: The GDSN attribute number (e.g. ``"3297"``) or a path segment
            name (e.g. ``"GpcCategoryCode"``) identifying the column.
        localised: Whether the attribute is a per-language ``LanguageCode``/``Value``
            group (resolved once per configured language).
        with_unit: Whether to append the paired ``MeasurementUnitCode`` to the value.
        primary_file: Whether to resolve the primary referenced-file URI instead of a
            plain attribute (used for ``image_url``).
    """

    model_config = ConfigDict(frozen=True)

    sheet: str
    attribute: str = ""
    localised: bool = False
    with_unit: bool = False
    primary_file: bool = False


# --- Column / sheet models ---------------------------------------------------


@dataclass(frozen=True)
class GdsnColumn:
    """One resolved column in a GDSN sheet."""

    index: int
    path: tuple[str, ...]
    label: str | None
    attr_id: str | None

    @property
    def leaf_name(self) -> str:
        """The index-stripped final path segment (e.g. ``"Value"``)."""
        return _strip_index(self.path[-1]) if self.path else ""

    @property
    def group_path(self) -> tuple[str, ...]:
        """The path with its leaf removed, used to pair sibling columns."""
        return self.path[:-1]

    def matches_attribute(self, attribute: str) -> bool:
        """Whether this column belongs to ``attribute`` (number or segment name)."""
        if attribute.isdigit():
            return self.attr_id == attribute
        return any(_strip_index(seg) == attribute for seg in self.path)


class GdsnSheet:
    """A parsed GDSN worksheet: its columns plus a (gtin, market) → row index."""

    def __init__(
        self,
        name: str,
        columns: list[GdsnColumn],
        rows_by_key: dict[tuple[str, str], tuple[object, ...]],
    ) -> None:
        self.name = name
        self.columns = columns
        self.rows_by_key = rows_by_key

    def _cell(self, row: tuple[object, ...], index: int) -> str | None:
        return _coerce_cell(row[index]) if index < len(row) else None

    def _sibling(self, group_path: tuple[str, ...], leaf: str) -> GdsnColumn | None:
        return next(
            (c for c in self.columns if c.group_path == group_path and c.leaf_name == leaf),
            None,
        )

    def has_attribute(self, attribute: str) -> bool:
        """Whether any column resolves the given attribute."""
        return any(c.matches_attribute(attribute) for c in self.columns)

    def pick_localised(
        self, gtin: str, market: str, attribute: str, lang: str
    ) -> str | None:
        """Return the ``Value`` whose paired ``LanguageCode`` matches ``lang``."""
        row = self.rows_by_key.get((gtin, market))
        if row is None:
            return None
        for value_col in self.columns:
            if value_col.leaf_name != _LEAF_VALUE or not value_col.matches_attribute(attribute):
                continue
            lang_col = self._sibling(value_col.group_path, _LEAF_LANGUAGE)
            if lang_col is None:
                continue
            cell_lang = self._cell(row, lang_col.index)
            if cell_lang and cell_lang.strip().lower() == lang.lower():
                value = self._cell(row, value_col.index)
                if value:
                    return value
        return None

    def pick_scalar(
        self, gtin: str, market: str, attribute: str, with_unit: bool = False
    ) -> str | None:
        """Return a language-agnostic attribute value, optionally with its unit."""
        row = self.rows_by_key.get((gtin, market))
        if row is None:
            return None
        candidates = [c for c in self.columns if c.matches_attribute(attribute)]
        value_col = next(
            (c for c in candidates if c.leaf_name == _LEAF_VALUE),
            None,
        ) or next(
            (c for c in candidates if c.leaf_name not in (_LEAF_LANGUAGE, _LEAF_UNIT)),
            None,
        )
        if value_col is None:
            return None
        value = self._cell(row, value_col.index)
        if value is None:
            return None
        if with_unit:
            unit_col = self._sibling(value_col.group_path, _LEAF_UNIT)
            unit = self._cell(row, unit_col.index) if unit_col else None
            if unit:
                return f"{value} {unit}"
        return value

    def pick_primary_file(self, gtin: str, market: str) -> str | None:
        """Return the URI of the primary product image, with graceful fallbacks."""
        row = self.rows_by_key.get((gtin, market))
        if row is None:
            return None
        groups: dict[str, list[GdsnColumn]] = {}
        for col in self.columns:
            if col.path and col.path[0].startswith(_FILE_GROUP_PREFIX):
                groups.setdefault(col.path[0], []).append(col)

        primary_uris: list[str] = []
        image_uris: list[str] = []
        all_uris: list[str] = []
        for cols in groups.values():
            uri = self._leaf_cell(row, cols, _LEAF_URI)
            if not uri:
                continue
            all_uris.append(uri)
            primary_flag = (self._leaf_cell(row, cols, _LEAF_IS_PRIMARY) or "").lower()
            is_primary = primary_flag in _TRUE_VALUES
            file_type = self._type_cell(row, cols)
            if is_primary:
                primary_uris.append(uri)
            if file_type == _PRODUCT_IMAGE_TYPE:
                image_uris.append(uri)
        for bucket in (primary_uris, image_uris, all_uris):
            if bucket:
                return bucket[0]
        return None

    def _leaf_cell(self, row: tuple[object, ...], cols: list[GdsnColumn], leaf: str) -> str | None:
        col = next((c for c in cols if c.leaf_name == leaf), None)
        return self._cell(row, col.index) if col else None

    def _type_cell(self, row: tuple[object, ...], cols: list[GdsnColumn]) -> str | None:
        col = next(
            (
                c
                for c in cols
                if c.leaf_name == _LEAF_VALUE
                and any(_FILE_TYPE_SEGMENT in seg for seg in c.path)
            ),
            None,
        )
        return self._cell(row, col.index) if col else None


# --- Workbook parsing --------------------------------------------------------


def _data_start_row(rows: list[tuple[object, ...]]) -> int | None:
    """Index of the first row whose first cell is an all-digit GTIN."""
    for i, row in enumerate(rows[:_MAX_HEADER_SCAN]):
        first = row[0] if row else None
        if first is not None and str(first).strip().isdigit():
            return i
    return None


def _parse_columns(rows: list[tuple[object, ...]], data_start: int) -> list[GdsnColumn]:
    """Reconstruct each column's attribute path and label from the header rows."""
    header_rows = rows[:data_start]
    ncols = max((len(r) for r in header_rows), default=0)
    columns: list[GdsnColumn] = []
    for idx in range(ncols):
        segments = [
            str(hr[idx]).strip()
            for hr in header_rows
            if idx < len(hr) and hr[idx] not in (None, "")
        ]
        if not segments:
            columns.append(GdsnColumn(index=idx, path=(), label=None, attr_id=None))
            continue
        path: tuple[str, ...]
        if len(segments) == 1:
            path, label = (segments[0],), segments[0]
        else:
            path, label = tuple(segments[:-1]), segments[-1]
        match = _ATTR_ID_RE.search(label)
        columns.append(
            GdsnColumn(index=idx, path=path, label=label, attr_id=match.group(1) if match else None)
        )
    return columns


def _key_index(columns: list[GdsnColumn], segment: str, fallback: int) -> int:
    """Locate a key column by its first path segment, else use the positional fallback."""
    for col in columns:
        if col.path and col.path[0] == segment:
            return col.index
    return fallback


def read_workbook(path: str) -> dict[str, GdsnSheet]:
    """Parse a GDSN datapool workbook into per-sheet models.

    Args:
        path: Filesystem path to the ``.xlsx`` export.

    Returns:
        Mapping of sheet name to :class:`GdsnSheet`. Reference/metadata sheets with
        no digit-keyed data rows are skipped.
    """
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheets: dict[str, GdsnSheet] = {}
        for name in workbook.sheetnames:
            rows = list(workbook[name].iter_rows(values_only=True))
            data_start = _data_start_row(rows)
            if data_start is None:
                continue
            columns = _parse_columns(rows, data_start)
            gtin_idx = _key_index(columns, _GTIN_SEGMENT, 0)
            market_idx = _key_index(columns, _MARKET_SEGMENT, 1)
            unit_idx = _key_index(columns, _UNIT_SEGMENT, 3)
            rows_by_key: dict[tuple[str, str], tuple[object, ...]] = {}
            for row in rows[data_start:]:
                gtin = _coerce_cell(row[gtin_idx]) if gtin_idx < len(row) else None
                if gtin is None or not gtin.isdigit():
                    continue  # E4: empty / key-less row skipped silently
                unit = _coerce_cell(row[unit_idx]) if unit_idx < len(row) else None
                if unit is not None and unit != CONSUMER_UNIT:
                    continue
                market = _coerce_cell(row[market_idx]) if market_idx < len(row) else None
                rows_by_key[(gtin, market or "")] = row
            sheets[name] = GdsnSheet(name=name, columns=columns, rows_by_key=rows_by_key)
        return sheets
    finally:
        workbook.close()


# --- Record building ---------------------------------------------------------


class BuildResult(NamedTuple):
    """Outcome of :func:`build_records`: records plus non-fatal/fatal messages."""

    records: list[ProductRecord]
    warnings: list[str]
    errors: list[str]


def _validate_sources(
    workbook: dict[str, GdsnSheet],
    gdsn_map: dict[str, GdsnSource],
) -> tuple[list[str], list[str]]:
    """Check each mapped source resolves; required fields error, optional fields warn (E17)."""
    warnings: list[str] = []
    errors: list[str] = []
    for field, src in gdsn_map.items():
        if field == "gtin":
            continue
        sheet = workbook.get(src.sheet)
        missing = sheet is None or (
            not src.primary_file and bool(src.attribute) and not sheet.has_attribute(src.attribute)
        )
        if not missing:
            continue
        detail = (
            f"field {field!r}: source sheet {src.sheet!r}/"
            f"attribute {src.attribute!r} not found"
        )
        (errors if field in REQUIRED_FIELDS else warnings).append(detail)
    return warnings, errors


def build_records(
    workbook: dict[str, GdsnSheet],
    gdsn_map: dict[str, GdsnSource],
    market_language: dict[str, str],
    default_language: str,
    gdsn_extras: dict[str, GdsnSource] | None = None,
) -> BuildResult:
    """Join GDSN sheets by GTIN into canonical :class:`~lib.records.ProductRecord`s.

    Args:
        workbook: Parsed sheets from :func:`read_workbook`.
        gdsn_map: ProductRecord field → :class:`GdsnSource`.
        market_language: ``{market_code: language}`` — which market supplies each
            language (e.g. ``{"528": "nl", "056": "fr"}``).
        default_language: The language whose ``product_name`` is required (E5).
        gdsn_extras: Optional named pass-through attributes carried into ``extras``.

    Returns:
        A :class:`BuildResult`. ``records`` holds successfully built products; ``errors``
        holds per-GTIN or config-level failures (caller exits non-zero and writes nothing).
    """
    gdsn_extras = gdsn_extras or {}
    warnings, errors = _validate_sources(workbook, gdsn_map)
    if errors:
        return BuildResult(records=[], warnings=warnings, errors=errors)

    lang_to_market = {lang: market for market, lang in market_language.items()}
    if default_language not in lang_to_market:
        return BuildResult(
            records=[],
            warnings=warnings,
            errors=[f"default_language {default_language!r} has no market in market_language"],
        )
    ctx = _BuildContext(
        workbook=workbook,
        lang_to_market=lang_to_market,
        primary_market=lang_to_market[default_language],
        default_language=default_language,
    )

    gtins = sorted({gtin for sheet in workbook.values() for (gtin, _market) in sheet.rows_by_key})
    records: list[ProductRecord] = []
    for gtin in gtins:
        acc = _Accumulator(scalars={"gtin": gtin}, localised={}, extras={})
        for field, src in gdsn_map.items():
            if field != "gtin":
                _resolve_field(ctx, field, src, gtin, acc)
        for name, src in gdsn_extras.items():
            value = _resolve_extra(ctx, src, gtin)
            if value is not None:
                acc.extras[name] = value

        product_name = acc.localised.get("product_name")
        if not product_name or default_language not in product_name:
            errors.append(f"GTIN {gtin}: missing product_name.{default_language}")  # E5
            continue
        try:
            records.append(
                build_product_record(
                    gtin=gtin, scalars=acc.scalars, localised=acc.localised, extras=acc.extras
                )
            )
        except ExportParseError as exc:
            errors.append(str(exc))
    return BuildResult(records=records, warnings=warnings, errors=errors)


@dataclass(frozen=True)
class _BuildContext:
    """Shared inputs threaded through per-field resolution."""

    workbook: dict[str, GdsnSheet]
    lang_to_market: dict[str, str]
    primary_market: str
    default_language: str


@dataclass
class _Accumulator:
    """Per-GTIN field values collected before constructing the record."""

    scalars: dict[str, str]
    localised: dict[str, dict[str, str]]
    extras: dict[str, str]


def _resolve_field(
    ctx: _BuildContext, field: str, src: GdsnSource, gtin: str, acc: _Accumulator
) -> None:
    """Resolve one mapped field for one GTIN into the accumulator."""
    sheet = ctx.workbook.get(src.sheet)
    if sheet is None:
        return
    if src.localised:
        values = {
            lang: value
            for lang, market in ctx.lang_to_market.items()
            if (value := sheet.pick_localised(gtin, market, src.attribute, lang)) is not None
        }
        if values:
            acc.localised[field] = values
    elif src.primary_file:
        value = sheet.pick_primary_file(gtin, ctx.primary_market)
        if value is not None:
            acc.scalars[field] = value
    else:
        value = sheet.pick_scalar(gtin, ctx.primary_market, src.attribute, src.with_unit)
        if value is not None:
            acc.scalars[field] = value


def _resolve_extra(ctx: _BuildContext, src: GdsnSource, gtin: str) -> str | None:
    """Resolve a pass-through extra to a single string (default language for localised)."""
    sheet = ctx.workbook.get(src.sheet)
    if sheet is None:
        return None
    if src.localised:
        market = ctx.lang_to_market.get(ctx.default_language, ctx.primary_market)
        return sheet.pick_localised(gtin, market, src.attribute, ctx.default_language)
    if src.primary_file:
        return sheet.pick_primary_file(gtin, ctx.primary_market)
    return sheet.pick_scalar(gtin, ctx.primary_market, src.attribute, src.with_unit)
