"""The §0 header labels, against the markdown renderer the Data screen actually uses.

The bug this guards against is not a logic bug — it is markup that is correct in the source and
invisible once rendered. `**product·3301**` in a table header is exactly that: markdown makes
header cells bold anyway, so the marker separating "a gap here holds the whole SKU" from "a gap
here only thins the page" showed up in the file and nowhere on screen.

So asserting the *source* string is only half a test. `ui/pages/data.py` renders the report with
``ui.markdown``, which is markdown2 with ``['fenced-code-blocks', 'tables']``, and that is where
the group label has to survive as a line break — a plain newline would be folded into a space,
putting `MANDATORY product·3301` on one line and undoing the grouping.

Lives under ``tests/ui/`` because markdown2 arrives with NiceGUI: in the required CI job, which
installs only ``.[dev]``, there is nothing here to import.
"""

from __future__ import annotations

import pytest

from lib.gdsn import GdsnSource
from lib.quality_report import MatrixInput, render_quality_report
from lib.records import LocalisedText, ProductRecord

pytest.importorskip("markdown2", reason="the ui extra is not installed here")

import markdown2  # noqa: E402

#: What `ui.markdown` passes to markdown2 (NiceGUI's `Markdown.default_extras`).
_EXTRAS = ["fenced-code-blocks", "tables"]


def _report() -> str:
    matrix = MatrixInput(
        products=[
            ProductRecord(
                gtin="08713195000001",
                brand="Noviplast",
                product_name=LocalisedText(values={"nl": "a", "fr": "b"}),
            )
        ],
        gdsn_map={
            "product_name": GdsnSource(sheet="S", attribute="3301", localised=True, required=True)
        },
        gdsn_extras={"material": GdsnSource(sheet="S", attribute="Material")},
        languages=["nl", "fr"],
        video_confirmed={"nl": set(), "fr": set()},
    )
    return render_quality_report(
        client_id="noviplast",
        source_issues=[],
        generated_issues=[],
        video_map_issues=[],
        category_issues=[],
        products={},
        snapshot="2026-07-27",
        freshness={},
        matrix=matrix,
    )


def test_the_group_labels_reach_the_screen_as_two_lines() -> None:
    html = markdown2.markdown(_report(), extras=_EXTRAS)

    assert "<th>MANDATORY<br>product·3301</th>" in html
    assert "<th>optional<br>material</th>" in html


def test_the_installed_markdown_renderer_still_makes_header_cells_bold_on_its_own() -> None:
    """The premise of the fix, checked rather than assumed.

    If a future renderer stopped emitting ``<th>`` — or started ignoring the bold that comes with
    it — the whole reason for replacing `**…**` with a group label would be gone, and this test is
    what would say so instead of the change quietly becoming cargo cult.
    """
    html = markdown2.markdown("| a | b |\n|---|---|\n| 1 | 2 |\n", extras=_EXTRAS)

    assert "<th>a</th>" in html  # no <strong> needed: <th> is bold by default in every browser
