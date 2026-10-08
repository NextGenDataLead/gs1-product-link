"""Tests for lib/live_inventory.py — everything this tool has put live, from the ledger.

Operator, 2026-10-07: a data-quality report for every product that went live, to give it follow-up.
The ledger records each page this machine wrote; a retracted or unpublished page is not live. The
site is the authority on what is there now, so the inventory says per page whether the site was
checked and what it answered — and never claims "live" for a page the check could not see.
"""

from __future__ import annotations

from datetime import UTC, datetime

from lib.live_inventory import live_products
from lib.records import State, StateEntry

A, B, C = "08713195004488", "08713195007151", "08713195000527"


def _entry(
    gtin: str, lang: str, *, video: str | None = "v.mp4", gs1: str = "h", **over: object
) -> StateEntry:
    base: dict[str, object] = {
        "wp_page_id": 1,
        "wp_url": f"https://site.test/{lang}/p-{gtin}/",
        "wp_featured_media_id": None,
        "content_hash": "c",
        "gs1_link_set_hash": gs1,
        "last_run": datetime(2026, 10, 7, 2, 28, tzinfo=UTC),
        "title": f"title-{gtin[-4:]}",
        "video_file": video,
    }
    base.update(over)
    return StateEntry.model_validate(base)


def _state() -> State:
    return State(
        client_id="noviplast",
        entries={
            A: {"nl": _entry(A, "nl"), "fr": _entry(A, "fr")},
            B: {"nl": _entry(B, "nl"), "fr": _entry(B, "fr", video="")},
            C: {"nl": _entry(C, "nl", retracted=True), "fr": _entry(C, "fr", wp_status="draft")},
        },
    )


def test_retracted_and_unpublished_pages_are_not_live() -> None:
    live = live_products(_state())

    assert [p.gtin for p in live] == [A, B]
    assert {page.language for page in live[0].pages} == {"nl", "fr"}
