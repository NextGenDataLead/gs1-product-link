"""The video mapping — every file, its state, and the hints for the ones still unset.

Reached from the Data screen rather than the rail, because it is one file's editor rather than a
step of the run. It exists because the mapping decided whether a product could be published at
all, and was the one input with no way to reach it from here: with
``media.restrict_to_mapped_gtins`` on, a product without a confirmed video in **every** language
never reaches the plan. An operator could complete every screen in the app and still produce an
empty plan, with the fix available only in a text editor.

What this screen will not do, and why:

* **It never re-drafts the file.** Confirmed rows are client sign-off; regenerating the skeleton
  would discard them. ``python -m scripts.build_video_map`` still prints a draft, in a terminal,
  where redirecting it is a deliberate act.
* **The hints are suggestions, not answers.** ``rank_candidates`` compares an English marketing
  filename against a Dutch product feed; a 0.53 is a coincidence more often than a match. Clicking
  one fills the box and nothing more — confirming a mapping is the client's call.
* **Nothing is written until Save.** Edits accumulate in the browser, so a half-finished session
  costs nothing, and one Save produces one backup rather than one per row.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final
from xml.etree import ElementTree as ET

from nicegui import events, ui

from lib import video_signoff, xlsx
from lib.config import ClientConfig
from lib.errors import VideoMapError
from lib.media_video import (
    list_video_files,
    load_video_map,
    normalize_video_name,
    rank_candidates,
    summarize_video_map,
)
from lib.records import ProductRecord
from ui import REPO_ROOT, context, runner, theme, video_map_edit

#: How many fuzzy hints to offer per file. Three is what the drafted comments already carry.
_HINTS = 3

#: Rows in the table before it scrolls rather than growing the page.
_TABLE_HEIGHT = "55vh"

_STATE_LABEL = {
    "confirmed": "confirmed",
    "skip": "skip",
    "unset": "needs a GTIN",
}


def render() -> None:
    cid = context.client_id()
    cfg = context.client_config(cid)

    with theme.page(
        "Video mapping",
        client_id=cid,
        environment=cfg.gs1.environment if cfg else None,
        facts=context.rail_facts(cid, cfg),
    ):
        theme.heading(
            theme.eyebrow("Video mapping"),
            "Video mapping",
            "Which video belongs to which product, in each language.",
        )
        if cfg is None or cid is None:
            theme.blocked(
                "clients.yml did not load, so this screen has nothing to work from.",
                link_label="Open Setup →",
                route="/",
            )
            return
        if cfg.media is None or not cfg.media.video_map_path:
            theme.band(
                "No `media.video_map_path` in clients.yml — this client attaches no videos.",
                "quiet",
            )
            return

        ui.link("← back to Data", "/data").classes("note")
        _editor(cfg, cid, Path(cfg.media.video_map_path))


def _editor(cfg: ClientConfig, cid: str, map_path: Path) -> None:
    path = map_path if map_path.is_absolute() else REPO_ROOT / map_path
    try:
        text = path.read_text(encoding="utf-8")
        rows = video_map_edit.parse(text)
    except (OSError, VideoMapError) as exc:
        theme.band(str(exc), "danger")
        return

    files = _files_on_disk(cfg)
    products = context.load_products(cid)
    pending: dict[tuple[str, str], str] = {}

    coverage = ui.column().classes("w-full")

    def refresh_coverage() -> None:
        """Recount from the file. Cheap, and these are the figures an edit is actually about."""
        _coverage(coverage, cfg, path, cid)

    refresh_coverage()
    _signoff(cfg, cid, path, pending, refresh_coverage)
    _rows_section(path, text, rows, files, products, pending, refresh_coverage)


def _rows_section(  # noqa: PLR0913 — the file, what is in it, what is on disk, and the counts
    path: Path,
    text: str,
    rows: list[video_map_edit.VideoRow],
    files: dict[str, list[str]],
    products: list[ProductRecord],
    pending: dict[tuple[str, str], str],
    applied: Callable[[], None],
) -> None:
    """The mapping row by row: pick a row, read its hints, stage a GTIN, save them all at once.

    Lifted out of :func:`_editor` when the sign-off import arrived beside it. Unchanged, and it
    keeps the property that matters: ``text`` is the file as it was read when the screen was built,
    so one Save produces one backup and the edits land on the bytes the operator was looking at.
    """
    with theme.section("Every file, and what it maps to"):
        ui.label(
            "A product needs a confirmed video in every language before it can be published at "
            "all. `skip` is a decision — a video that maps to no product — and is not a gap."
        ).classes("note")

        table, status = _table(rows, files)
        editor = ui.column().classes("w-full mt-4")

        def refresh_status() -> None:
            status.text = f"{len(pending)} unsaved change(s)" if pending else "no unsaved changes"

        def select(event: Any) -> None:
            key = event.args[1]["file"], event.args[1]["language"]
            row = next(r for r in rows if r.file == key[0] and r.language == key[1])
            editor.clear()
            with editor:
                _row_editor(row, products, pending, table, refresh_status)

        table.on("rowClick", select)

        def save() -> None:
            if not pending:
                theme.notify_warning("Nothing to save")
                return
            backup = _write(path, video_map_edit.apply_edits(text, pending))
            if backup is None:
                return
            theme.notify_ok(f"Saved {len(pending)} row(s). Previous version kept at {backup.name}")
            pending.clear()
            refresh_status()
            applied()

        def add_missing() -> None:
            absent = video_map_edit.files_missing_from_map(text, files)
            if not absent:
                theme.notify_ok("Every file on disk is already in the mapping")
                return
            candidate = text
            for language, names in absent.items():
                candidate = video_map_edit.append_rows(candidate, language, names)
            backup = _write(path, candidate)
            if backup is None:
                return
            total = sum(len(names) for names in absent.values())
            theme.notify_ok(
                f"Added {total} unset row(s). Previous version kept at {backup.name}. "
                "Reload the screen to fill them in."
            )

        with ui.row().classes("gap-3 mt-4 items-center"):
            theme.quiet_action("Add files that are on disk but not in the mapping", add_missing)
            theme.action("Save the mapping", save, danger=True)
        refresh_status()


def _signoff(  # noqa: PLR0913 — the sheet, the mapping, and what to do once it is applied
    cfg: ClientConfig,
    cid: str,
    path: Path,
    pending: dict[tuple[str, str], str],
    applied: Callable[[], None],
) -> None:
    """Take the client's filled-in sheet, show what it would change, and apply only the fills.

    This is the one input the pilot has been waiting on, and until now it arrived as a spreadsheet
    somebody then re-typed into the rows below. The retyping is the whole cost: 173 rows, and one
    transposed digit maps a video to the wrong product with nothing downstream to catch it.

    **Nothing is written by the upload.** Reading the sheet produces a plan, the plan is shown, and
    a second press applies it — the same shape as the row-by-row editor, where edits accumulate and
    one Save writes them. :mod:`lib.video_signoff` decides; this only renders and asks.

    **The sheet is read and not kept.** Where operator inputs are filed is an open question in this
    project, and an upload here that invented a folder of its own would be answering it by
    accident. What changed is recoverable from the mapping's dated backup, which is what somebody
    would actually go looking for.
    """
    with theme.section(
        "Import the client's sign-off sheet",
        explain=(
            "The spreadsheet the client fills in — `python -m scripts.report_video_candidates` "
            "writes the one to send them, and this reads it back. Any sheet with a language, a "
            "filename and a barcode column will do, under whatever those are called (EAN, "
            "Barcode, Taal, Filename…); every other column is ignored, and the table may sit "
            "below a title row on any sheet of the workbook. Uploading writes nothing: it shows "
            "what the sheet would change, and a second press applies it. Only rows that are still "
            "unset are ever filled — a row already signed off is reported as a conflict and left "
            "exactly as it is."
        ),
    ):
        plan_box = ui.column().classes("w-full")

        async def receive(event: events.UploadEventArguments) -> str:
            # Refused rather than merged: `apply_edits` rewrites the file text as it was read when
            # this screen was built, so an import on top of unsaved row edits would write the
            # import and silently drop the edits — one success message for both.
            if pending:
                theme.notify_problem(
                    f"Save or discard your {len(pending)} unsaved row edit(s) first — an import "
                    "rewrites the whole file and would drop them."
                )
                return "Not read — there are unsaved edits below."

            data = await event.file.read()
            try:
                grid = xlsx.read_grid(io.BytesIO(data), header_row=video_signoff.is_header)
            except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
                theme.notify_problem(f"That file could not be read as a spreadsheet: {exc}")
                return "Not read."
            if grid is None or video_signoff.columns(grid) is None:
                theme.notify_problem(
                    "No sheet in that file has both a barcode column and a filename column. The "
                    "ⓘ above lists the names this looks for."
                )
                return "Not read — no sheet with the columns this needs."

            decided = video_signoff.plan(
                grid,
                load_video_map(path),
                exported={product.gtin14 for product in context.load_products(cid)},
                languages=cfg.wordpress.languages,
            )
            plan_box.clear()
            with plan_box:
                _plan_view(decided, path, pending, applied)
            fills = len(decided.of(video_signoff.FILL))
            theme.notify_ok(f"Read. {fills} row(s) would be filled — nothing is written yet.")
            return f"{len(decided.rows)} row(s) read from {event.file.name}."

        theme.upload("Sign-off sheet (.xlsx)", receive, busy="Reading the sheet…")


#: The plan's tables, in the order somebody acts on them: what will happen, then what they have to
#: decide, then what is wrong with the sheet. ``unchanged`` and ``blank`` get no table — they are
#: counts, and on a real sheet they are most of it.
_PLAN_SECTIONS: Final = (
    (
        video_signoff.FILL,
        "Would be filled in ({n})",
        "These rows are unset in the mapping, and the sheet gives a barcode the export carries.",
    ),
    (
        video_signoff.CONFLICT,
        "Conflicts ({n}) — nothing here will be touched",
        "The mapping already carries a different confirmed GTIN for these files. A confirmed row "
        "is client sign-off, so an import never overwrites one: settle these by hand, in the "
        "row-by-row table below.",
    ),
    (
        video_signoff.REJECTED,
        "Could not be used ({n})",
        "Each of these says what is wrong with it. A barcode problem is fixed in the sheet and the "
        "file uploaded again; an unknown filename usually means the mapping has no row for that "
        "video yet, which the button below this table adds.",
    ),
)


def _plan_view(
    decided: video_signoff.SignoffPlan,
    path: Path,
    pending: dict[tuple[str, str], str],
    applied: Callable[[], None],
) -> None:
    """What the sheet would do, then the button that does it.

    The counts come first and are deliberately not summed: a fill is work the import does, a
    conflict is work a person has to settle, a rejection is a defect in the sheet, and a blank is a
    row the client has not reached yet. Added together, the only number anybody could act on would
    be the one that disappeared.
    """
    with ui.row().classes("gap-12 items-end mb-4"):
        theme.figure(str(len(decided.of(video_signoff.FILL))), "row(s) would be filled")
        theme.figure(str(len(decided.of(video_signoff.CONFLICT))), "conflict(s)")
        theme.figure(str(len(decided.of(video_signoff.REJECTED))), "rejected")
        theme.figure(str(len(decided.of(video_signoff.UNCHANGED))), "already set")
        theme.figure(str(len(decided.of(video_signoff.BLANK))), "left blank")

    for outcome, title, note in _PLAN_SECTIONS:
        rows = decided.of(outcome)
        if rows:
            _plan_table(title.format(n=len(rows)), note, rows)

    fills = decided.edits
    if not fills:
        theme.band("Nothing in that sheet is new, so there is nothing to apply.", "quiet")
        return

    def apply() -> None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            theme.notify_problem(str(exc))
            return
        backup = _write(path, video_map_edit.apply_edits(text, fills))
        if backup is None:
            return
        pending.clear()
        # The coverage figures above are recounted from the file, because they are the answer to
        # "did that help?" — and they are what Preflight will say about this client next.
        applied()
        # A dialog, not a toast, and **no reload**: this says how many signed-off rows changed and
        # where the previous version went, which is more than a toast has time for. Reloading the
        # page under it would destroy it unread — the same race the Data screen's four-second beat
        # exists for. So the operator closes it, and it says what on this screen is now stale.
        theme.announce(
            f"{len(fills)} row(s) filled in",
            f"The previous version of the mapping is kept beside it as {backup.name}. The "
            "coverage figures above have been recounted; the row-by-row table below still shows "
            "what the file said before, so reload this page to work in it.",
        )

    theme.action(f"Fill in the {len(fills)} row(s) from this sheet", apply, danger=True)


def _plan_table(title: str, note: str, rows: tuple[video_signoff.SignoffRow, ...]) -> None:
    """One outcome's rows. The spreadsheet row number comes first: it is the address of the fix."""
    theme.subhead(title)
    ui.label(note).classes("note")
    columns = [
        {"name": "line", "label": "Row", "field": "line", "align": "left", "sortable": True},
        {"name": "language", "label": "Lang", "field": "language", "align": "left"},
        {"name": "file", "label": "File", "field": "file", "align": "left", "sortable": True},
        {"name": "given", "label": "In the sheet", "field": "given", "align": "left"},
        {"name": "gtin", "label": "Read as", "field": "gtin", "align": "left"},
        {"name": "detail", "label": "Why", "field": "detail", "align": "left"},
    ]
    data = [
        {
            "line": row.line,
            "language": row.language,
            "file": row.file,
            "given": row.given,
            "gtin": row.gtin or "—",
            "detail": row.detail,
        }
        for row in rows
    ]
    table = ui.table(columns=columns, rows=data, row_key="line", pagination=0)
    table.classes("w-full mt-2").props("dense flat bordered")


def _row_editor(
    row: video_map_edit.VideoRow,
    products: list[ProductRecord],
    pending: dict[tuple[str, str], str],
    table: ui.table,
    refresh_status: Any,
) -> None:
    """The panel for one selected row: its hints, and a box to put a GTIN in."""
    with theme.section(f"{row.file}  ·  {row.language}"):
        if row.note:
            # The file's own note, not a fresh hint: it was written when the row was drafted, and
            # the suggestions below are recomputed now. On a confirmed row the note is the evidence
            # for the GTIN; on an unset one it is an older, worse guess than the buttons below it.
            ui.label(f"Noted in the file: {row.note.lstrip('# ')}").classes("note")

        current = pending.get((row.language, row.file), row.gtin)
        field = ui.input("GTIN", value=current).props("outlined dense").classes("w-96")

        hints = rank_candidates(normalize_video_name(row.file), products, top_n=_HINTS)
        if hints:
            ui.label(
                "Suggestions — a fuzzy match of the filename against the feed, nothing more. "
                "The filenames are English marketing names that mostly do not appear in the feed."
            ).classes("note mt-3")
            with ui.row().classes("gap-2 flex-wrap"):
                for hint in hints:
                    label = f"{hint.gtin} · {hint.name} ({hint.score:.2f})"
                    ui.button(
                        label, on_click=lambda _e=None, g=hint.gtin: field.set_value(g)
                    ).props("no-caps outline color=grey-8 size=sm")

        def stage(value: str) -> None:
            pending[(row.language, row.file)] = value
            field.set_value(value)
            for entry in table.rows:
                if entry["file"] == row.file and entry["language"] == row.language:
                    entry["gtin"] = value or "—"
                    entry["state"] = _STATE_LABEL[video_map_edit.state_of(value)] + " (unsaved)"
            table.update()
            refresh_status()

        with ui.row().classes("gap-3 mt-3"):
            theme.quiet_action("Use this GTIN", lambda: stage(field.value.strip()))
            theme.quiet_action("No product for this video", lambda: stage(video_map_edit.SKIP))
            theme.quiet_action("Clear", lambda: stage(""))


def _write(path: Path, candidate: str) -> Path | None:
    """Write the candidate, or say why not and leave the file alone. ``None`` means refused."""
    try:
        return video_map_edit.write_validated(path, candidate)
    except (OSError, VideoMapError) as exc:
        theme.notify_problem(str(exc))
        return None


def _table(
    rows: list[video_map_edit.VideoRow], files: dict[str, list[str]]
) -> tuple[ui.table, Any]:
    """The whole mapping as one table, plus the label that counts unsaved edits."""
    on_disk = {(language, name) for language, names in files.items() for name in names}
    data = [
        {
            "file": row.file,
            "language": row.language,
            "gtin": row.gtin or "—",
            "state": _STATE_LABEL[row.state],
            "disk": "yes" if (row.language, row.file) in on_disk else "not on disk",
        }
        for row in rows
    ]
    columns = [
        {"name": "file", "label": "File", "field": "file", "align": "left", "sortable": True},
        {"name": "language", "label": "Lang", "field": "language", "align": "left"},
        {"name": "state", "label": "State", "field": "state", "align": "left", "sortable": True},
        {"name": "gtin", "label": "GTIN", "field": "gtin", "align": "left"},
        {"name": "disk", "label": "File", "field": "disk", "align": "left"},
    ]
    table = ui.table(columns=columns, rows=data, row_key="file", pagination=0).classes("w-full")
    table.props(f'dense flat bordered virtual-scroll style="height: {_TABLE_HEIGHT}"')
    status = ui.label("no unsaved changes").classes("note mt-2")
    return table, status


def _coverage(container: ui.column, cfg: ClientConfig, path: Path, cid: str) -> None:
    """What the preflight would say about this file, counted from the same summary it uses."""
    container.clear()
    with container, theme.section("Coverage"):
        try:
            summary = summarize_video_map(
                load_video_map(path), _files_on_disk(cfg), cfg.wordpress.languages
            )
        except VideoMapError as exc:
            theme.band(str(exc), "danger")
            return
        with ui.row().classes("gap-12 items-end mb-4"):
            theme.figure(str(summary.confirmed_gtins), "GTIN(s) publishable")
            theme.figure(str(summary.unconfirmed), "row(s) needing a GTIN")
            theme.figure(str(summary.files), "video file(s) on disk")
        if summary.no_files_found:
            theme.band(
                "No video files found — the folders under media.video_folders are empty or not "
                "on this machine yet. The mapping is fine; the library has not arrived.",
                "warn",
            )
        _check(cid)


def _files_on_disk(cfg: ClientConfig) -> dict[str, list[str]]:
    if cfg.media is None:
        return {}
    return {
        language: [p.name for p in list_video_files(Path(folder))]
        for language, folder in cfg.media.video_folders.items()
    }


def _check(cid: str) -> None:
    """Run the real coverage gate in a subprocess, and show the command that did it.

    The figures above are computed in this process, which is what makes them instant. This is the
    same question asked of the command an operator would type — so a disagreement between them is
    visible here rather than at the next run.
    """
    argv = runner.build_video_map_argv(cid, check=True)
    theme.command(argv)
    output = ui.log().classes("console mt-2").style("display:none")

    # Async, and the subprocess runs off the event loop. Both halves are needed, for the
    # reason `runner.run_off_the_loop` gives: a blocking call in a sync handler holds the
    # loop until the command has already finished, so the running-state the button paints
    # arrives at the browser only once there is nothing left to report.
    async def run() -> None:
        result = await runner.run_off_the_loop(argv)
        output.style("display:block")
        output.clear()
        output.push(result.stderr or result.stdout or "(no output)")

    theme.quiet_action("Run the coverage check", run)
