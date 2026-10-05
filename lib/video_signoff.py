"""Read the client's filled-in video sign-off sheet into a plan, without touching the mapping.

The video mapping decides whether a product may publish at all, and filling it in is the
client's job, not the tool's. ``scripts/report_video_candidates`` writes them a grid to work
in — one row per (language, file), with ranked candidates — and what comes back is that sheet
with its ``gtin`` column filled in, or a three-column list somebody typed from scratch. This
module turns either into a decision per row. It writes nothing: :mod:`ui.video_map_edit`
applies the edits, and only the ones an operator confirmed.

**A confirmed row is never overwritten.** A GTIN already in the mapping is client sign-off,
and the sheet that arrives may be stale, partly filled, or a copy of an older one. So a row
whose mapping entry already carries a *different* GTIN is a :data:`CONFLICT` — reported, never
applied. The only rows that fill are the ones currently unset. That is this module's whole
safety property, and it is why there is no flag to turn it off.

**What the sheet says about a barcode is not what the mapping means by one.** Excel hands back
``8713195008486`` as a number for a 13-digit EAN, ``…486.0`` as a float, ``08713195008486`` as
text when a leading zero survived, and ``871-3195008486`` when somebody formatted it. There are
already two normalisations in this codebase and they disagree:
:func:`lib.process_list._coerce_gtin` zero-pads without stripping punctuation, and
:func:`lib.media_video.canon_gtin` strips non-digits and then pads. What is written here is read
back by :func:`lib.media_video.fully_mapped_gtins`, so this normalises on **the mapping's own
rule** — ``canon_gtin`` — and refuses what that rule would silently mangle rather than passing
it through to be compared against products forever.

The validation that actually catches mistakes is **"is this GTIN in the export?"**. A transposed
digit usually produces a barcode no product has, and that is reportable; a check digit catches
the same cases and stays silent on the one that matters — a typo that happens to be another real
product. So the export is the authority, and a GTIN absent from it is rejected with the cell
quoted back.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from lib.media_video import CONFIRMED, SKIP, UNSET, VideoMap, canon_gtin, state_of
from lib.xlsx import Grid

#: Header spellings accepted for each column, normalised by :func:`_norm`. The sheet may be our own
#: report (``language`` / ``file`` / ``gtin``) or something the client built; Dutch and French are
#: here because the pilot's own operator files are in both.
LANGUAGE_COLUMNS: Final = ("language", "lang", "languagecode", "taal", "langue")
FILE_COLUMNS: Final = ("file", "filename", "video", "videofile", "bestand", "fichier")
GTIN_COLUMNS: Final = ("gtin", "gtin13", "gtin14", "ean", "ean13", "barcode", "barcodenumber")

#: What was decided about one row.
FILL: Final = "fill"
#: The mapping already says exactly this. Not an error and not work — the ordinary case for a sheet
#: that is a superset of what has already been signed off, which every later round of one is.
UNCHANGED: Final = "unchanged"
#: The mapping carries a different confirmed GTIN. Reported, never applied.
CONFLICT: Final = "conflict"
#: No GTIN in the cell. The client has not signed this row off yet, which is not a problem.
BLANK: Final = "blank"
#: The row cannot be applied: an unknown language or file, or a barcode that is not one.
REJECTED: Final = "rejected"

#: Canonical GTIN width, as :func:`lib.media_video.canon_gtin` pads to.
_GTIN_WIDTH: Final = 14

#: Formatting people put inside a barcode, and the only characters stripped before validating.
_FORMATTING: Final = (" ", ".", "-", " ", "–")


def _norm(text: str) -> str:
    """A header cell reduced for matching: lower-cased, letters and digits only.

    So ``"Language Code"``, ``"language_code"`` and ``"LANGUAGE-CODE"`` are one column, while
    ``"gtin"`` stays distinct from ``"candidate_1_gtin"`` — which matters, because our own report
    carries both and a loose match would read the first *suggestion* as the client's answer.
    """
    return re.sub(r"[^a-z0-9]", "", text.lower())


@dataclass(frozen=True)
class SignoffRow:
    """One row of the client's sheet, and what it means for the mapping.

    Attributes:
        line: The row's 1-based position in the spreadsheet, header included, so a rejection can be
            pointed at — "row 24" is the only address the person fixing it has.
        language: The language cell, verbatim.
        file: The filename cell, verbatim.
        given: The barcode cell exactly as the sheet carried it, kept for every message. A
            rejection that does not quote what it read is unfixable.
        gtin: The canonical GTIN-14, or :data:`SKIP`, or ``""`` when the row yields neither.
        outcome: One of :data:`FILL`, :data:`UNCHANGED`, :data:`CONFLICT`, :data:`BLANK`,
            :data:`REJECTED`.
        detail: Why, in one line, for the outcomes that need a reason.
    """

    line: int
    language: str
    file: str
    given: str
    gtin: str
    outcome: str
    detail: str = ""


@dataclass(frozen=True)
class SignoffPlan:
    """Every row of the sheet, decided. Nothing here has been written.

    Attributes:
        rows: One per data row of the sheet, in sheet order.
    """

    rows: tuple[SignoffRow, ...]

    def of(self, outcome: str) -> tuple[SignoffRow, ...]:
        """The rows with this outcome, in sheet order."""
        return tuple(row for row in self.rows if row.outcome == outcome)

    @property
    def edits(self) -> dict[tuple[str, str], str]:
        """The fills, in the shape :func:`ui.video_map_edit.apply_edits` takes.

        Keyed ``(language, file)`` — the mapping's own coordinates, and the same key the screen's
        per-row editor stages its pending edits under, so one apply path serves both.
        """
        return {(row.language, row.file): row.gtin for row in self.of(FILL)}


def columns(grid: Grid) -> dict[str, int] | None:
    """Locate the three columns the sheet must have, or ``None`` when one is missing.

    Returns ``{"language": i, "file": j, "gtin": k}``. Extra columns are ignored, which is what
    lets the candidates report come back unmodified apart from the column the client filled in.
    """
    found: dict[str, int] = {}
    for name, accepted in (
        ("language", LANGUAGE_COLUMNS),
        ("file", FILE_COLUMNS),
        ("gtin", GTIN_COLUMNS),
    ):
        index = next((n for n, cell in enumerate(grid.header) if _norm(cell) in accepted), None)
        if index is None:
            return None
        found[name] = index
    return found


def is_header(texts: Sequence[str]) -> bool:
    """Whether a row of cell texts is the sheet's header — it names a barcode **and** a file.

    Both, not either: a data row whose filename happens to read ``file`` is one cell away from
    being mistaken for the header, and the title row above a real table often carries one word.
    """
    normalised = {_norm(text) for text in texts}
    return bool(normalised & set(GTIN_COLUMNS)) and bool(normalised & set(FILE_COLUMNS))


def plan(
    grid: Grid,
    vmap: VideoMap,
    *,
    exported: Collection[str],
    languages: Collection[str],
) -> SignoffPlan:
    """Decide every row of the sheet against the mapping and the export.

    Args:
        grid: The sheet, as :func:`lib.xlsx.read_grid` returned it.
        vmap: The mapping as it stands. Never modified.
        exported: GTIN-14s the export carries — ``{p.gtin14 for p in products}``. Empty skips that
            check, deliberately: an import run before ``parse_export`` should report "not in the
            export" for nothing rather than for every row.
        languages: The configured languages, so a sheet naming one the client invented is caught
            rather than silently filling a block no run reads.

    Returns:
        The plan — empty when the sheet has no recognisable columns.
    """
    where = columns(grid)
    if where is None:
        return SignoffPlan(())
    known = {
        language: {entry.file for entry in entries}
        for language, entries in vmap.by_language.items()
    }
    return SignoffPlan(
        tuple(
            _decide(row, line, where, vmap, known, exported=exported, languages=languages)
            # Line 1 is the header to the person reading the sheet, so the first data row is line
            # 2. Off by one here and every rejection points at the row above the problem.
            for line, row in enumerate(grid.rows, start=2)
        )
    )


def _decide(  # noqa: PLR0913 — one collaborator per question this row has to answer
    cells: Sequence[str],
    line: int,
    where: Mapping[str, int],
    vmap: VideoMap,
    known: Mapping[str, set[str]],
    *,
    exported: Collection[str],
    languages: Collection[str],
) -> SignoffRow:
    """One row's outcome. Pure, and with :func:`_against_mapping` the only place the rules live."""
    language = _cell(cells, where["language"])
    file = _cell(cells, where["file"])
    given = _cell(cells, where["gtin"])

    def row(outcome: str, gtin: str = "", detail: str = "") -> SignoffRow:
        return SignoffRow(line, language, file, given, gtin, outcome, detail)

    unaddressable = _unaddressable(language, file, known, languages)
    if unaddressable:
        return row(REJECTED, detail=unaddressable)
    if not given:
        return row(BLANK)

    gtin, problem = _coerce(given)
    if problem:
        return row(REJECTED, detail=problem)
    if gtin != SKIP and exported and gtin not in exported:
        return row(
            REJECTED,
            gtin,
            detail=f"{gtin} is not in the export — check it, or export it from MyGS1",
        )

    outcome, detail = _against_mapping(vmap, language, file, gtin)
    return row(outcome, gtin, detail)


def _unaddressable(
    language: str, file: str, known: Mapping[str, set[str]], languages: Collection[str]
) -> str:
    """Why this row names no mapping entry, or ``""`` when it names one.

    Checked before the barcode, because a perfectly good GTIN against a filename the mapping has
    never heard of is still nothing this import can do — and "unknown file" is the message that
    gets it fixed, where "not in the export" would send somebody to MyGS1 for a typo in a filename.
    """
    if not file:
        return "no filename in this row"
    if language not in languages:
        return f"{language!r} is not a configured language ({', '.join(sorted(languages))})"
    if file not in known.get(language, set()):
        return f"the mapping has no {language} row for this file — add it on this screen first"
    return ""


def _against_mapping(vmap: VideoMap, language: str, file: str, gtin: str) -> tuple[str, str]:
    """``(outcome, detail)`` for a usable barcode against what the mapping already says."""
    current = next(
        (entry.gtin for entry in vmap.by_language.get(language, []) if entry.file == file), ""
    )
    if state_of(current) == UNSET:
        return FILL, ""
    if _same(current, gtin):
        return UNCHANGED, ""
    return CONFLICT, f"the mapping already says {current.strip()!r} for this file, confirmed"


def _same(current: str, incoming: str) -> bool:
    """Whether the mapping already carries this answer. ``skip`` compares as itself, not a GTIN."""
    if state_of(current) == SKIP:
        return incoming == SKIP
    if incoming == SKIP:
        return False
    return state_of(current) == CONFIRMED and canon_gtin(current) == incoming


def _coerce(given: str) -> tuple[str, str]:
    """A barcode cell as ``(canonical GTIN or ``skip``, problem)``. One of the two is always empty.

    Refuses rather than normalises in the two cases where normalising would be a guess:

    * **Letters or other junk in the cell.** ``canon_gtin`` strips every non-digit, so ``"EAN 871"``
      and ``"see tab 2"`` both become plausible-looking GTIN-14s. Stripping *formatting* is fair —
      spaces, dots and hyphens are how people write barcodes down — so those pass and nothing else
      does.
    * **More than 14 digits.** ``zfill`` does not truncate, so such a value would simply never match
      a product, and the row would report as "not in the export" when the real fault is a
      concatenated cell.

    Scientific notation gets a message of its own because it is the commonest way a barcode arrives
    broken and the least obvious to the person who sent it: Excel renders a 13-digit number as
    ``8.7132E+12`` in a narrow column, and a CSV round trip bakes that in. Told only "expected
    digits", somebody re-types the same cell and sends the same file back.
    """
    value = given.strip()
    if value.lower() == SKIP:
        return SKIP, ""
    if re.fullmatch(r"\d(?:[.,]\d+)?[eE][+-]?\d+", value):
        return "", (
            f"{given!r} is scientific notation, not a barcode — Excel does this to a long number. "
            "Format that column as text, re-enter the barcode, and save again."
        )
    # Excel hands a 13-digit barcode back as a number, and a whole-number float is that same
    # barcode. Only a trailing ``.0`` — anything else that looks decimal is not a GTIN.
    if re.fullmatch(r"\d+\.0+", value):
        value = value.split(".", 1)[0]
    cleaned = value
    for char in _FORMATTING:
        cleaned = cleaned.replace(char, "")
    if not cleaned.isdigit():
        return "", f"{given!r} is not a barcode — expected digits, or the word {SKIP!r}"
    if len(cleaned) > _GTIN_WIDTH:
        return "", (
            f"{given!r} has {len(cleaned)} digits — a GTIN has at most {_GTIN_WIDTH}. A cell "
            "formatted as a number can come back like this; set the column to text and re-save."
        )
    return canon_gtin(cleaned), ""


def _cell(cells: Sequence[str], index: int) -> str:
    """One cell, stripped, or ``""`` when the row is shorter than the header."""
    return cells[index].strip() if 0 <= index < len(cells) else ""
