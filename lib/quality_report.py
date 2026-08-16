"""Render the four per-step issue reports into one human-readable markdown worklist.

The pipeline writes several machine-readable issue files
(``source_issues.json``, ``generated_issues.json``, ``video_map_issues.json``,
``category_issues.json``) — each a flat list of :class:`~lib.records.SourceIssue`. This module
folds them into a single report grouped by **owner and action** (what blocks publishing, what a
human must review, what the client fixes in MyGS1), so the operator has one thing to read and hand
off rather than four JSON files.

Pure and deterministic: :func:`render_quality_report` takes already-parsed inputs (including the
snapshot date and per-source freshness) and returns markdown. All I/O and clock access live in
``scripts/report_quality.py``, so the same inputs render byte-identically and the renderer is unit
-testable without touching the filesystem. Each section is built by a small ``_*_lines`` helper.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, NamedTuple

from lib.mandatory import MandatoryGap
from lib.records import ProductRecord, SourceIssue

if TYPE_CHECKING:
    from lib.gdsn import GdsnSource

#: Issue kinds emitted by the generator merge (``generated_issues.json``).
_HELD = "missing_generation_input"
_INFERENCE = "generation_inference"
_GENERATED = "content_generated"
#: A value the generator rendered into a language the feed lacked — §4's MyGS1 work queue.
_TRANSLATED = "value_translated"
#: Issue kinds emitted by the export parser (``source_issues.json``).
_BLANK = "value_blank"
_INCONSISTENT = "value_inconsistent_across_markets"
_WRONG_LANG = "value_wrong_language"

#: Length of an ISO ``YYYY-MM-DD`` freshness date. Anything else (``—``) is "unknown", not old.
_ISO_DATE_LEN = 10

#: Base fields whose blank makes a published page broken/degraded enough to block it: the
#: page title (``product_name``) and the hero image (``image_url``). A blank in any other
#: field (e.g. ``net_content``) only degrades a detail line, so it is a source fix, not a block.
_BLOCKING_BLANK_FIELDS = frozenset({"product_name", "image_url"})


#: Matrix cell marks. A filled circle reads as "there" at a glance across 30 rows in a way that
#: a tick and a dash do not, and the half circle says "one language only" without a legend lookup.
_PRESENT = "●"
_PARTIAL = "◐"
_ABSENT = "○"

#: Column header for the video pair — not a ``gdsn_map`` field, but the same kind of fact.
_VIDEO_COLUMN = "video"


class MatrixInput(NamedTuple):
    """Everything the §0 coverage matrix needs, gathered by the caller.

    Bundled rather than passed as five more keyword arguments: they are one fact — "the scope and
    the shape of its data" — and a renderer signature that already takes nine sources does not
    need five more that must always travel together.
    """

    #: In-scope products, in any order; the matrix sorts them itself.
    products: list[ProductRecord]
    gdsn_map: dict[str, GdsnSource]
    gdsn_extras: dict[str, GdsnSource]
    languages: list[str]
    #: Language → GTIN-14s with a client-confirmed video in it.
    video_confirmed: dict[str, set[str]]


class FieldColumn(NamedTuple):
    """One column of the coverage matrix: where the value comes from and whether it is required."""

    field: str
    #: Short header. GDSN attribute numbers are what an operator searches MyGS1 by, so they win
    #: over the internal field name wherever there is one.
    header: str
    localised: bool
    required: bool


#: The one column that does not come from the feed: a client-confirmed video, per language.
#: Mandatory because a GTIN without one is held out of publishing entirely (E24), so it belongs
#: with the other columns whose gap stops the SKU rather than off at the end of the row.
_VIDEO = FieldColumn("video", _VIDEO_COLUMN, localised=True, required=True)


def _columns(
    gdsn_map: dict[str, GdsnSource], gdsn_extras: dict[str, GdsnSource]
) -> list[FieldColumn]:
    """Matrix columns, every mandatory one first, derived from config rather than listed here.

    Derived so the matrix cannot drift from what the pipeline actually enforces: marking a field
    ``required`` in ``clients.yml`` moves it into the mandatory block here with no code change,
    which is the whole point of a coverage table nobody has to maintain by hand.

    **This is the only thing that orders the matrix** — the renderer takes the list as given and
    neither re-sorts nor splices. Otherwise the header could group the columns one way while the
    cells were built another, and the test pinning a single mandatory→optional crossing would be
    pinning the renderer's own partition instead of this config-derived order.
    """
    mandatory = [
        FieldColumn(name, f"{name.split('_')[0]}·{src.attribute}", src.localised, True)
        for name, src in gdsn_map.items()
        if (src.required or src.required_group) and src.in_matrix
    ]
    optional = [
        FieldColumn(name, f"{name.split('_')[0]}·{src.attribute}", src.localised, False)
        for name, src in gdsn_map.items()
        if not (src.required or src.required_group) and src.in_matrix
    ] + [
        FieldColumn(name, name.replace("_", "·"), src.localised, False)
        for name, src in gdsn_extras.items()
        if src.in_matrix
    ]
    return [*mandatory, _VIDEO, *optional]


def _mark(
    product: ProductRecord,
    column: FieldColumn,
    languages: list[str],
    video_confirmed: dict[str, set[str]],
) -> tuple[str, int]:
    """The cell for one product/column, and how many language slots it fills (for the sort).

    A ``gdsn_extras`` field is counted per language when the record actually carries it that way
    (:attr:`ProductRecord.extras_localised`) and as a single slot otherwise. The distinction is
    the record's, not the config's: ``localised`` describes the *source attribute*, and a
    ``products.json`` written before extras were kept per language holds one flat string however
    the attribute looked. Counting that flat value as a language group would find nothing and
    report every extra missing — wrong in the direction that invents work for the client.
    """
    if column is _VIDEO:
        # Confirmation is per language, which is the shape _language_mark already reads: a video
        # in one of two languages is the same half-filled cell as a name in one of two.
        confirmed = {
            lang: "confirmed"
            for lang in languages
            if product.gtin14 in video_confirmed.get(lang, set())
        }
        return _language_mark(confirmed, languages)
    if column.field not in type(product).model_fields:
        localised = product.extras_localised.get(column.field)
        if localised is None:
            filled = bool(str(product.extras.get(column.field) or "").strip())
            return (_PRESENT if filled else _ABSENT), int(filled)
        return _language_mark(localised.values, languages)
    value = getattr(product, column.field, None)
    if not column.localised:
        filled = bool(str(value or "").strip())
        return (_PRESENT if filled else _ABSENT), int(filled)
    return _language_mark(getattr(value, "values", {}) or {}, languages)


def _language_mark(values: dict[str, str], languages: list[str]) -> tuple[str, int]:
    """The cell for a per-language value: full, half, or empty, plus the slots it fills."""
    have = sum(bool(str(values.get(lang) or "").strip()) for lang in languages)
    if have == len(languages):
        return _PRESENT, have
    return (_PARTIAL if have else _ABSENT), have


def _stalest(freshness: dict[str, str]) -> str:
    """The oldest input date, or "" when none is known.

    Dates are ISO ``YYYY-MM-DD`` so they sort lexically; anything unparseable (``—``) is ignored
    rather than treated as ancient, which would put a scare in the header on missing information.
    """
    dates = [v for v in freshness.values() if len(v) == _ISO_DATE_LEN and v[:4].isdigit()]
    return min(dates) if dates else ""


def _blocks_publish(field: str) -> bool:
    """True when a blank in ``field`` should hold the GTIN out of publishing (title/image)."""
    return field.split(".", 1)[0] in _BLOCKING_BLANK_FIELDS


def _market_cell(issue: SourceIssue) -> str:
    """All target-market values for a cross-market conflict, chosen (highest-ranked) marked ✓.

    Shows the conflicting texts side by side so a reader can compare on the spot instead of
    seeing only the winning value. Falls back to the bare value for legacy issues that predate
    the structured ``market_values`` breakdown.
    """
    if not issue.market_values:
        return _cell(issue.value)
    parts = [
        f"{market}=`{_cell(value)}`" + (" ✓" if idx == 0 else "")
        for idx, (market, value) in enumerate(issue.market_values)
    ]
    return " · ".join(parts)


def _name(products: dict[str, ProductRecord], gtin: str) -> str:
    """Best-effort product label for a GTIN (nl name, then fr); blank if unknown."""
    if not gtin.isdigit():
        return ""
    product = products.get(gtin.zfill(14))
    if product is None or product.product_name is None:
        return ""
    values = product.product_name.values
    return values.get("nl") or values.get("fr") or ""


def _short(gtin: str) -> str:
    """The trailing four digits operators use to refer to a GTIN in chat."""
    return "…" + gtin[-4:] if gtin else "(unassigned)"


def _cell(text: str) -> str:
    """Escape a value for a single markdown table cell (pipes would split the column)."""
    return text.replace("|", "\\|")


def _lang(field: str) -> str:
    """The language suffix of a dotted field like ``generated_description.nl`` (else empty)."""
    return field.rsplit(".", 1)[-1] if "." in field else ""


def _label(products: dict[str, ProductRecord], gtin: str) -> str:
    """A ``\\`gtin\\` (…1234) — Name`` cell, name omitted when unknown."""
    name = _name(products, gtin)
    return f"`{gtin}` ({_short(gtin)})" + (f" — {name}" if name else "")


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    """A markdown table, or a single ``_None._`` line when there are no rows."""
    if not rows:
        return ["_None._"]
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    out += ["| " + " | ".join(cells) + " |" for cells in rows]
    return out


def _header_lines(client_id: str, snapshot: str, freshness: dict[str, str]) -> list[str]:
    """The title block, leading with **when this document was written**.

    On its own line, in local time with the zone named, and first — because the question a reader
    asks of a worklist is how old it is, and the answer used to be a date buried mid-sentence.
    Whoever opens the file sees it without checking a file browser or asking the person who ran it.

    The generation time and the *input* freshness are deliberately separate facts: a report can be
    minutes old and still describe a month-old export, which is exactly the case here.
    """

    def f(key: str) -> str:
        return freshness.get(key, "—")

    stale = _stalest(freshness)
    return [
        f"# {client_id.title()} — Data quality report",
        "",
        f"**Generated {snapshot}**",
        "",
        f"_Describes data as of: generated `{f('generated')}`, source `{f('source')}`, "
        f"video-map `{f('video_map')}`, categories `{f('category')}`."
        + (
            f" **The oldest input is from {stale}** — anything fixed since is not reflected here._"
            if stale
            else "_"
        ),
        "",
        f"> Regenerate the underlying data: `run_plan {client_id}` (generated + categories), "
        f"`parse_export {client_id}` (source), `build_video_map {client_id} --check` (video-map); "
        f"then `python -m scripts.report_quality {client_id}`.",
        "",
    ]


def _summary_lines(  # noqa: PLR0913 — one parameter per source feeding a summary row
    generated_issues: list[SourceIssue],
    source_issues: list[SourceIssue],
    video_map_issues: list[SourceIssue],
    category_issues: list[SourceIssue],
    mandatory_gaps: dict[str, list[MandatoryGap]],
    video_held: list[str],
) -> list[str]:
    by_kind = Counter(i.issue for i in generated_issues)
    held = {i.gtin for i in generated_issues if i.issue == _HELD}
    blanks = [i for i in source_issues if i.issue == _BLANK]
    blocking_blanks = [i for i in blanks if _blocks_publish(i.field)]
    degrade_blanks = [i for i in blanks if not _blocks_publish(i.field)]
    inconsistent = [i for i in source_issues if i.issue == _INCONSISTENT]
    rows = [
        [
            "Source",
            "**Missing mandatory data (E23)**",
            f"{sum(len(g) for g in mandatory_gaps.values())} gaps / {len(mandatory_gaps)} GTINs",
            "Client (MyGS1)",
            "**Yes — whole SKU**",
        ],
        [
            "Media",
            "**No confirmed video (E24)**",
            f"{len(video_held)} GTINs",
            "Client",
            "**Yes — whole SKU**",
        ],
        [
            "Copy",
            "No marketing message (1083)",
            f"{by_kind[_HELD]} rows / {len(held)} GTINs",
            "Client (MyGS1)",
            # Not a flat "Yes": a unit whose 1067 carries copy publishes from it. §1 marks which
            # rows are which, and an unqualified blocker count that includes non-blockers is how
            # the real ones stop being urgent.
            "**Yes** — where 1067 is blank too",
        ],
        [
            "Source",
            "Blank title / image",
            str(len(blocking_blanks)),
            "Client (MyGS1)",
            "**Yes**",
        ],
        [
            "Copy",
            "Inferred claims to verify",
            str(by_kind[_INFERENCE]),
            "Operator/Client",
            "Review",
        ],
        [
            "Source",
            "Blank non-critical fields",
            str(len(degrade_blanks)),
            "Client (MyGS1)",
            "Degrade only",
        ],
        [
            "Source",
            "Cross-market inconsistencies",
            str(len(inconsistent)),
            "Client (MyGS1)",
            "No — but review",
        ],
        [
            "Source",
            "Possible wrong-language values",
            str(len([i for i in source_issues if i.issue == _WRONG_LANG])),
            "Client (MyGS1)",
            "Worth a glance",
        ],
        [
            "Source",
            "Values translated to fill a language gap",
            str(len([i for i in generated_issues if i.issue == _TRANSLATED])),
            "Client (MyGS1)",
            "No — §4 to paste back",
        ],
        [
            "Media",
            "Videos not yet mapped to a GTIN",
            str(len(video_map_issues)),
            "Client",
            "For those GTINs",
        ],
        [
            "Category",
            "Unmapped GPC bricks",
            str(len(category_issues)),
            "Operator",
            "—" if not category_issues else "Yes",
        ],
    ]
    body = ["| " + " | ".join(r) + " |" for r in rows]
    return [
        "## Summary",
        "",
        "| Area | Finding | Count | Owner | Blocks publish? |",
        "|---|---|--:|---|---|",
        *body,
        "",
    ]


def _matrix_lines(  # noqa: PLR0913 — a matrix needs its rows, its columns, and their sources
    products: list[ProductRecord],
    gdsn_map: dict[str, GdsnSource],
    gdsn_extras: dict[str, GdsnSource],
    languages: list[str],
    video_confirmed: dict[str, set[str]],
    names: dict[str, ProductRecord],
) -> list[str]:
    """Per-SKU coverage of every field, richest first.

    One row per in-scope product, one column per configured field plus the video pair. Sorted by
    how much is filled, so the SKUs closest to publishable sit at the top and the worst-served at
    the bottom — the order someone works in, rather than GTIN order, which carries no information.

    Mandatory columns come first and say so in the header, because a gap there stops the SKU
    while a gap in an optional column only thins the page. The two facts look identical in a
    matrix otherwise — and marking them in **bold** did not separate them either: markdown makes
    header cells bold anyway, so the marker rendered as nothing at all in the report as read. A
    group label inside the cell is the version that survives, markdown having no row above the
    header to put one in.
    """
    if not products:
        return ["## 0. Coverage matrix", "", "_No products in scope._", ""]

    columns = _columns(gdsn_map, gdsn_extras)
    rows: list[tuple[int, list[str]]] = []
    for product in products:
        gtin = product.gtin14
        cells, score = [], 0
        for column in columns:
            mark, filled = _mark(product, column, languages, video_confirmed)
            cells.append(mark)
            score += filled
        name = _name(names, gtin)[:20]
        rows.append((score, [f"`{gtin}`", name, *cells, str(score)]))

    # Descending by fill, then by GTIN so equal rows keep a stable order between runs.
    rows.sort(key=lambda r: (-r[0], r[1][0]))
    mandatory = sum(1 for c in columns if c.required)
    header = (
        ["GTIN", "Name"]
        + [f"{'MANDATORY' if c.required else 'optional'}<br>{c.header}" for c in columns]
        + ["score"]
    )
    return [
        "## 0. Coverage matrix",
        "",
        f"{_PRESENT} present · {_PARTIAL} one language only · {_ABSENT} missing. The "
        f"**MANDATORY** columns come first: a gap in any of those {mandatory} holds the whole "
        f"SKU, while the **optional** ones after them only thin the page. Sorted richest first; "
        f"`score` counts filled language-slots, so a localised field can contribute "
        f"{len(languages)}.",
        "",
        *_table(header, [cells for _, cells in rows]),
        "",
    ]


def _has_feature_benefit(products: dict[str, ProductRecord], gtin: str, language: str) -> bool:
    """Whether the feed carries attr 1067 for this unit — the fallback copy is written from.

    Recomputed from ``products.json`` rather than read off the finding, the same rule
    ``report_quality._publish_blocks`` follows for E23/E24: the report says what is true of the
    data *today*, from an export the operator may have replaced since the last run.
    """
    product = products.get(gtin.zfill(14))
    if product is None or product.description_long is None:
        return False
    return bool((product.description_long.values.get(language) or "").strip())


def _blocking_lines(
    missing_1083: list[SourceIssue], products: dict[str, ProductRecord]
) -> list[str]:
    """§1 — the units whose marketing message (attr 1083) the feed does not carry.

    This was §1c, under a §1 that also held three subsections repeating §0's matrix: E23's
    mandatory gaps (§1a), E24's missing videos (§1b), and the blank title/image findings (§1d),
    whose only GTIN beyond the matrix was out of scope entirely — a whole-catalogue finding leaking
    into a scoped report. All three are gone, and with one subsection left there is no subsection.

    **Its own text had drifted furthest.** It said *"the generator produced nothing for these
    units … then re-run generation"*, which was never what it listed: the rows come from
    ``missing_generation_input``, which fires on a blank attr 1083, and no amount of re-running
    generation fills a field the datapool does not have. Once copy is written only for the rows a
    run publishes, that sentence would have read as an accusation about every already-live unit.

    The consequence is per row rather than asserted for all of them, because it differs: a unit
    whose 1067 also has nothing is genuinely held (E21), and one whose 1067 carries copy publishes
    from it. Calling both a blocker is how a real blocker stops being read.
    """
    rows = [
        [
            _label(products, issue.gtin),
            _lang(issue.field),
            "Publishes from 1067"
            if _has_feature_benefit(products, issue.gtin, _lang(issue.field))
            else "**Held** — no 1067 either (E21)",
        ]
        for issue in sorted(missing_1083, key=lambda i: (i.gtin, i.field))
    ]
    return [
        "## 1. Blocks publish — no marketing message in the feed (attr 1083)",
        "",
        "Attr 1083 is what a page's copy is written from. These `(GTIN, language)` units have "
        "none, and no other language carries it either, so there is nothing to translate from — "
        "**this is a source finding for MyGS1, not a generator failure.** Re-running generation "
        "cannot close it.",
        "",
        "What it costs depends on attr 1067. With nothing there either, there is nothing to write "
        "copy from at all and the unit is **held out of the plan** (E21) — the SKU does not "
        "publish in that language. With 1067 present, the page publishes from it and only the "
        "datapool is short a field.",
        "",
        "_Not a list of units without copy **this run**._ Copy is written for the rows a run "
        "publishes, so a unit that is already live and unchanged has none by design and is not "
        "listed here.",
        "",
        "**Action: fill attr 1083 for the named language in MyGS1.**",
        "",
        *_table(["GTIN", "Lang", "Consequence"], rows),
        "",
    ]


def _review_lines(
    inferences: list[SourceIssue],
    generated_count: int,
    products: dict[str, ProductRecord],
    client_id: str,
) -> list[str]:
    inf_rows = [[_label(products, i.gtin), _lang(i.field), _cell(i.value)] for i in inferences]
    return [
        "## 2. Review before publish — inferred claims",
        "",
        "Claims the copy makes that go **beyond the literal feed text** (plausible, but derived). "
        "Confirm each holds for the real product before it goes live — this is the actionable "
        "slice of copy review.",
        "",
        *_table(["GTIN", "Lang", "Claim to verify"], inf_rows),
        "",
        f"_The {generated_count} generated-copy row(s) written this run are reviewed in context "
        "at the operator gate (Review Gate #1); raw text in "
        f"`output/{client_id}/data/generation_results.json`. Copy is written only for the rows a "
        "run publishes, so this counts **this run's batch**, not every unit in scope._",
        "",
    ]


def _source_lines(
    degrade_blanks: list[SourceIssue],
    inconsistent: list[SourceIssue],
    wrong_lang: list[SourceIssue],
    products: dict[str, ProductRecord],
) -> list[str]:
    blank_rows = [[_label(products, i.gtin), i.field, _cell(i.source)] for i in degrade_blanks]
    inc_rows = [[_label(products, i.gtin), i.field, _market_cell(i)] for i in inconsistent]
    lang_rows = [[_label(products, i.gtin), i.field, _cell(i.value)] for i in wrong_lang]
    return [
        "## 3. Source-data fixes in MyGS1 (do not block publish)",
        "",
        "### 3a. Blank non-critical fields",
        "",
        "These degrade the page (e.g. a missing spec line in Technische details) but don't break "
        "it, so they don't hold the GTIN. Worth filling at source.",
        "",
        *_table(["GTIN", "Field", "Source attribute"], blank_rows),
        "",
        "### 3b. Values inconsistent across markets",
        "",
        "The same field carries different text across GS1 target markets (priority "
        "`528 > 056 > 276 > 442`); the tool used the highest-ranked (marked ✓). Both/all market "
        "texts are shown so you can compare on the spot and align the authoritative one in MyGS1.",
        "",
        *_table(["GTIN", "Field", "Value per market (✓ = used)"], inc_rows),
        "",
        "### 3c. Possible wrong-language values (worth a glance)",
        "",
        "Heuristic: a localised value carrying letter patterns that belong to the *other* language "
        "(e.g. a French title still reading `Schoonmaakdoek`). Not a blocker — skim and fix the "
        "translation at source if the flag is right.",
        "",
        *_table(["GTIN", "Field", "Value (reads like the wrong language)"], lang_rows),
        "",
    ]


def _translated_lines(
    translated: list[SourceIssue], products: dict[str, ProductRecord]
) -> list[str]:
    """§4 — the values the tool rendered into a language the feed did not carry them in.

    A work queue, not a confession: each row is one paste into MyGS1, after which the next export
    carries the value for real and the tool stops writing it. So unlike §2 — which is a count and
    a pointer, because nobody acts on generated copy row by row — the text belongs in the table.

    Rows whose attribute has no per-language slot in GS1 (attr 4.012 Material) say so instead of
    naming a field, rather than sending the operator to look for one that does not exist.

    One row per filled value, undeduplicated. This used to collapse rows asking for the same paste,
    because attr 3301 was declared twice — as ``product_name`` and again as
    ``extras.functional_name`` — so one MyGS1 cell was reported as two jobs. The duplicate
    declaration is gone and ``lib.config`` now refuses one at load, which is the better place for
    it: a dedupe here would have quietly absorbed the next one instead of surfacing it.
    """
    rows = [
        [
            _label(products, issue.gtin),
            _lang(issue.field),
            _cell(issue.source),
            _cell(issue.value),
        ]
        for issue in translated
    ]
    return [
        "## 4. Translated to fill a language gap — paste these into MyGS1",
        "",
        "The feed carries each of these in another language but not in this one, so the tool "
        "**translated it** and the page shows LLM-written text where it should show the client's. "
        "Nothing here blocks publishing. Putting the value back in MyGS1 is what ends that: the "
        "next export carries it for real and the tool stops writing it.",
        "",
        "**Action: paste each value into the named attribute for that language in MyGS1.**",
        "",
        *_table(["GTIN", "Lang", "Source attribute", "Value to paste"], rows),
        "",
    ]


def _video_lines(video_map_issues: list[SourceIssue], client_id: str) -> list[str]:
    lines = [
        "## 5. Video mapping backlog",
        "",
        f"**{len(video_map_issues)}** video files have no GTIN assigned yet. Client to map each "
        f"filename → GTIN in `input/{client_id}/videos/mapping.yml` (or mark `skip`), then "
        f"`build_video_map {client_id} --check`. A GTIN only becomes publishable once it has a "
        "confirmed video in **every** language.",
        "",
    ]
    if video_map_issues:
        lines.append("<details><summary>Unassigned video files</summary>")
        lines.append("")
        lines += [f"- `{i.field}` — {_cell(i.value)}" for i in video_map_issues]
        lines.append("")
        lines.append("</details>")
        lines.append("")
    return lines


def _category_lines(category_issues: list[SourceIssue]) -> list[str]:
    body = (
        f"{len(category_issues)} unmapped GPC brick(s) — see `category_issues.json`."
        if category_issues
        else "No unmapped GPC bricks — every product resolves to a site category."
    )
    return ["## 6. Categories", "", body, ""]


def _observations_lines(observations: list[str]) -> list[str]:
    """Free-text, in-session review notes — the assistant's own 'worth a glance' flags.

    Unlike every other section (deterministic renders of the pipeline's issue files), these are
    qualitative judgements the in-session producer wrote while reviewing a run — the same
    heads-ups it would give the user in chat, captured here so they persist beyond the chat.
    """
    body = (
        [f"- {note}" for note in observations]
        if observations
        else ["_None recorded for this run._"]
    )
    return [
        "## Observations (this run)",
        "",
        "Qualitative flags the in-session assistant noted while reviewing this run — not "
        "deterministic checks. Skim and act if relevant.",
        "",
        *body,
        "",
    ]


def render_quality_report(  # noqa: PLR0913 — a document renderer needs each source plus its metadata
    *,
    client_id: str,
    source_issues: list[SourceIssue],
    generated_issues: list[SourceIssue],
    video_map_issues: list[SourceIssue],
    category_issues: list[SourceIssue],
    products: dict[str, ProductRecord],
    snapshot: str,
    freshness: dict[str, str],
    observations: list[str] | None = None,
    mandatory_gaps: dict[str, list[MandatoryGap]] | None = None,
    video_held: list[str] | None = None,
    matrix: MatrixInput | None = None,
) -> str:
    """Render the consolidated data-quality report as markdown.

    Args:
        client_id: The client the report is for (titles the document).
        source_issues: ``source_issues.json`` (blank / cross-market source-data findings).
        generated_issues: ``generated_issues.json`` (held, generated, and inferred copy).
        video_map_issues: ``video_map_issues.json`` (videos not yet mapped to a GTIN).
        category_issues: ``category_issues.json`` (unmapped GPC bricks).
        products: Parsed products keyed by GTIN-14, for human-readable names.
        snapshot: The report date (injected, so the output is deterministic).
        freshness: Last-updated date per source, keyed ``generated``/``source``/``video_map``/
            ``category`` — each source has its own producer run, so they can differ.
        observations: Free-text, in-session review notes (``observations.json``) — the
            assistant's own qualitative flags for this run. ``None``/empty renders a placeholder.
        mandatory_gaps: Missing mandatory source values per GTIN (E23), from
            :func:`lib.mandatory.missing_mandatory`. Computed by the caller rather than derived
            here, because it needs the client's ``gdsn_map`` and this renderer stays pure.
        video_held: GTINs held for want of a client-confirmed video in every language (E24).
        matrix: In-scope products plus the field definitions and video confirmations behind the
            §0 coverage matrix. ``None`` omits the section — a report for a client with no
            ``gdsn_map`` has nothing to tabulate.

    Returns:
        The full markdown document.
    """
    missing_1083 = [i for i in generated_issues if i.issue == _HELD]
    inferences = [i for i in generated_issues if i.issue == _INFERENCE]
    generated_count = sum(1 for i in generated_issues if i.issue == _GENERATED)
    translated = [i for i in generated_issues if i.issue == _TRANSLATED]
    blanks = [i for i in source_issues if i.issue == _BLANK]
    # The blocking half is counted in the Summary and shown in §0's `product·3301` / `image·2485`
    # columns; §1d used to list it a third time, and its one GTIN beyond the matrix was out of
    # scope. `_blocks_publish` still splits them, so §3a does not pick the blocking ones up.
    degrade_blanks = [i for i in blanks if not _blocks_publish(i.field)]
    inconsistent = [i for i in source_issues if i.issue == _INCONSISTENT]
    wrong_lang = [i for i in source_issues if i.issue == _WRONG_LANG]

    lines = [
        *_header_lines(client_id, snapshot, freshness),
        *_summary_lines(
            generated_issues,
            source_issues,
            video_map_issues,
            category_issues,
            mandatory_gaps or {},
            video_held or [],
        ),
        *_observations_lines(observations or []),
        *(
            _matrix_lines(
                matrix.products,
                matrix.gdsn_map,
                matrix.gdsn_extras,
                matrix.languages,
                matrix.video_confirmed,
                products,
            )
            if matrix is not None
            else []
        ),
        *_blocking_lines(missing_1083, products),
        *_review_lines(inferences, generated_count, products, client_id),
        *_source_lines(degrade_blanks, inconsistent, wrong_lang, products),
        *_translated_lines(translated, products),
        *_video_lines(video_map_issues, client_id),
        *_category_lines(category_issues),
    ]
    return "\n".join(lines)
