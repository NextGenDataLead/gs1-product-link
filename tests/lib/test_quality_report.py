"""Unit tests for lib/quality_report.py — the pure consolidated-report renderer.

Pure and deterministic: given already-parsed issue lists + products, it returns markdown.
No I/O, no clock (the snapshot date is injected), so the same inputs render byte-identical.
"""

from __future__ import annotations

from lib.quality_report import render_quality_report
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
    assert "generated_cache.json" in md
    assert "src" not in md and "txt" not in md  # no per-row source dump
    assert "2b." not in md  # the old subsection is gone
