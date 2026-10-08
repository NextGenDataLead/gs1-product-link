"""Tests for how the Publish screen words a CHANGED row's diff (``ui.pages.publish._changes``)."""

from __future__ import annotations

import pytest

from lib.records import LocalisedText, PlanClassification, PlanRow, ProductRecord

pytest.importorskip("nicegui")

from ui.pages.publish import _changes  # noqa: E402 — only importable with the [ui] extra


def _row(diff: dict[str, tuple[str, str]]) -> PlanRow:
    return PlanRow(
        gtin="08713195007359",
        language="nl",
        classification=PlanClassification.CHANGED,
        slug="p-08713195007359",
        target_url="https://wp.test/noviplast/p-08713195007359/",
        title="Rugsteun",
        content_hash="h",
        product=ProductRecord(
            gtin="08713195007359",
            brand="Noviplast",
            product_name=LocalisedText(values={"nl": "Rugsteun"}),
        ),
        diff=diff,
    )


def test_a_record_still_linking_fr_reads_in_the_operators_words() -> None:
    lines = _changes(_row({"gs1_languages": ("every language", "nl only")}))

    assert lines == ["Changes:", "  GS1 link: every language → nl only"]


def test_a_link_never_written_still_reads_as_before() -> None:
    lines = _changes(_row({"gs1_link": ("not written", "will be written")}))

    assert lines == ["Changes: resolver link not written yet"]
