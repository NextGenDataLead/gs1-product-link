"""Unit tests for lib/quality_report.py — the pure consolidated-report renderer.

Pure and deterministic: given already-parsed issue lists + products, it returns markdown.
No I/O, no clock (the snapshot date is injected), so the same inputs render byte-identical.
"""

from __future__ import annotations

from lib.gdsn import GdsnSource
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


def test_blank_title_blocks_publish() -> None:
    src = [
        _issue(
            "08713195007649",
            "value_blank",
            field="product_name.fr",
            source="TradeItemDescription attr 3301",
        )
    ]
    md = _render(source_issues=src, products=_products("08713195007649"))

    # A blank title is a publish blocker: it lands in section 1, not the source-fix section.
    section_1, _, rest = md.partition("## 2.")
    assert "08713195007649" in section_1
    assert "title" in section_1.lower()
    assert "Blank title / image" in md  # summary row, marked a blocker


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

    assert "2 generated-copy row(s) are reviewed" in md  # count-based pointer
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

    body = [line for line in md.splitlines() if line.startswith("| `0871")]
    assert body[0].startswith("| `08713195000002`")  # richer first


def test_matrix_marks_mandatory_columns_and_puts_them_first() -> None:
    md = _render(matrix=_matrix(products=[_p("08713195000001")]))

    header = next(line for line in md.splitlines() if line.startswith("| GTIN |"))
    cols = [c.strip() for c in header.split("|")[1:-1]]
    assert cols[2] == "**product·3301**"  # mandatory, first after GTIN/Name
    assert cols[-3] == "material"  # optional, after the mandatory block
    assert cols[-2] == "**video**"


def test_a_localised_field_in_one_language_is_a_half_mark() -> None:
    md = _render(
        matrix=_matrix(
            products=[_p("08713195000001", product_name=LocalisedText(values={"nl": "a"}))]
        )
    )

    row = next(line for line in md.splitlines() if line.startswith("| `08713195000001`"))
    assert "◐" in row


#: A `gdsn_extras` entry whose source attribute carries a LanguageCode/Value pair.
_LOCALISED_EXTRA = {"functional_name": GdsnSource(sheet="S", attribute="3301", localised=True)}


def _extra_cell(md: str, gtin: str) -> str:
    """Trailing cells are: … | functional·name | video | score |, so the extra sits at -4."""
    row = next(line for line in md.splitlines() if line.startswith(f"| `{gtin}`"))
    return row.split("|")[-4].strip()


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

    row = next(line for line in md.splitlines() if line.startswith("| `08713195000001`"))
    assert row.split("|")[-4].strip() == "●"


def test_the_video_column_reflects_both_languages() -> None:
    gtin = "08713195000001"
    both = _render(
        matrix=_matrix(products=[_p(gtin)], video_confirmed={"nl": {gtin}, "fr": {gtin}})
    )
    one = _render(matrix=_matrix(products=[_p(gtin)], video_confirmed={"nl": {gtin}, "fr": set()}))
    neither = _render(matrix=_matrix(products=[_p(gtin)]))

    def video_cell(md: str) -> str:
        return next(line for line in md.splitlines() if line.startswith(f"| `{gtin}`")).split("|")[
            -3
        ]

    assert "●" in video_cell(both)
    assert "◐" in video_cell(one)
    assert "○" in video_cell(neither)


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

    header = next(line for line in md.splitlines() if line.startswith("| GTIN |"))
    assert "logistics" not in header
    assert "material" in header  # the neighbouring optional column is untouched
