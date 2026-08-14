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

from pathlib import Path
from typing import Any

from nicegui import ui

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

    with theme.page("Data", client_id=cid, environment=cfg.gs1.environment if cfg else None):
        theme.heading(
            theme.step("Data"),
            "Video mapping",
            "Which video belongs to which product, in each language.",
        )
        if cfg is None or cid is None:
            theme.band("clients.yml did not load. Fix that on the Setup screen first.", "danger")
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
    _coverage(coverage, cfg, path, cid)

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
                ui.notify("Nothing to save", type="warning")
                return
            backup = _write(path, video_map_edit.apply_edits(text, pending))
            if backup is None:
                return
            ui.notify(
                f"Saved {len(pending)} row(s). Previous version kept at {backup.name}",
                type="positive",
            )
            pending.clear()
            refresh_status()
            _coverage(coverage, cfg, path, cid)

        def add_missing() -> None:
            absent = video_map_edit.files_missing_from_map(text, files)
            if not absent:
                ui.notify("Every file on disk is already in the mapping", type="positive")
                return
            candidate = text
            for language, names in absent.items():
                candidate = video_map_edit.append_rows(candidate, language, names)
            backup = _write(path, candidate)
            if backup is None:
                return
            total = sum(len(names) for names in absent.values())
            ui.notify(
                f"Added {total} unset row(s). Previous version kept at {backup.name}. "
                "Reload the screen to fill them in.",
                type="positive",
            )

        with ui.row().classes("gap-3 mt-4 items-center"):
            theme.quiet_action("Add files that are on disk but not in the mapping", add_missing)
            theme.action("Save the mapping", save, danger=True)
        refresh_status()


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
        ui.notify(str(exc), type="negative", timeout=15000)
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

    def run() -> None:
        result = runner.run(argv)
        output.style("display:block")
        output.clear()
        output.push(result.stderr or result.stdout or "(no output)")

    theme.quiet_action("Run the coverage check", run)
