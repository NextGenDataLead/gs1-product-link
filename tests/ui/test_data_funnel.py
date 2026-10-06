"""The Data screen's coverage funnel and the caption above Next — counted, not rendered.

Pure: rows in the shape :func:`ui.batch_grid.choose` builds them, an :class:`Eligibility`, numbers
out. The funnel is what the operator reads to know what a run will do, so each of its figures is
pinned to the split the plan makes.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("nicegui", reason="the ui extra is not installed here")

from lib.eligibility import Eligibility  # noqa: E402
from ui.batch_grid import _GTIN, _ROW, Funnel, funnel, save_line  # noqa: E402

_A, _B, _C, _D, _E = (f"0871319500000{n}" for n in range(1, 6))


def _rows(*gtins: str | None) -> list[dict[str, Any]]:
    return [{_ROW: n, _GTIN: gtin} for n, gtin in enumerate(gtins)]


#: A eligible with a video · B eligible, missing fr · C held for data · D not in the export · E ok
_VERDICT = Eligibility(
    not_eligible={_C: "missing data: x"}, missing_video={_B: "no confirmed video in fr"}
)
_EXPORTED = {_A, _B, _C, _E}


def test_the_funnel_splits_the_list_the_way_a_run_will() -> None:
    rows = _rows(_A, _B, _C, _D, _E)

    counts = funnel(rows, ticked=[rows[0], rows[1]], verdict=_VERDICT, exported=_EXPORTED)

    assert counts == Funnel(
        listed=5,
        not_in_export=1,
        not_eligible=1,
        eligible=3,
        selected=2,
        missing_video=1,
        missing_video_selected=1,
    )


def test_products_are_counted_once_however_many_rows_name_them() -> None:
    """The pilot's own list carries one barcode on two rows; a run publishes it once."""
    rows = _rows(_A, _A, None)

    counts = funnel(rows, ticked=rows[:2], verdict=Eligibility(), exported={_A})

    assert (counts.listed, counts.eligible, counts.selected) == (1, 1, 1)


def test_a_ticked_row_that_is_not_eligible_is_never_counted_as_selected() -> None:
    rows = _rows(_C)

    assert funnel(rows, ticked=rows, verdict=_VERDICT, exported=_EXPORTED).selected == 0


def test_an_undecidable_batch_has_nothing_eligible() -> None:
    """An unreadable mapping under the video rule holds everything; the funnel must agree."""
    rows = _rows(_A)

    counts = funnel(rows, ticked=rows, verdict=Eligibility(problem="x"), exported={_A})

    assert (counts.eligible, counts.not_eligible, counts.selected) == (0, 1, 0)


def test_the_caption_says_what_next_will_save_before_it_is_pressed() -> None:
    assert save_line(3, 3) == "Next saves all 3 eligible row(s) and goes on to the copy."
    assert save_line(1, 3) == (
        "Next saves 1 of 3 eligible row(s) — 2 unticked — and goes on to the copy."
    )
    assert save_line(0, 0) == "Nothing on the list is eligible, so a run would publish nothing."


def test_an_unticked_product_without_a_video_is_not_counted_as_selected_without_one() -> None:
    rows = _rows(_A, _B)

    counts = funnel(rows, ticked=[rows[0]], verdict=_VERDICT, exported=_EXPORTED)

    assert (counts.missing_video, counts.missing_video_selected) == (1, 0)
