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

import io
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final

from lib.media_video import CONFIRMED, SKIP, UNSET, VideoMap, canon_gtin, state_of
from lib.xlsx import Grid, read_grid

#: Header spellings recognised for each column, normalised by :func:`_norm`. These are a **pre-fill,
#: not a requirement**: the screen always shows which column it read for what and lets the operator
#: change it. This list is a guess about somebody else's spreadsheet and it has already been wrong
#: once — the real sign-off sheet calls the barcode ``current_gtin``, so the import refused the one
#: file it exists for. A list like this cannot be completed by thinking harder about it.
LANGUAGE_COLUMNS: Final = ("language", "lang", "languagecode", "taal", "langue")
FILE_COLUMNS: Final = ("file", "filename", "video", "videofile", "bestand", "fichier")
GTIN_COLUMNS: Final = (
    "gtin",
    "gtin13",
    "gtin14",
    "ean",
    "ean13",
    "barcode",
    "barcodenumber",
    # ``current_gtin`` is what an earlier revision of ``report_video_candidates`` called it, and the
    # operator still has sheets in that shape. In both schemas the one barcode column is *both* the
    # mapping's current value and where the client writes their answer — they edit it in place — so
    # reading it is safe by construction: an untouched row decides UNCHANGED, a filled one FILL, and
    # one that disagrees with sign-off CONFLICT, which is never applied.
    "currentgtin",
    "currentean",
)

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
#: Applying this row would leave one GTIN mapped to two files in one language. Reported, never
#: applied — see :func:`_mark_ambiguous`.
AMBIGUOUS: Final = "ambiguous"

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


#: The fewest filled cells a row needs before it could be a header at all. Two, because the sheet
#: has at least a filename and a barcode in it, and a title row above the table is one cell.
_HEADER_CELLS: Final = 2


def read_sheet(source: Path | bytes) -> Grid | None:
    """The sign-off sheet, by the names we know or failing that by shape. ``None`` if neither works.

    Two passes, and the second is why the operator can fix a sheet this module does not recognise.
    The first looks for a header carrying names from :data:`GTIN_COLUMNS` and :data:`FILE_COLUMNS` —
    the ordinary case, and it needs no decisions. The second accepts the first row with
    :data:`_HEADER_CELLS` filled cells, so a sheet whose columns are called something nobody
    anticipated still arrives on screen with its headers listed, where they can be assigned.

    Without that second pass the column picker could not exist: a sheet we cannot find a header in
    is a sheet we cannot show columns for, and the operator would be told to go and rename things in
    Excel to match a list the tool never shows them.

    Taking ``bytes`` as well as a path is not incidental either: each pass needs its own stream,
    and an upload is bytes that were deliberately never written to disk.
    """
    for recognises in (is_header, _could_be_a_header):
        grid = read_grid(_stream(source), header_row=recognises)
        if grid is not None:
            return grid
    return None


#: Two headings every GS1 Data Source export opens with, and no sign-off sheet carries together.
_EXPORT_HEADINGS: Final = frozenset({"gtin", "targetmarketcountrycode"})


def looks_like_an_export(grid: Grid) -> bool:
    """Whether this "sheet" is really a GS1 Data Source export dropped in the wrong place.

    Worth its own refusal because :func:`read_sheet`'s second pass accepts any row with two filled
    cells as a header: an export *does* produce a grid, and the plan then rejects every row of it,
    loudly, about barcodes and filenames — which is the wrong thing to be told. Three identical
    ``.xlsx`` pickers on one screen make this the likeliest mix-up there is.
    """
    return {_norm(cell) for cell in grid.header} >= _EXPORT_HEADINGS


def _stream(source: Path | bytes) -> Path | IO[bytes]:
    """A fresh readable for one pass. A path reopens; bytes need wrapping each time."""
    return io.BytesIO(source) if isinstance(source, bytes) else source


def _could_be_a_header(texts: Sequence[str]) -> bool:
    """Whether a row could be a header at all, knowing nothing about what its columns are called.

    Deliberately crude. It can mistake the first data row for the header, and that is survivable
    precisely because of what happens next: the operator is shown the column names it found, and
    ``fr`` / ``Airfryer Basket_FR.mpg`` listed as column *names* is unmistakable.
    """
    return sum(1 for text in texts if text.strip()) >= _HEADER_CELLS


def column_options(grid: Grid) -> dict[int, str]:
    """Every column of the sheet, labelled for a picker: ``{position: name}``.

    A blank header cell is still a column — the sheet may have one, and it may even be the one
    wanted — so it is offered by position rather than dropped.
    """
    return {
        index: name.strip() or f"(column {index + 1}, no heading)"
        for index, name in enumerate(grid.header)
    }


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
    where: Mapping[str, int] | None = None,
) -> SignoffPlan:
    """Decide every row of the sheet against the mapping and the export.

    Args:
        grid: The sheet, as :func:`read_sheet` returned it.
        vmap: The mapping as it stands. Never modified.
        exported: GTIN-14s the export carries — ``{p.gtin14 for p in products}``. Empty skips that
            check, deliberately: an import run before ``parse_export`` should report "not in the
            export" for nothing rather than for every row.
        languages: The configured languages, so a sheet naming one the client invented is caught
            rather than silently filling a block no run reads.
        where: Which column is which, as ``{"language": i, "file": j, "gtin": k}``. The operator's
            answer when they have given one; :func:`columns` guesses when they have not. Explicit
            rather than always inferred because the guess is about a file this project does not
            control, and it has already been wrong on the only real sheet there is.

    Returns:
        The plan — empty when no columns were given and none could be recognised.
    """
    where = where if where is not None else columns(grid)
    if where is None:
        return SignoffPlan(())
    known = {
        language: {entry.file for entry in entries}
        for language, entries in vmap.by_language.items()
    }
    decided = [
        _decide(row, line, where, vmap, known, exported=exported, languages=languages)
        # Line 1 is the header to the person reading the sheet, so the first data row is line 2.
        # Off by one here and every rejection points at the row above the problem.
        for line, row in enumerate(grid.rows, start=2)
    ]
    return SignoffPlan(_mark_ambiguous(decided, vmap))


def _mark_ambiguous(decided: Sequence[SignoffRow], vmap: VideoMap) -> tuple[SignoffRow, ...]:
    """Turn into :data:`AMBIGUOUS` any fill that would leave one GTIN on two files in one language.

    **One video per product per language, or none at all.** :meth:`VideoMap.resolve` returns
    ``None`` when a GTIN is confirmed to more than one file in a language, so applying such a pair
    does not attach two videos — it attaches *neither*. The product is then held (the gate requires
    exactly one per language), and the report's §1b names both files.

    This is not hypothetical and it is not the sheet being careless. The pilot's own sign-off sheet
    names `Roll Light Summer.mpg` and `Roll Light Winter.mpg` for one GTIN — two genuine videos of
    one product, which the mapping has no way to express — and separately pairs ``Super Trap.mp4``
    with ``Super Trap.mpg``, one video in two formats. Both were applied silently before this check
    existed, and the second was invisible even to the coverage report, because
    :func:`lib.media_video.check_video_map` was comparing raw cells: 13-digit beside 14-digit did
    not look like a duplicate to it, while ``resolve`` canonicalises and saw one.

    Checked over the **resulting** state — existing confirmed rows plus every fill in this sheet —
    because either side can supply the collision, and a sheet that collides with itself is the case
    that actually happened. Reported rather than resolved: which of two files to keep is a decision
    about the videos, and the operator has them open.
    """
    claims: dict[tuple[str, str], list[str]] = {}
    for language, entries in vmap.by_language.items():
        for entry in entries:
            if state_of(entry.gtin) == CONFIRMED:
                claims.setdefault((language, canon_gtin(entry.gtin)), []).append(entry.file)
    for row in decided:
        if row.outcome == FILL and row.gtin != SKIP:
            claims.setdefault((row.language, row.gtin), []).append(row.file)

    marked = []
    for row in decided:
        others = claims.get((row.language, row.gtin), []) if row.outcome == FILL else []
        rival = sorted(name for name in others if name != row.file)
        if row.outcome == FILL and row.gtin != SKIP and rival:
            marked.append(
                SignoffRow(
                    row.line,
                    row.language,
                    row.file,
                    row.given,
                    row.gtin,
                    AMBIGUOUS,
                    f"{row.gtin} would also be mapped to {', '.join(rival)} in {row.language}; "
                    "one GTIN can carry only one video per language, so both would get none",
                )
            )
            continue
        marked.append(row)
    return tuple(marked)


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
