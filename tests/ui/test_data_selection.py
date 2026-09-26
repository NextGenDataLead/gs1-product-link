"""The selection model on the Data screen: what a filter does to a tick, and what it must not.

The screen follows the spreadsheet rule people actually expect: **a tick belongs to a row, not to
the view.** Filtering changes what is on screen and nothing else; a bulk tick or untick acts on the
rows the filters are showing; the running total counts the whole file. The two cases the operator
described are the two tests below — filter then untick, and untick-all then filter then tick.

Why this is held in Python rather than read off the table: a Quasar table's ``selected`` only ever
holds rows it is currently rendering, so a ticked row that a filter hides drops out of it. Saving
from that list loses rows the operator never touched, and nothing on screen disagrees — the count
would be consistent with itself the whole way down.

Needs NiceGUI only for the module import, not for a browser.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("nicegui", reason="the ui extra is not installed here")

from ui.pages.data import _ROW, _admits, _Selection  # noqa: E402


def _rows(count: int) -> list[dict[str, Any]]:
    """``count`` rows, each carrying its own key and a category in column ``c1``."""
    return [
        {_ROW: n, "c0": f"gtin-{n}", "c1": "A" if n < count // 2 else "B"} for n in range(count)
    ]


def _shown(rows: list[dict[str, Any]], category: str) -> list[dict[str, Any]]:
    return [row for row in rows if row["c1"] == category]


def test_filtering_then_unticking_leaves_the_rest_alone() -> None:
    """100 ticked, filter to one value, untick those → 90 still ticked.

    The operator's first example, and the one that decides whether a filter is safe to use at all:
    if unticking inside a filter could touch a hidden row, the only safe way to prune a list would
    be to scroll the whole thing.
    """
    rows = _rows(100)
    selection = _Selection({int(row[_ROW]) for row in rows})
    visible = _shown(rows, "A")[:10]

    selection.remove(visible)

    assert len(selection.keys) == 90
    assert all(int(row[_ROW]) not in selection.keys for row in visible)


def test_unticking_everything_then_filtering_and_ticking_selects_only_those() -> None:
    """Nothing ticked, filter to 20 hits, tick all → exactly 20.

    The operator's second example. It is the same rule read the other way round, and it is the one
    that makes a filter a *tool for building* a batch rather than only for pruning one.
    """
    rows = _rows(100)
    selection = _Selection(set())
    hits = _shown(rows, "B")[:20]

    selection.add(hits)

    assert selection.keys == {int(row[_ROW]) for row in hits}


def test_a_hidden_tick_survives_a_table_event() -> None:
    """The failure this model exists to prevent, stated as a test.

    The table reports the rows *it* has selected, which can only ever be visible ones. Folding
    that in as the whole truth would silently drop every ticked row the filter was hiding — and
    the save would be consistent, the caption would agree, and the operator would find out from
    the site.
    """
    rows = _rows(10)
    selection = _Selection({0, 1, 2, 9})
    visible = [rows[0], rows[1]]

    # The operator unticks row 1 while rows 2 and 9 are filtered out of view.
    selection.sync_from(visible, [rows[0]])

    assert selection.keys == {0, 2, 9}


def test_a_column_with_no_filter_admits_everything() -> None:
    """An empty control is not a filter that matches nothing."""

    class _Empty:
        value = None

    class _Blank:
        value = []

    assert _admits(_Empty(), "anything")
    assert _admits(_Blank(), "anything")
    assert _admits(None, "anything")


def test_a_value_picker_matches_exactly_and_text_matches_loosely() -> None:
    """Two kinds of filter, because two kinds of column.

    A picker's values came from the column itself, so an exact match is what the operator chose. A
    text box is someone typing part of a barcode, where an exact match would find nothing.
    """

    class _Picked:
        value = ["A", "C"]

    class _Typed:
        value = "319"

    assert _admits(_Picked(), "A")
    assert not _admits(_Picked(), "B")
    assert _admits(_Typed(), "08713195007359")
    assert not _admits(_Typed(), "08700000000001")


def test_matching_ignores_case_and_surrounding_space() -> None:
    """The cells come from the operator's own spreadsheet, where both are ordinary."""

    class _Typed:
        value = " Rugsteun "

    assert _admits(_Typed(), "rugsteun blauw")
