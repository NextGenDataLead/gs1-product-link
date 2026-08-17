"""Unit tests for lib/quality_report.py — the pure consolidated-report renderer.

Pure and deterministic: given already-parsed issue lists + products, it returns markdown.
No I/O, no clock (the snapshot date is injected), so the same inputs render byte-identical.
"""

from __future__ import annotations

from lib.gdsn import GdsnSource
from lib.mandatory import MandatoryGap
from lib.quality_report import MatrixInput, render_quality_report
from lib.records import LocalisedText, ProductRecord, SourceIssue

_FRESH = {
    "generated": "2026-07-26",
    "source": "2026-07-19",
    "video_map": "2026-07-20",
    "category": "2026-07-26",
}


def _issue(  # noqa: PLR0913 — a test factory mirroring every SourceIssue field
    gtin: str,
    issue: str,
    *,
    field: str = "f",
    value: str = "",
    source: str = "",
    market_values: tuple[tuple[str, str], ...] = (),
) -> SourceIssue:
    return SourceIssue(
        gtin=gtin,
        field=field,
        source=source,
        issue=issue,
        value=value,
        detail="d",
        market_values=market_values,
    )


def _products(*gtins: str) -> dict[str, ProductRecord]:
    out: dict[str, ProductRecord] = {}
    for g in gtins:
        p = ProductRecord(
            gtin=g, brand="Noviplast", product_name=LocalisedText(values={"nl": f"prod-{g[-4:]}"})
        )
        out[p.gtin14] = p
    return out


def _render(**over: object) -> str:
    base: dict[str, object] = {
        "client_id": "noviplast",
        "source_issues": [],
        "generated_issues": [],
        "video_map_issues": [],
        "category_issues": [],
        "products": {},
        "snapshot": "2026-07-27",
        "freshness": _FRESH,
    }
    base.update(over)
    return render_quality_report(**base)  # type: ignore[arg-type]


def test_the_header_leads_with_when_the_report_was_written() -> None:
    """On its own line, near the top — the reader should not have to check a file browser."""
    md = _render(snapshot="2026-08-13 22:02 CEST")

    lines = md.splitlines()
    assert "**Generated 2026-08-13 22:02 CEST**" in lines
    # Prominent, not buried: within the first handful of lines, under the title.
    assert lines.index("**Generated 2026-08-13 22:02 CEST**") < 4


def test_the_header_names_the_oldest_input_not_only_the_generation_time() -> None:
    """A report can be minutes old and still describe a month-old export — say which."""
    md = _render(snapshot="2026-08-13 22:02 CEST")

    assert "The oldest input is from 2026-07-19" in md  # the `source` entry in _FRESH


def test_a_missing_freshness_entry_does_not_become_an_ancient_date() -> None:
    """An unknown date is unknown; treating it as old would put a false scare in the header."""
    md = _render(snapshot="2026-08-13 22:02 CEST", freshness={})

    assert "oldest input" not in md
    assert "**Generated 2026-08-13 22:02 CEST**" in md


def test_held_gtins_block_publish() -> None:
    gen = [
        _issue("08713195003276", "missing_generation_input", field="description_short.nl"),
        _issue("08713195003276", "missing_generation_input", field="description_short.fr"),
    ]
    md = _render(generated_issues=gen, products=_products("08713195003276"))

    assert "Held" in md
    assert "08713195003276" in md
    assert "prod-3276" in md  # product name resolved
    assert "BLOCKS" in md.upper()


def test_inferences_are_listed_for_verification() -> None:
    gen = [
        _issue(
            "08713195007915",
            "generation_inference",
            field="generated_description.nl",
            value="Bevestig hem op elke metalen ondergrond",
        )
    ]
    md = _render(generated_issues=gen, products=_products("08713195007915"))

    assert "Bevestig hem op elke metalen ondergrond" in md


def test_blank_title_is_a_blocker_not_a_source_fix() -> None:
    """R-c removed §1d, which listed these. What must survive it is pinned here.

    §1d said what §0's `product·3301` column and the Summary row already said, and named one GTIN
    neither did — an out-of-scope one, which is a whole-catalogue finding leaking into a scoped
    report. Dropping the section must not quietly demote the finding to §3a's "does not block"
    list, which is the one way this change could go wrong.
    """
    src = [
        _issue(
            "08713195007649",
            "value_blank",
            field="product_name.fr",
            source="TradeItemDescription attr 3301",
        )
    ]
    md = _render(source_issues=src, products=_products("08713195007649"))

    assert "Blank title / image" in md  # summary row, still marked a blocker
    _, _, source_fixes = md.partition("## 3.")
    assert "08713195007649" not in source_fixes  # never demoted to "do not block publish"


def test_blank_net_content_is_degrade_only() -> None:
    src = [
        _issue(
            "08713195000794",
            "value_blank",
            field="net_content",
            source="TradeItemMeasurements attr 3510",
        )
    ]
    md = _render(source_issues=src, products=_products("08713195000794"))

    # net_content degrades but does not block: it belongs under source fixes, not section 1.
    section_1, _, after = md.partition("## 2.")
    assert "08713195000794" not in section_1
    assert "08713195000794" in after
    assert "Blank non-critical fields" in md


def test_cross_market_values_shown_side_by_side() -> None:
    src = [
        _issue(
            "08713195007496",
            "value_inconsistent_across_markets",
            field="product_name.fr",
            value="Désherbant",
            market_values=(("528", "Désherbant"), ("056", "Desherbant")),
        )
    ]
    md = _render(source_issues=src, products=_products("08713195007496"))

    # both conflicting market texts are visible, with the chosen (highest-ranked) marked
    assert "528=`Désherbant` ✓" in md
    assert "056=`Desherbant`" in md


def test_wrong_language_values_are_listed() -> None:
    src = [
        _issue(
            "08713195000527",
            "value_wrong_language",
            field="product_name.fr",
            value="Schoonmaakdoek",
        )
    ]
    md = _render(source_issues=src, products=_products("08713195000527"))

    assert "Possible wrong-language values" in md  # summary + §3c heading
    assert "3c." in md
    assert "Schoonmaakdoek" in md


def test_summary_counts_each_area() -> None:
    md = _render(
        generated_issues=[
            _issue("08713195000001", "content_generated"),
            _issue("08713195000002", "missing_generation_input"),
            _issue("08713195000003", "generation_inference", value="x"),
        ],
        source_issues=[_issue("08713195000004", "value_blank")],
        video_map_issues=[_issue("", "video_unconfirmed", value="A.mpg")],
    )

    assert "## Summary" in md
    assert "Videos not yet mapped to a GTIN" in md  # the media row exists
    # counts come from the passed data: one blank + one video issue each render a count of 1
    assert "| 1 |" in md


def test_categories_clean_line_when_no_issues() -> None:
    assert "No unmapped GPC bricks" in _render()


# --- §4 translated values ------------------------------------------------------


def _translated(gtin: str, field: str, value: str, source: str, detail: str = "d") -> SourceIssue:
    return SourceIssue(
        gtin=gtin,
        field=field,
        source=source,
        issue="value_translated",
        value=value,
        detail=detail,
    )


def test_translated_values_are_listed_with_the_text_to_paste() -> None:
    """The value is the deliverable here, not evidence for a count.

    §2 deliberately became a pointer rather than a per-row dump, because nobody acts on generated
    copy row by row. This section is the opposite: each row is one paste into MyGS1, so the text
    has to be in the table.
    """
    gen = [
        _translated(
            "08713195000001",
            "product_name.fr",
            "Pic d'arrosage",
            "TradeItemDescription attr 3301",
        )
    ]
    md = _render(generated_issues=gen, products=_products("08713195000001"))

    assert "## 4. Translated to fill a language gap" in md
    assert "Pic d'arrosage" in md
    assert "TradeItemDescription attr 3301" in md
    assert "fr" in md


def test_every_filled_value_is_one_row_and_the_summary_says_the_same_number() -> None:
    """One row per filled value, and one number above it that agrees.

    The count and the table are assembled in different places, so they can drift — and they once
    did, for a reason since removed: attr 3301 was declared twice, as `product_name` and again as
    `extras.functional_name`, so one MyGS1 paste appeared as two rows. That was deduplicated here.
    The duplicate declaration is gone and `lib.config` refuses one at load, which leaves this test
    holding the half that still matters: a summary saying 2 above a table of 1 is the kind of
    small contradiction that makes a reader stop trusting the whole document.
    """
    same = "TradeItemDescription attr 3301"
    md = _render(
        generated_issues=[
            _translated("08713195007649", "product_name.fr", "câble magnétique", same),
            _translated("08713195007649", "product_name.de", "Magnetkabel", same),
        ],
        products=_products("08713195007649"),
    )

    section = md.partition("## 4.")[2].partition("## 5.")[0]
    assert "câble magnétique" in section
    assert "Magnetkabel" in section
    rows = [line for line in section.splitlines() if line.startswith("| `0871")]
    row = next(line for line in md.splitlines() if "Values translated" in line)
    assert f"| {len(rows)} |" in row
    assert len(rows) == 2


def test_a_translated_value_lands_after_the_other_mygs1_fixes_not_among_the_blockers() -> None:
    # It is MyGS1 work that does not hold the GTIN — §3's neighbourhood, not §1's.
    md = _render(
        generated_issues=[
            _translated(
                "08713195000001", "product_name.fr", "Pic", "TradeItemDescription attr 3301"
            )
        ],
        products=_products("08713195000001"),
    )

    before, _, after = md.partition("## 3.")
    assert "Translated to fill a language gap" in after
    assert "Translated to fill a language gap" not in before


def test_the_video_and_category_sections_move_down_to_make_room() -> None:
    md = _render()

    assert "## 5. Video mapping backlog" in md
    assert "## 6. Categories" in md


def test_an_empty_translation_section_says_none_rather_than_a_headerless_table() -> None:
    md = _render()

    section = md.partition("## 4.")[2]
    assert "_None._" in section


def test_the_summary_counts_translated_values_as_non_blocking_mygs1_work() -> None:
    md = _render(
        generated_issues=[
            _translated(
                "08713195000001", "product_name.fr", "Pic", "TradeItemDescription attr 3301"
            )
        ],
        products=_products("08713195000001"),
    )

    row = next(line for line in md.splitlines() if "Values translated" in line)
    assert "MyGS1" in row
    assert "no" in row.lower()


def test_observations_section_renders_notes() -> None:
    md = _render(
        observations=[
            "…0527's French title reads Dutch (Schoonmaakdoek).",
            "…7496 resolved only after a brief propagation lag — re-check.",
        ]
    )
    assert "## Observations (this run)" in md
    assert "Schoonmaakdoek" in md
    assert "propagation lag" in md


def test_observations_section_placeholder_when_empty() -> None:
    md = _render()
    assert "## Observations (this run)" in md
    assert "None recorded for this run" in md


def test_render_is_deterministic() -> None:
    args = {
        "generated_issues": [_issue("08713195000001", "content_generated", value="src")],
        "products": _products("08713195000001"),
    }
    assert _render(**args) == _render(**args)


def test_pipe_in_value_is_escaped_for_markdown_table() -> None:
    gen = [
        _issue(
            "08713195000001",
            "generation_inference",
            field="generated_description.nl",
            value="a | b",
        )
    ]
    md = _render(generated_issues=gen, products=_products("08713195000001"))
    # the raw pipe must be escaped so it does not break the table column
    assert "a \\| b" in md


def test_generated_copy_is_a_pointer_not_a_per_row_dump() -> None:
    # 2b used to list every generated row; now only a count + pointer, no per-row source text.
    gen = [
        _issue(
            "08713195000001", "content_generated", field="generated_description.nl", value="src"
        ),
        _issue(
            "08713195000002", "content_generated", field="generated_description.fr", value="txt"
        ),
    ]
    md = _render(generated_issues=gen, products=_products("08713195000001", "08713195000002"))

    assert "2 generated-copy row(s) written this run are reviewed" in md  # count-based pointer
    assert "generation_results.json" in md
    assert "src" not in md and "txt" not in md  # no per-row source dump
    assert "2b." not in md  # the old subsection is gone


# --- §0 coverage matrix -------------------------------------------------------


def _matrix(**over: object) -> MatrixInput:
    base: dict[str, object] = {
        "products": [],
        "gdsn_map": {
            "product_name": GdsnSource(sheet="S", attribute="3301", localised=True, required=True),
            "brand": GdsnSource(sheet="S", attribute="3336", required=True),
            "net_content": GdsnSource(sheet="S", attribute="3510"),
        },
        "gdsn_extras": {"material": GdsnSource(sheet="S", attribute="Material")},
        "languages": ["nl", "fr"],
        "video_confirmed": {"nl": set(), "fr": set()},
    }
    base.update(over)
    return MatrixInput(**base)  # type: ignore[arg-type]


def _p(gtin: str, **over: object) -> ProductRecord:
    base: dict[str, object] = {
        "gtin": gtin,
        "brand": "Noviplast",
        "product_name": LocalisedText(values={"nl": "a", "fr": "b"}),
    }
    base.update(over)
    return ProductRecord.model_validate(base)


def test_matrix_sorts_richest_first() -> None:
    """The order someone works in — GTIN order carries no information."""
    thin = _p("08713195000001", product_name=LocalisedText(values={"nl": "a"}))
    rich = _p("08713195000002", net_content="10 cm", extras={"material": "PP"})
    md = _render(matrix=_matrix(products=[thin, rich]))

    body = [line for line in md.splitlines() if "| `0871" in line]
    assert body[0].startswith("| 1 | `08713195000002`")  # richer first


def _headers(md: str) -> list[str]:
    """The §0 header cells, in rendered order."""
    header = next(line for line in md.splitlines() if line.startswith("| # |"))
    return [c.strip() for c in header.split("|")[1:-1]]


def _cell_for(md: str, gtin: str, header: str) -> str:
    """The cell under ``header`` for one product, found by name rather than by position.

    Every lookup here used to count backwards from the end of the row, which made a column
    reorder break tests that have nothing to do with ordering — twice now. The header row is
    already the index; use it.
    """
    idx = next(i for i, h in enumerate(_headers(md)) if h.removesuffix(" (optional)") == header)
    row = next(line for line in md.splitlines() if f"| `{gtin}`" in line)
    return row.split("|")[1:-1][idx].strip()


def test_the_optional_columns_say_optional_in_words() -> None:
    """Four goes at this, and the first three were all too clever.

    `**…**` was invisible (markdown makes header cells bold anyway); `MANDATORY<br>…` rendered
    correctly and showed a literal HTML tag to anyone reading the markdown as text; a trailing
    `~` was plain enough but meant nothing without the legend. The word is the version a reader
    does not have to decode.
    """
    md = _render(matrix=_matrix(products=[_p("08713195000001")]))

    assert "material (optional)" in _headers(md)
    assert "product·3301" in _headers(md)  # mandatory: unmarked
    assert not [h for h in _headers(md) if "**" in h or "<" in h or "~" in h]


def test_the_header_makes_exactly_one_crossing_from_mandatory_to_optional() -> None:
    """Mandatory columns first, optional after, and the boundary crossed once.

    A regression pin, not a fix: `_columns` already emits them in that order. It is worth pinning
    because the split is derived from `required` / `required_group` in the **gitignored**
    `clients.yml`, so a field flipping its flag re-groups the header with no code change — and
    nothing in git can see the config that did it. Interleaved groups would make the mark noise:
    the legend says "everything up to X", which is only true of a contiguous run.
    """
    md = _render(matrix=_matrix(products=[_p("08713195000001")]))
    fields = [h for h in _headers(md) if h not in {"#", "GTIN", "Name", "score"}]
    optional = [h.endswith(" (optional)") for h in fields]

    assert set(optional) == {True, False}
    crossings = [(a, b) for a, b in zip(optional, optional[1:], strict=False) if a != b]
    assert crossings == [(False, True)]  # mandatory → optional, once


def test_score_sits_on_the_boundary_between_the_two_groups() -> None:
    """`score` is the divider, which is why the groups need no marker between them.

    Placed after the last mandatory column and before the first optional one, so what it counts
    is legible from where it sits rather than from the legend.
    """
    md = _render(matrix=_matrix(products=[_p("08713195000001")]))
    cols = _headers(md)
    at = cols.index("score")

    assert not cols[at - 1].endswith(" (optional)")  # last mandatory column
    assert cols[at + 1].endswith(" (optional)")  # first optional one
    assert all(h.endswith(" (optional)") for h in cols[at + 1 :])


def test_score_counts_the_mandatory_columns_only() -> None:
    """The score answers "how close is this SKU to publishable", so optional fill cannot raise it.

    It used to count every column, which let a product with both optional values and a missing
    mandatory one outrank a publishable one — and the table is *sorted* by it, so that put the
    wrong SKU at the top of the worklist.
    """
    gtin = "08713195000001"
    md = _render(
        matrix=_matrix(
            products=[_p(gtin, net_content="10 cm", extras={"material": "PP"})],
            video_confirmed={"nl": {gtin}, "fr": {gtin}},
        )
    )

    # product·3301 (2 languages) + brand (1) + video (2) = 5; net_content and material are
    # optional and add nothing, though both are present.
    assert _cell_for(md, gtin, "net·3510") == "●"
    assert _cell_for(md, gtin, "material") == "●"
    assert _cell_for(md, gtin, "score") == "5"


def test_a_thin_sku_with_optional_extras_ranks_below_a_publishable_one() -> None:
    """The consequence of the rule above, at the level the operator actually reads: row order."""
    publishable = _p("08713195000001")  # both languages, no optional values
    padded = _p(  # one language, but every optional column filled
        "08713195000002",
        product_name=LocalisedText(values={"nl": "a"}),
        net_content="10 cm",
        extras={"material": "PP"},
    )
    md = _render(matrix=_matrix(products=[padded, publishable]))

    body = [line for line in md.splitlines() if "| `0871" in line]
    assert body[0].startswith("| 1 | `08713195000001`")


def test_the_legend_explains_the_boundary_score_marks() -> None:
    """The legend and the header are one fact, so they must not be able to disagree."""
    md = _render(matrix=_matrix(products=[_p("08713195000001")]))
    legend = next(line for line in md.splitlines() if "present ·" in line)

    assert "before `score` are mandatory" in legend
    assert "after `score`" in legend
    assert "Bold columns" not in legend  # it pointed at a marker that rendered as nothing


def test_the_context_line_says_how_many_skus_the_table_holds() -> None:
    """The first question asked of a worklist is how big it is, and it was answered nowhere."""
    md = _render(matrix=_matrix(products=[_p(f"0871319500000{n}") for n in range(1, 4)]))

    assert "**3 SKUs in scope**" in md
    assert len([line for line in md.splitlines() if "| `0871" in line]) == 3


def test_rows_are_numbered_in_the_order_they_are_shown() -> None:
    """A counter to refer to a row by, following the sort rather than the GTIN."""
    thin = _p("08713195000001", product_name=LocalisedText(values={"nl": "a"}))
    rich = _p("08713195000002", net_content="10 cm", extras={"material": "PP"})
    md = _render(matrix=_matrix(products=[thin, rich]))

    body = [line for line in md.splitlines() if "| `0871" in line]
    assert [line.split("|")[1].strip() for line in body] == ["1", "2"]
    assert body[0].startswith("| 1 | `08713195000002`")  # richest first, numbered from the top


def test_a_required_extra_joins_the_mandatory_run() -> None:
    """`required` on a `gdsn_extras` entry means what it means on a mapped field.

    It could not before: every extra was hard-coded optional here, so a client could mark one
    required and watch the matrix ignore it — the shape #96's `multivalue` flag had.
    """
    md = _render(
        matrix=_matrix(
            products=[_p("08713195000001")],
            gdsn_extras={
                "dim_height": GdsnSource(sheet="S", attribute="3498", required=True),
                "material": GdsnSource(sheet="S", attribute="Material"),
            },
        )
    )
    fields = [h for h in _headers(md) if h not in {"#", "GTIN", "Name", "score"}]

    assert "dim·height" in fields  # unmarked: mandatory
    assert "material (optional)" in fields
    assert fields.index("dim·height") < fields.index("material (optional)")


#: The live either-or: Noviplast's copy comes from attr 1083 *or* attr 1067, never both required.
_GROUP_MAP = {
    "description_short": GdsnSource(
        sheet="S", attribute="1083", localised=True, required_group="marketing_copy"
    ),
    "description_long": GdsnSource(
        sheet="S", attribute="1067", localised=True, required_group="marketing_copy"
    ),
    "net_content": GdsnSource(sheet="S", attribute="3510"),
}


def _grouped(gtin: str, **over: object) -> str:
    return _render(matrix=_matrix(products=[_p(gtin, **over)], gdsn_map=_GROUP_MAP))


def test_a_required_group_is_one_column_named_after_the_group() -> None:
    """Two columns for one requirement said something false about both of them.

    `description_short` (1083) and `description_long` (1067) are a `required_group`: a SKU is held
    only when **both** are blank. Rendered as two mandatory columns, the legend's "a gap here
    holds the whole SKU" was untrue of either — and on the real report 26 of 37 in-scope SKUs
    publish with 1067 empty, each showing a ○ that means nothing on its own.
    """
    md = _grouped("08713195000001")
    fields = [h for h in _headers(md) if h not in {"#", "GTIN", "Name", "score"}]

    assert "marketing·copy" in fields
    assert not [h for h in fields if "1083" in h or "1067" in h]
    assert fields.index("marketing·copy") < fields.index("net·3510 (optional)")


def test_either_member_satisfies_the_group() -> None:
    """Which one the feed carries is not the matrix's question — whether the SKU is held is."""
    both_langs = LocalisedText(values={"nl": "a", "fr": "b"})
    short_only = _grouped("08713195000001", description_short=both_langs)
    long_only = _grouped("08713195000001", description_long=both_langs)
    neither = _grouped("08713195000001")

    assert _cell_for(short_only, "08713195000001", "marketing·copy") == "●"
    assert _cell_for(long_only, "08713195000001", "marketing·copy") == "●"
    assert _cell_for(neither, "08713195000001", "marketing·copy") == "○"


def test_the_group_is_satisfied_per_language_across_its_members() -> None:
    """One member in nl and the other in fr satisfies the requirement in both languages.

    The case that makes this a union per language rather than the best member's mark: taken
    member-wise, each is half-filled, and the SKU would read as ◐ while nothing about it is
    missing. E23 asks the same question the same way — a group is satisfied in a language when
    **any** member carries a value in it.
    """
    md = _grouped(
        "08713195000001",
        description_short=LocalisedText(values={"nl": "a"}),
        description_long=LocalisedText(values={"fr": "b"}),
    )

    assert _cell_for(md, "08713195000001", "marketing·copy") == "●"


def test_the_group_scores_once_however_many_members_carry_a_value() -> None:
    """Satisfying it twice is not more publishable than satisfying it once.

    It used to score both members, so a SKU with 1083 *and* 1067 outranked one with 1083 alone by
    two slots — on the real report that was rows 1 and 2, ranked apart by a field neither needed.
    """
    both = _grouped(
        "08713195000001",
        description_short=LocalisedText(values={"nl": "a", "fr": "b"}),
        description_long=LocalisedText(values={"nl": "c", "fr": "d"}),
    )
    one = _grouped("08713195000001", description_short=LocalisedText(values={"nl": "a", "fr": "b"}))

    # The absolute value, not just that the two agree: a multiplier applied to *both* would keep
    # them equal while still inflating every group in the table. `_GROUP_MAP`'s only mandatory
    # columns are marketing·copy (satisfied in 2 languages) and video (confirmed in none).
    assert _cell_for(one, "08713195000001", "score") == "2"
    assert _cell_for(both, "08713195000001", "score") == "2"


def test_a_group_member_declared_as_an_extra_still_satisfies_it() -> None:
    """`missing_mandatory` groups across both maps, so the matrix has to as well.

    Reading `gdsn_map` alone would drop the extra from the column's members and show ○ for a SKU
    the plan publishes — the matrix and the hold disagreeing, which is the whole failure this
    column exists to stop.
    """
    md = _render(
        matrix=_matrix(
            products=[_p("08713195000001", extras={"fallback_copy": "een korte tekst"})],
            gdsn_map={
                "description_short": GdsnSource(
                    sheet="S", attribute="1083", required_group="marketing_copy"
                )
            },
            gdsn_extras={
                "fallback_copy": GdsnSource(
                    sheet="S", attribute="1067", required_group="marketing_copy"
                )
            },
        )
    )

    assert "marketing·copy" in _headers(md)
    assert _cell_for(md, "08713195000001", "marketing·copy") == "●"


def test_a_group_keeps_its_column_when_only_one_member_asked_for_one() -> None:
    """`in_matrix: false` says "this value needs no column", not "this value does not exist".

    The requirement still holds SKUs, so it still needs a column — and the hidden member still
    counts towards satisfying it, or the column would report a gap that E23 does not see.
    """
    md = _render(
        matrix=_matrix(
            products=[_p("08713195000001", extras={"hidden_copy": "een korte tekst"})],
            gdsn_map={
                "description_short": GdsnSource(
                    sheet="S", attribute="1083", required_group="marketing_copy"
                )
            },
            gdsn_extras={
                "hidden_copy": GdsnSource(
                    sheet="S",
                    attribute="1067",
                    required_group="marketing_copy",
                    in_matrix=False,
                )
            },
        )
    )

    assert "marketing·copy" in _headers(md)
    assert "hidden·copy" not in _headers(md)  # no column of its own
    assert _cell_for(md, "08713195000001", "marketing·copy") == "●"  # but it satisfies the group


def test_the_legend_says_the_group_is_an_either_or() -> None:
    """A collapsed column has to say what satisfies it, or it reads as one more mandatory field."""
    legend = next(line for line in _grouped("08713195000001").splitlines() if "present ·" in line)

    assert "`marketing·copy`" in legend
    assert "1083" in legend and "1067" in legend


def test_video_sits_with_the_mandatory_columns_not_after_the_optional_ones() -> None:
    """A missing confirmed video holds the whole SKU (E24), so it belongs in the mandatory run.

    It used to render last, between `material` and `score`, which put a third group after the
    optional block and made "mandatory first" untrue of the table as a whole.
    """
    md = _render(matrix=_matrix(products=[_p("08713195000001")]))
    cols = _headers(md)

    assert "video" in cols  # unmarked: mandatory
    assert cols.index("video") < cols.index("material (optional)")
    assert cols.index("video") < cols.index("score")  # video counts towards it
    assert cols[0] == "#"


def test_grouping_the_header_leaves_every_cell_where_it_was() -> None:
    """`_columns` is what `_mark` and the score iterate, so a header change can reach the cells.

    Pinned against a product with one of each mark — full, half, missing, plus a video confirmed
    in one language only — so a reorder that dropped or double-counted a column shows up here as
    a wrong score rather than as a silently different table.
    """
    gtin = "08713195000001"
    md = _render(
        matrix=_matrix(
            products=[
                _p(
                    gtin,
                    product_name=LocalisedText(values={"nl": "a"}),
                    net_content="10 cm",
                    extras={"material": "PP"},
                )
            ],
            video_confirmed={"nl": {gtin}, "fr": set()},
        )
    )

    row = next(line for line in md.splitlines() if f"| `{gtin}`" in line)
    # mandatory: product·3301 ◐ (nl only) · brand ● · video ◐ = 3 | optional: net ● · material ●
    assert row == f"| 1 | `{gtin}` |  | ◐ | ● | ◐ | 3 | ● | ● |"


def test_a_localised_field_in_one_language_is_a_half_mark() -> None:
    md = _render(
        matrix=_matrix(
            products=[_p("08713195000001", product_name=LocalisedText(values={"nl": "a"}))]
        )
    )

    row = next(line for line in md.splitlines() if "| `08713195000001`" in line)
    assert "◐" in row


#: A `gdsn_extras` entry whose source attribute carries a LanguageCode/Value pair.
_LOCALISED_EXTRA = {"functional_name": GdsnSource(sheet="S", attribute="3301", localised=True)}


def _extra_cell(md: str, gtin: str) -> str:
    return _cell_for(md, gtin, "functional·name")


def test_a_localised_extra_is_counted_per_language() -> None:
    """Now the parser keeps every language of a localised extra, the matrix can count them.

    It could not before: the value collapsed to one flat string, so a per-language reading
    found nothing and reported every extra missing.
    """
    md = _render(
        matrix=_matrix(
            products=[
                _p(
                    "08713195000001",
                    extras_localised={
                        "functional_name": LocalisedText(values={"nl": "haak", "fr": "crochet"})
                    },
                ),
                _p(
                    "08713195000002",
                    extras_localised={"functional_name": LocalisedText(values={"nl": "haak"})},
                ),
            ],
            gdsn_extras=_LOCALISED_EXTRA,
        )
    )

    assert _extra_cell(md, "08713195000001") == "●"
    assert _extra_cell(md, "08713195000002") == "◐"


def test_a_flat_extra_from_an_older_parse_is_still_one_slot() -> None:
    """A products.json written before extras were per-language must not read as all-missing.

    Wrong in the direction that invents work for the client — the fix is to re-parse, not to
    open a translation queue against a file that simply predates the field.
    """
    md = _render(
        matrix=_matrix(
            products=[_p("08713195000001", extras={"functional_name": "haak"})],
            gdsn_extras=_LOCALISED_EXTRA,
        )
    )

    assert _extra_cell(md, "08713195000001") == "●"


def test_a_language_agnostic_extra_is_one_flat_slot() -> None:
    # material has no LanguageCode pair in the feed, so one value fills its only slot.
    md = _render(matrix=_matrix(products=[_p("08713195000001", extras={"material": "PP"})]))

    assert _cell_for(md, "08713195000001", "material") == "●"


def test_the_video_column_reflects_both_languages() -> None:
    gtin = "08713195000001"
    both = _render(
        matrix=_matrix(products=[_p(gtin)], video_confirmed={"nl": {gtin}, "fr": {gtin}})
    )
    one = _render(matrix=_matrix(products=[_p(gtin)], video_confirmed={"nl": {gtin}, "fr": set()}))
    neither = _render(matrix=_matrix(products=[_p(gtin)]))

    assert _cell_for(both, gtin, "video") == "●"
    assert _cell_for(one, gtin, "video") == "◐"
    assert _cell_for(neither, gtin, "video") == "○"


def test_no_matrix_input_omits_the_section() -> None:
    assert "Coverage matrix" not in _render()


def test_an_empty_scope_says_so_rather_than_rendering_an_empty_table() -> None:
    md = _render(matrix=_matrix(products=[]))

    assert "Coverage matrix" in md
    assert "No products in scope" in md


def test_a_field_marked_out_of_the_matrix_gets_no_column() -> None:
    """A column present for every product that feeds nothing is noise in a gaps table.

    `logistics_name` (3297) and `marketing_name` (3318) are pure pass-through: carried verbatim,
    consumed by nothing, so they read present on every SKU and never indicate work.
    """
    md = _render(
        matrix=_matrix(
            products=[_p("08713195000001", extras={"logistics_name": "x", "material": "PP"})],
            gdsn_extras={
                "logistics_name": GdsnSource(sheet="S", attribute="3297", in_matrix=False),
                "material": GdsnSource(sheet="S", attribute="Material"),
            },
        )
    )

    header = next(line for line in md.splitlines() if line.startswith("| # |"))
    assert "logistics" not in header
    assert "material" in header  # the neighbouring optional column is untouched


# --- §1: one section, saying what it lists (R-b, R-c, and the §1c rewrite) ----
#
# §1 used to hold four subsections. §1a (E23) and §1b (E24) repeated what §0's matrix already
# shows; §1d repeated the matrix's `product·3301` / `image·2485` columns and leaked a
# whole-catalogue GTIN into a scoped report. What is left is the copy block, which had drifted
# furthest of all: it said "the generator produced nothing for these units", while what it lists
# is products whose attr 1083 is blank in a language.


def _blank_1083(gtin: str, language: str = "nl") -> SourceIssue:
    return _issue(gtin, "missing_generation_input", field=f"description_short.{language}")


def test_the_redundant_subsections_are_gone() -> None:
    md = _render(
        generated_issues=[_blank_1083("08713195000001")],
        mandatory_gaps={"08713195000002": [MandatoryGap("brand", "", "3336")]},
        video_held=["08713195000003"],
        source_issues=[_issue("08713195000004", "blank_value", field="image_url")],
        products=_products(*[f"0871319500000{n}" for n in range(1, 5)]),
    )

    assert "### 1a." not in md
    assert "### 1b." not in md
    assert "### 1c." not in md  # collapsed into §1 itself, not renamed
    assert "### 1d." not in md
    assert md.count("## 1. ") == 1


def test_section_one_says_what_it_actually_lists() -> None:
    """It is a source-data finding about attr 1083, not a report on whether generation ran.

    The old wording sent an operator to re-run generation for a gap re-running cannot close, and
    under scoped generation it would have read as an accusation about every already-live unit.
    """
    md = _render(
        generated_issues=[_blank_1083("08713195000001")],
        products=_products("08713195000001"),
    )

    assert "1083" in md
    assert "The generator produced nothing" not in md
    # The distinction that stops it being read as a generation failure.
    assert "already live" in md


def test_a_unit_with_no_1067_either_is_named_as_held() -> None:
    """1083 blank *and* no 1067 is the one that actually blocks: nothing to write copy from."""
    md = _render(
        generated_issues=[_blank_1083("08713195000001", "fr")],
        products=_products("08713195000001"),
    )

    assert "| fr | **Held** — no 1067 either (E21) |" in md


def test_a_unit_whose_1067_carries_the_copy_is_not_reported_as_blocking() -> None:
    """§1's rows are not uniform, and calling them all blockers is how a blocker gets ignored.

    A product whose 1083 is blank but whose 1067 carries usable copy publishes perfectly well —
    the datapool is still missing a field, which is worth fixing, but not before this SKU ships.
    """
    products = _products("08713195000001")
    gtin14 = next(iter(products))
    products[gtin14] = products[gtin14].model_copy(
        update={"description_long": LocalisedText(values={"nl": "Kort en krachtig"})}
    )

    md = _render(generated_issues=[_blank_1083("08713195000001")], products=products)

    assert "| Publishes from 1067 |" in md


def test_the_generated_copy_count_says_which_copy_it_counted() -> None:
    """Under scoped generation the number means "written this run", not "all in-scope copy".

    A count whose meaning moves without the words moving is the failure R-a is also about.
    """
    gen = [
        _issue("08713195000001", "content_generated", field="generated_description.nl", value="s")
    ]

    md = _render(generated_issues=gen, products=_products("08713195000001"))

    assert "1 generated-copy row(s) written this run" in md
