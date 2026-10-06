"""What the Data screen says about the video rule, and that it offers the file that lifts it.

With ``media.restrict_to_mapped_gtins`` on, a product without a client-confirmed video in **every**
language never reaches the plan. The screen marked those rows in a Video column and then said
nothing more about them: no total, no consequence, no route. On the pilot that is 86 of 110 rows
marked, so the caption's "Next saves all 110 row(s)" was true while a run would publish 24 — two
true sentences that read as agreement.

Two things are pinned here. The sentence, which must count the **ticked** rows and name what a run
would publish; and the link, because both this module's docstring and ``ui/app.py`` claimed for
three releases that the mapping was "reached from the Data screen" while no such link existed. A
claim in a comment is not a route.

Needs NiceGUI only for the module import, not for a browser.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

pytest.importorskip("nicegui", reason="the ui extra is not installed here")

from ui import theme  # noqa: E402
from ui.pages.data import _GTIN, _HELD, _video_counts, _video_line  # noqa: E402

#: Repo-rooted, the way the sibling contract tests resolve: a cwd-relative path reads these files
#: only when pytest happens to be run from the repository root.
_ROOT: Final = Path(__file__).resolve().parent.parent.parent
_DATA: Final = _ROOT / "ui" / "pages" / "data.py"
_PREFLIGHT: Final = _ROOT / "lib" / "preflight.py"

#: The route the mapping editor is registered at, named once here so a rename fails loudly.
_VIDEO_ROUTE = "/videos"


def _row(gtin: str, *, held: bool) -> dict[str, object]:
    """One table row as the grid builds it — the barcode it counts by, and the Video column's mark.

    The mark is the string the column renders, because that is what the count reads: the band and
    the column must never be able to disagree about which rows are held.
    """
    return {_GTIN: gtin, _HELD: "no video yet" if held else ""}


def test_a_held_batch_says_how_many_and_what_a_run_would_publish() -> None:
    """The pilot's own numbers: 86 of 110 held, so 24 publish.

    Both halves matter. The hold count alone leaves the operator to subtract, and it is the
    *difference* that contradicts a caption promising to save all 110.
    """
    line = _video_line(86, 110)

    assert "86 of the 110" in line
    assert "a run would publish 24" in line


def test_a_fully_mapped_batch_says_so_rather_than_going_quiet() -> None:
    """Silence would be indistinguishable from the rule not running.

    The band only exists on a batch that holds something, so this is what the operator sees after
    unticking the held rows — and "nothing here is held" is the confirmation that the unticking
    worked, not noise.
    """
    assert (
        _video_line(0, 24) == "All 24 ticked product(s) have a confirmed video in every language."
    )


def test_an_empty_batch_does_not_claim_anything_is_confirmed() -> None:
    """Reachable with one press of *Untick all shown*.

    "All 0 ticked product(s) have a confirmed video" is both nonsense and reassuring, which is the
    worst pair available.
    """
    assert _video_line(0, 0) == "Nothing is ticked, so a run would publish nothing."


def test_the_band_counts_barcodes_and_not_spreadsheet_rows() -> None:
    """A run publishes products, and the pilot's own list names ``08713195008486`` on two rows.

    Counted by row, its 111 matched rows against 87 held marks still printed "a run would publish
    24" — right only because that duplicate is itself held. The day it is not, the row count claims
    a page no run creates, which is the overclaim this band exists to retire.
    """
    duplicated = [_row("08713195008486", held=False) for _ in range(2)]

    assert _video_counts(duplicated) == (0, 1)


def test_a_duplicated_barcode_is_held_once_not_twice() -> None:
    """The same fold on the held side: one product, one hold, whatever the sheet repeats."""
    rows = [_row("08713195008486", held=True) for _ in range(2)]

    assert _video_counts(rows) == (1, 1)


def test_counts_separate_the_held_from_the_rest() -> None:
    """The ordinary case, in the order :func:`_video_line` takes them: held first, total second."""
    rows = [
        _row("08713195000001", held=True),
        _row("08713195000002", held=True),
        _row("08713195000003", held=False),
    ]

    assert _video_counts(rows) == (2, 3)


def test_the_line_speaks_the_doctors_vocabulary_for_the_same_rule() -> None:
    """One rule, one spelling. ``check_scope`` renders the other half of this sentence.

    An operator reads this band while choosing the batch and ``check_scope`` on Preflight a minute
    later. Two phrasings of one hold read as two different rules — and this repo has shipped a flag
    meaning one thing on one path and something else on another three times.
    """
    shared = "a confirmed video in every language"
    assert shared in _video_line(20, 37)
    assert shared in _PREFLIGHT.read_text(encoding="utf-8"), (
        "lib/preflight.check_scope no longer spells the video hold this way — the Data screen's "
        "band copies its vocabulary deliberately, so change both or neither"
    )


def test_the_data_screen_offers_a_route_to_the_mapping() -> None:
    """The defect this band exists to fix: a hold the operator cannot act on from here.

    Asserted against the source rather than a rendered page because the band is built inside the
    grid, which needs an export, a selection list and a client on disk. What must not regress is
    cheaper than that: the screen names the route.
    """
    routes = {
        node.value
        for node in ast.walk(ast.parse(_DATA.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert _VIDEO_ROUTE in routes, (
        f"ui/pages/data.py no longer links to {_VIDEO_ROUTE} — a product held for want of a video "
        "is then marked on this screen with no way to reach the file that lifts the hold"
    )


def test_that_route_is_one_the_rail_registers() -> None:
    """The other direction: a link to a route nothing serves is a dead link.

    Pinned separately from the check above so a renamed route fails as a rename rather than as a
    missing link on the Data screen.
    """
    assert _VIDEO_ROUTE in {screen.route for screen in theme.NAV}
