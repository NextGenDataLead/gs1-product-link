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

from lib.records import ProductRecord, SourceIssue

#: Issue kinds emitted by the generator merge (``generated_issues.json``).
_HELD = "missing_generation_input"
_INFERENCE = "generation_inference"
_GENERATED = "content_generated"
#: Issue kinds emitted by the export parser (``source_issues.json``).
_BLANK = "value_blank"
_INCONSISTENT = "value_inconsistent_across_markets"

_MAX_SOURCE_SNIPPET = 90


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


def _snippet(value: str) -> str:
    """Escaped, length-capped source text for the generated-copy table."""
    escaped = _cell(value)
    return escaped[:_MAX_SOURCE_SNIPPET] + ("…" if len(escaped) > _MAX_SOURCE_SNIPPET else "")


def _header_lines(client_id: str, snapshot: str, freshness: dict[str, str]) -> list[str]:
    def f(key: str) -> str:
        return freshness.get(key, "—")

    return [
        f"# {client_id.title()} — Data quality report",
        "",
        "_Consolidated worklist of everything blocking correct, complete product pages. "
        f"Snapshot {snapshot}. Source freshness: generated `{f('generated')}`, "
        f"source `{f('source')}`, video-map `{f('video_map')}`, categories `{f('category')}`._",
        "",
        f"> Regenerate the underlying data: `run_plan {client_id}` (generated + categories), "
        f"`parse_export {client_id}` (source), `build_video_map {client_id} --check` (video-map); "
        f"then `python -m scripts.report_quality {client_id}`.",
        "",
    ]


def _summary_lines(
    generated_issues: list[SourceIssue],
    source_issues: list[SourceIssue],
    video_map_issues: list[SourceIssue],
    category_issues: list[SourceIssue],
) -> list[str]:
    by_kind = Counter(i.issue for i in generated_issues)
    held = {i.gtin for i in generated_issues if i.issue == _HELD}
    blanks = [i for i in source_issues if i.issue == _BLANK]
    inconsistent = [i for i in source_issues if i.issue == _INCONSISTENT]
    rows = [
        [
            "Copy",
            "Held — no marketing message (1083)",
            f"{by_kind[_HELD]} rows / {len(held)} GTINs",
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
        ["Copy", "Generated copy to review", str(by_kind[_GENERATED]), "Operator/Client", "Review"],
        [
            "Source",
            "Blank required fields",
            str(len(blanks)),
            "Client (MyGS1)",
            "If in a target GTIN",
        ],
        [
            "Source",
            "Cross-market inconsistencies",
            str(len(inconsistent)),
            "Client (MyGS1)",
            "No (cosmetic)",
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


def _held_lines(held: list[str], products: dict[str, ProductRecord]) -> list[str]:
    rows = [[_label(products, gtin), "Fill attr 1083 in nl + fr"] for gtin in held]
    return [
        "## 1. Held — no marketing copy (BLOCKS publish)",
        "",
        "These GTINs have a blank marketing message (attr **1083**) and no feature bullets "
        "(**1067**), so there is nothing to write honest copy from. They stay in the pilot "
        "allowlist (so they keep showing up here) but are **excluded from publishing** until copy "
        "exists. **Action: client fills attr 1083 (nl + fr) in MyGS1**, then re-run generation.",
        "",
        *_table(["GTIN", "Fix"], rows),
        "",
    ]


def _review_lines(
    inferences: list[SourceIssue],
    generated: list[SourceIssue],
    products: dict[str, ProductRecord],
    client_id: str,
) -> list[str]:
    inf_rows = [[_label(products, i.gtin), _lang(i.field), _cell(i.value)] for i in inferences]
    gen_rows = [[_label(products, i.gtin), _lang(i.field), _snippet(i.value)] for i in generated]
    return [
        "## 2. Review before publish",
        "",
        "### 2a. Inferred claims — verify each is true",
        "",
        "Claims the copy makes that go **beyond the literal feed text** (plausible, but derived). "
        "Confirm each holds for the real product before it goes live.",
        "",
        *_table(["GTIN", "Lang", "Claim to verify"], inf_rows),
        "",
        "### 2b. Generated copy — review against source",
        "",
        "Every tagline + Eigenschappen block was LLM-written and should be reviewed. The "
        "*Source (1083)* column is the marketing text it was written from. Full copy: "
        f"`output/{client_id}/data/generated_cache.json`.",
        "",
        *_table(["GTIN", "Lang", "Source (1083) it was written from"], gen_rows),
        "",
    ]


def _source_lines(
    blanks: list[SourceIssue],
    inconsistent: list[SourceIssue],
    products: dict[str, ProductRecord],
) -> list[str]:
    blank_rows = [[_label(products, i.gtin), i.field, _cell(i.source)] for i in blanks]
    inc_rows = [[_label(products, i.gtin), i.field, _cell(i.value)] for i in inconsistent]
    return [
        "## 3. Source-data fixes in MyGS1",
        "",
        "### 3a. Blank required fields",
        "",
        *_table(["GTIN", "Field", "Source attribute"], blank_rows),
        "",
        "### 3b. Values inconsistent across markets (nl vs fr)",
        "",
        "Cosmetic — the visible page headline is the tagline, not this field — but worth aligning "
        "in MyGS1.",
        "",
        *_table(["GTIN", "Field", "Current value"], inc_rows),
        "",
    ]


def _video_lines(video_map_issues: list[SourceIssue], client_id: str) -> list[str]:
    lines = [
        "## 4. Video mapping backlog",
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
    return ["## 5. Categories", "", body, ""]


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

    Returns:
        The full markdown document.
    """
    held = sorted({i.gtin for i in generated_issues if i.issue == _HELD})
    inferences = [i for i in generated_issues if i.issue == _INFERENCE]
    generated = sorted(
        (i for i in generated_issues if i.issue == _GENERATED), key=lambda i: (i.gtin, i.field)
    )
    blanks = [i for i in source_issues if i.issue == _BLANK]
    inconsistent = [i for i in source_issues if i.issue == _INCONSISTENT]

    lines = [
        *_header_lines(client_id, snapshot, freshness),
        *_summary_lines(generated_issues, source_issues, video_map_issues, category_issues),
        *_held_lines(held, products),
        *_review_lines(inferences, generated, products, client_id),
        *_source_lines(blanks, inconsistent, products),
        *_video_lines(video_map_issues, client_id),
        *_category_lines(category_issues),
    ]
    return "\n".join(lines)
