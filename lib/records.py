"""Canonical record schema and export-row parsing.

Implements ``docs/IMPLEMENTATION_SPEC.md`` §2 (type definitions) and §4.9
(``parse_excel_row``). :class:`ProductRecord` is the normalised, language-agnostic
internal shape produced by ``scripts/parse_export.py`` and consumed by every
downstream module (templates, WordPress client, GS1 client, QR, state).

The client-specific *bridge* from a raw export to this shape lives in the column
mapping (§3): for flat single-sheet exports here in :func:`parse_excel_row`, and
for GS1 Data Source / GDSN datapool exports in ``lib/gdsn.py`` (a spec extension —
see §3 notes).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lib.errors import ExportParseError

# --- Canonical target paths (§3.2) -------------------------------------------
#
# The set of ``ProductRecord`` field paths a column map may target. Shared with
# ``lib.config`` so an invalid mapping is caught at config-load time (edge E6).

#: Language-agnostic scalar fields addressable by a bare name.
SCALAR_TARGETS: Final[frozenset[str]] = frozenset(
    {"gtin", "brand", "gpc_brick_code", "net_content", "image_url", "category"}
)

#: Per-language fields addressable via dotted ``<field>.<lang>`` notation.
LOCALISED_TARGETS: Final[frozenset[str]] = frozenset(
    {"product_name", "description_short", "description_long"}
)

#: Prefix for free-form pass-through targets (``extras.<name>``).
_EXTRAS_PREFIX: Final = "extras"


def is_valid_target_path(path: str) -> bool:
    """Return whether ``path`` is a mappable ``ProductRecord`` field path (§3.2).

    Args:
        path: A canonical target path, e.g. ``"gtin"``, ``"product_name.nl"``,
            or ``"extras.hs_code"``.

    Returns:
        ``True`` for a language-agnostic scalar, a ``<localised>.<lang>`` path, or
        an ``extras.<name>`` path; ``False`` otherwise.
    """
    if "." not in path:
        return path in SCALAR_TARGETS
    head, _, tail = path.partition(".")
    if not tail:
        return False
    return head in LOCALISED_TARGETS or head == _EXTRAS_PREFIX


# --- Records (§2.1) ----------------------------------------------------------


class LocalisedText(BaseModel):
    """A text value that varies per language.

    Keys are ISO 639-1 codes (nl, en, fr, de, ...).

    Attributes:
        values: Mapping of language code to text.
    """

    model_config = ConfigDict(frozen=True)

    values: dict[str, str]

    def get(self, lang: str, fallback: str | None = None) -> str | None:
        """Return the text for ``lang``, else the ``fallback`` language's text.

        Args:
            lang: Preferred ISO 639-1 language code.
            fallback: Language code to fall back to when ``lang`` is absent.

        Returns:
            The matching text, or ``None`` when neither is present.
        """
        return self.values.get(lang, self.values.get(fallback) if fallback else None)


class ProductRecord(BaseModel):
    """The canonical internal shape for one product (§2.1).

    Language-agnostic at the top level; language-specific fields nested in
    :class:`LocalisedText`.
    """

    model_config = ConfigDict(frozen=True)

    gtin: str = Field(..., pattern=r"^\d{8,14}$")
    brand: str
    product_name: LocalisedText

    gpc_brick_code: str | None = None
    net_content: str | None = None
    image_url: str | None = None
    category: str | None = None

    description_short: LocalisedText | None = None
    description_long: LocalisedText | None = None

    extras: dict[str, str] = Field(default_factory=dict)

    @property
    def gtin14(self) -> str:
        """The GTIN zero-padded to 14 digits for Digital Link URIs."""
        return self.gtin.zfill(14)


# --- Plan types (§2.2) -------------------------------------------------------


class PlanClassification(StrEnum):
    """How a plan row compares to prior state (§2.2)."""

    NEW = "new"
    UNCHANGED = "unchanged"
    CHANGED = "changed"


class PlanRow(BaseModel):
    """One (GTIN, language) unit of work in a :class:`Plan` (§2.2)."""

    model_config = ConfigDict(frozen=True)

    gtin: str
    language: str
    classification: PlanClassification
    title: str
    slug: str
    content_hash: str
    target_url: str
    diff: dict[str, tuple[str, str]] | None = None
    product: ProductRecord


class Plan(BaseModel):
    """A full run plan for one client (§2.2)."""

    model_config = ConfigDict(frozen=True)

    client_id: str
    generated_at: datetime
    total: int
    counts: dict[PlanClassification, int]
    rows: list[PlanRow]


class ConfirmedPlan(BaseModel):
    """A :class:`Plan` plus the operator-confirmed subset to execute (§2.2)."""

    model_config = ConfigDict(frozen=True)

    plan: Plan
    confirmed_gtins_by_lang: set[tuple[str, str]]


# --- Run/state types (§2.3) — intentionally mutable --------------------------


class RunOutcome(BaseModel):
    """The result of processing one (GTIN, language) during a run (§2.3)."""

    gtin: str
    language: str
    ts: datetime
    status: str
    wp_page_id: int | None = None
    wp_url: str | None = None
    wp_featured_media_id: int | None = None
    gs1_set: bool = False
    qr_paths: list[str] = Field(default_factory=list)
    error: str | None = None


class SourceIssue(BaseModel):
    """One defect in the source datapool, for the operator to fix upstream.

    The tool reports these rather than repairing them: the datapool is the authoritative
    record, so a value silently corrected here stays wrong in MyGS1 and comes back on the
    next export. Emitted to ``output/{client_id}/data/source_issues.json`` — a file rather
    than a log line, because the work of fixing them happens later, elsewhere, by a person.

    The eventual home for generated-content reporting too: when the LLM fills a gap the feed
    should have carried, that is the same kind of finding — a datapool gap with a suggested
    value. Success is this file shrinking to empty.

    Attributes:
        gtin: The product, so it can be found in MyGS1.
        field: Dotted path in *our* vocabulary, e.g. ``product_name.nl``. Useful for
            debugging the tool; useless for finding the field in the source system.
        source: The same field in the **source system's** vocabulary, e.g.
            ``MarketingInformation attr 1083``. This is what the operator searches MyGS1
            for — ``description_short`` exists nowhere but in this codebase, and a work
            queue naming fields nobody can find is not a work queue.
        issue: Machine-readable kind, e.g. ``brand_prefix_mismatch``.
        value: The current source value, verbatim.
        detail: One human-readable sentence: what is wrong and what to do.
    """

    model_config = ConfigDict(frozen=True)

    gtin: str
    field: str
    source: str = ""
    issue: str
    value: str
    detail: str


class StateEntry(BaseModel):
    """Persisted state for one (GTIN, language) between runs (§2.3).

    ``title`` is the page title as last written. It is the one product field state
    keeps verbatim, so that a re-run can show a real before/after in a CHANGED row's
    diff (§10.6.2) — ``content_hash`` proves *that* something changed but, being a
    digest, can never say *what*. It is optional because state files written before
    titles were persisted have none; ``None`` means "not recorded", and the diff omits
    the title rather than guessing an old value.
    """

    wp_page_id: int
    wp_url: str
    wp_featured_media_id: int | None
    content_hash: str
    gs1_link_set_hash: str
    last_run: datetime
    title: str | None = None


class State(BaseModel):
    """The full persisted state for a client (§2.3).

    ``entries`` is keyed ``entries[gtin][language]``.

    ``reset_from_corrupt`` is set by :func:`lib.state.load_state` when it recovered from a
    corrupt state file (edge E19) and is excluded from serialisation — it describes *this*
    load, not the persisted state. It exists so the reset reaches the operator in the plan
    summary they actually read: a reset silently turns an incremental re-run into a full
    rewrite (every row reclassifies as NEW), and an ERROR log line is too quiet for that.
    """

    client_id: str
    entries: dict[str, dict[str, StateEntry]]
    reset_from_corrupt: bool = Field(default=False, exclude=True)


# --- Flat single-sheet row parsing (§4.9) ------------------------------------


def _coerce_cell(value: object) -> str | None:
    """Coerce a raw spreadsheet cell to a trimmed string, or ``None`` if empty.

    Handles the openpyxl casting behaviours behind edge cases E1/E2: text GTINs
    keep their leading zeros verbatim, integer GTINs become their decimal string.

    Args:
        value: The raw cell value from openpyxl (``str``, ``int``, ``float``,
            ``bool``, ``datetime``, or ``None``).

    Returns:
        The normalised string, or ``None`` for empty/blank cells.
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, bool):  # bool is a subclass of int — check it first
        return "true" if value else "false"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)  # int and any other scalar


def parse_excel_row(
    row: dict[str, object],
    column_map: dict[str, str],
    extras_columns: list[str],
    default_language: str,
) -> ProductRecord:
    """Parse one flat export row into a :class:`ProductRecord` (§4.9).

    This is the flat single-sheet path. Rich GS1 Data Source / GDSN exports are
    handled by ``lib/gdsn.py`` instead.

    Args:
        row: Mapping of Excel column name to raw cell value.
        column_map: Mapping of Excel column name to a canonical target path (§3.2).
        extras_columns: Excel column names carried verbatim into ``extras`` under
            the column name as spelled.
        default_language: The language whose ``product_name`` is required (§3.3).

    Returns:
        The parsed, validated record.

    Raises:
        ExportParseError: If a required field is missing (E5) or the row fails
            record validation (e.g. a malformed GTIN). The GTIN, when known, is
            included in the message.
    """
    scalars: dict[str, str] = {}
    localised: dict[str, dict[str, str]] = {}
    extras: dict[str, str] = {}

    for col, target in column_map.items():
        val = _coerce_cell(row.get(col))
        if val is None:
            continue
        if "." in target:
            head, _, tail = target.partition(".")
            if head in LOCALISED_TARGETS:
                localised.setdefault(head, {})[tail] = val
            elif head == _EXTRAS_PREFIX:
                extras[tail] = val
            else:  # defensive — normally rejected at config load (E6)
                raise ExportParseError(f"unknown target path {target!r} in column map")
        elif target in SCALAR_TARGETS:
            scalars[target] = val
        else:  # defensive — normally rejected at config load (E6)
            raise ExportParseError(f"unknown target path {target!r} in column map")

    for name in extras_columns:
        val = _coerce_cell(row.get(name))
        if val is not None:
            extras[name] = val

    gtin = scalars.get("gtin")
    product_name = localised.get("product_name")
    if not product_name or default_language not in product_name:
        raise ExportParseError(f"GTIN {gtin or '?'}: missing product_name.{default_language}")

    return build_product_record(
        gtin=gtin,
        scalars=scalars,
        localised=localised,
        extras=extras,
    )


def build_product_record(
    *,
    gtin: str | None,
    scalars: dict[str, str],
    localised: dict[str, dict[str, str]],
    extras: dict[str, str],
) -> ProductRecord:
    """Assemble a :class:`ProductRecord` from collected field values.

    Shared by the flat parser and the GDSN builder so both surface a typed
    :class:`ExportParseError` (with the GTIN) instead of a raw pydantic trace.

    Args:
        gtin: The product GTIN, used only for the error message here.
        scalars: Language-agnostic field values keyed by field name.
        localised: Per-language field values keyed by field then language code.
        extras: Free-form pass-through values.

    Returns:
        The validated record.

    Raises:
        ExportParseError: If record validation fails.
    """
    fields: dict[str, object] = dict(scalars)
    for field_name, values in localised.items():
        if values:
            fields[field_name] = LocalisedText(values=values)
    if extras:
        fields["extras"] = extras
    try:
        return ProductRecord.model_validate(fields)
    except ValidationError as exc:
        raise ExportParseError(f"GTIN {gtin or '?'}: invalid product record: {exc}") from exc
