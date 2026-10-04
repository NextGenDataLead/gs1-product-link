"""The batch in force, said the same way on every screen that acts on it.

Data, Content, Preflight and Publish all operate on one pair of files, and until now only the first
of them named either — and only while the operator was still looking at the upload button. Every
screen after it described a batch it would not identify, which is the condition under which somebody
generates copy for last quarter's export and finds out from the live site.

So this is one panel, rendered from :func:`lib.batch.in_force`, in one wording. Layout only: every
judgement is in ``lib/batch.py`` where it can be tested without a browser.

**Facts, never a tick.** The rail's rule, for the same reason: a green mark on Data because *an*
export exists cannot tell you it is the *right* export, and a tick that lies is worse than no tick.
A name, a date and a row count can be checked against what the operator believes.
"""

from __future__ import annotations

from nicegui import ui

from lib.batch import Batch, Chosen, Document
from ui import context, theme


def render(batch: Batch | None) -> None:
    """The panel. Renders nothing for a client with no selection to describe."""
    if batch is None:
        return
    with ui.element("div").classes("card mb-4"):
        ui.label("Batch in force").classes("gate-step")
        _line("Export", _export_words(batch.export))
        _line("Selection", _selection_words(batch))
        _agreement(batch)
        if batch.unreadable_records:
            ui.label(
                f"{batch.unreadable_records} line(s) of this client's history could not be read — "
                "usually the last one, truncated when a process was killed mid-write. A record may "
                "be missing."
            ).classes("note mt-2")


def _line(label: str, words: list[str]) -> None:
    with ui.row().classes("gap-3 items-baseline mt-2 w-full"):
        ui.label(label).classes("figure-label")
        with ui.column().classes("gap-0"):
            for index, text in enumerate(words):
                ui.label(text).classes("note" if index else "gate-title scroll-x")


def _export_words(export: Document) -> list[str]:
    """What the export is, leading with the name the operator would recognise.

    ``given_name`` first because that is the document they sent; the archived name is the copy that
    will still be openable in a year, and it goes underneath rather than instead. A batch identified
    only by ``export-20260920T015319.xlsx`` asks the operator to remember a timestamp.
    """
    if not export.exists:
        return ["No export uploaded yet."]
    named = f"“{export.given_name}”" if export.given_name else export.path.name
    counted = f"{export.rows} products" if export.rows is not None else "not parsed yet"
    under = [f"{context.age_of(export.modified)} · {counted}"]
    if export.archived_name:
        under.append(f"kept as {export.archived_name}")
    else:
        under.append("no record of this upload, so it cannot be named beyond its path")
    return [named, *under]


def _selection_words(batch: Batch) -> list[str]:
    """How many rows are ticked, out of how many were sent.

    "11 of 13" and "11" are different facts, and the second one hides the two the operator dropped.
    Where no upload is on disk to compare against, it says so rather than printing the same number
    twice — which would read as "nothing was dropped".
    """
    selection = batch.selection
    if not selection.exists:
        return ["No product list uploaded yet."]
    if selection.rows is None:
        return [
            "The selection on disk will not read.",
            f"{selection.path.name} — upload the list again on Data.",
        ]
    if batch.listed is None:
        return [
            f"{selection.rows} ticked",
            f"saved {context.age_of(selection.modified)}",
            "no uploaded list to compare against, so any row dropped cannot be counted",
        ]
    return [
        f"{selection.rows} of {batch.listed} ticked",
        f"saved {context.age_of(selection.modified)}",
    ]


def _agreement(batch: Batch) -> None:
    """Whether the ticks and the export belong together — the one thing the files cannot say.

    The loud case is a selection chosen against an export that has since been replaced: the ticks
    may name barcodes the current export has no row for, and nothing else in the tool notices. It
    was the risk the old "both files must arrive this visit" rule was reaching for, and hiding the
    grid never addressed it, because a run read the file either way.
    """
    if not batch.export.exists or not batch.selection.exists:
        return
    if batch.chosen_against is Chosen.THIS_EXPORT:
        ui.label("Chosen against the export now on disk.").classes("note mt-2")
        return
    if batch.chosen_against is Chosen.NOT_RECORDED:
        ui.label(
            "Which export these ticks were chosen against was not recorded — they predate this "
            "being kept. Re-saving on Data records it."
        ).classes("note mt-2")
        return
    against = batch.chosen_export or "an earlier export"
    theme.band(
        f"These ticks were chosen against {against}, not the export on disk now. A barcode the "
        "current export has no row for silently produces no page and no error — check the "
        "selection on Data before publishing.",
        "warn",
    )
