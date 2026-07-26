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


def _issue(
    gtin: str, issue: str, *, field: str = "f", value: str = "", source: str = ""
) -> SourceIssue:
    return SourceIssue(gtin=gtin, field=field, source=source, issue=issue, value=value, detail="d")


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


def test_render_is_deterministic() -> None:
    args = {
        "generated_issues": [_issue("08713195000001", "content_generated", value="src")],
        "products": _products("08713195000001"),
    }
    assert _render(**args) == _render(**args)


def test_pipe_in_value_is_escaped_for_markdown_table() -> None:
    gen = [
        _issue(
            "08713195000001", "content_generated", field="generated_description.nl", value="a | b"
        )
    ]
    md = _render(generated_issues=gen, products=_products("08713195000001"))
    # the raw pipe must be escaped so it does not break the table column
    assert "a \\| b" in md
