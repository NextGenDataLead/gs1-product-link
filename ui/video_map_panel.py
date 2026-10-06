"""The video mapping as a panel: its coverage figures, and every file with what it maps to.

A component, not a screen — :mod:`ui.batch_view` is the precedent — because the mapping decides
whether a product can be published at all and so belongs on the screen where the batch is chosen.
It was a screen of its own under *This machine*, filed with the things touched once.

What it will not do, and why:

* **It never re-drafts the file.** Confirmed rows are client sign-off; regenerating the skeleton
  would discard them. ``python -m scripts.build_video_map`` still prints a draft, in a terminal,
  where redirecting it is a deliberate act.
* **The hints are suggestions, not answers.** ``rank_candidates`` compares an English marketing
  filename against a Dutch product feed; a 0.53 is a coincidence more often than a match. Clicking
  one fills the box and nothing more — confirming a mapping is the client's call.
* **Nothing is written until Save.** Edits accumulate in a :class:`MappingSession`, so a
  half-finished session costs nothing, and one Save produces one backup rather than one per row.

**Why a session, and why outside the page.** The editor used to capture the file's text once, when
the screen was built, and keep unsaved edits in a closure. A screen that redraws its sections — the
Data screen redraws on every upload — would then either drop the edits or keep a stale copy of the
file. Held here, keyed by client like ``ui.pages.data._BATCHES``, the edits survive a redraw from
an unrelated upload; every write re-reads the file, so nothing on screen asks to be reloaded; and a
file changed on disk since it was read is re-read before a save, rather than silently overwritten —
``video_map_edit.write_validated`` refusing a candidate that lost a row stays the backstop.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from nicegui import ui

from lib.config import ClientConfig
from lib.errors import VideoMapError
from lib.media_video import (
    files_by_language,
    load_video_map,
    normalize_video_name,
    rank_candidates,
    summarize_video_map,
)
from lib.records import ProductRecord
from ui import REPO_ROOT, context, runner, theme, video_map_edit

#: How many fuzzy hints to offer per file. Three is what the drafted comments already carry.
_HINTS: Final = 3

#: Rows in the table before it scrolls rather than growing the page.
_TABLE_HEIGHT: Final = "55vh"

_STATE_LABEL: Final = {
    "confirmed": "confirmed",
    "skip": "skip",
    "unset": "needs a GTIN",
}


@dataclass
class MappingSession:
    """The mapping as last read, and the edits not yet written to it.

    Attributes:
        path: The mapping file, resolved against the repository.
        text: The file as last read — what the next save applies edits to.
        rows: ``text`` parsed into rows.
        pending: Staged edits, ``(language, file) -> gtin``. Survive any redraw.
        problem: Why the file could not be read, or ``None``.
    """

    path: Path
    text: str = ""
    rows: list[video_map_edit.VideoRow] = field(default_factory=list)
    pending: dict[tuple[str, str], str] = field(default_factory=dict)
    problem: str | None = None
    _read_as: tuple[float, int] | None = None

    def reload(self) -> None:
        """Re-read the file. Staged edits are kept — they are applied at Save, to what is there."""
        try:
            self.text = self.path.read_text(encoding="utf-8")
            self.rows = video_map_edit.parse(self.text)
        except (OSError, VideoMapError) as exc:
            self.problem, self.rows = str(exc), []
            return
        self.problem = None
        self._read_as = _stat(self.path)

    def dirty(self) -> bool:
        return bool(self.pending)

    def stale_on_disk(self) -> bool:
        """Whether the file changed since it was read — edited by hand, or by another window."""
        return self._read_as != _stat(self.path)

    def write(self, candidate: str) -> Path | None:
        """Write ``candidate`` and re-read. The backup, or ``None`` if refused (and said so)."""
        try:
            backup = video_map_edit.write_validated(self.path, candidate)
        except (OSError, VideoMapError) as exc:
            theme.notify_problem(str(exc))
            return None
        self.reload()
        return backup


def _stat(path: Path) -> tuple[float, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime, stat.st_size


#: One per client for the life of the process — see the module docstring.
_SESSIONS: dict[str, MappingSession] = {}


def session_for(cid: str, cfg: ClientConfig) -> MappingSession | None:
    """This client's mapping session, or ``None`` when the client attaches no videos."""
    if cfg.media is None or not cfg.media.video_map_path:
        return None
    configured = Path(cfg.media.video_map_path)
    path = configured if configured.is_absolute() else REPO_ROOT / configured
    session = _SESSIONS.get(cid)
    if session is None or session.path != path:
        session = _SESSIONS[cid] = MappingSession(path)
        session.reload()
    elif not session.dirty() and session.stale_on_disk():
        session.reload()
    return session


def files_on_disk(cfg: ClientConfig) -> dict[str, list[str]]:
    return files_by_language(cfg.media.video_folders) if cfg.media is not None else {}


# --- Coverage --------------------------------------------------------------------------------


def coverage(cfg: ClientConfig, cid: str, session: MappingSession) -> None:
    """What the preflight would say about this file, counted from the same summary it uses.

    The answer to "did that sheet help?", which is why it sits under the sign-off upload.
    """
    if session.problem is not None:
        theme.band(session.problem, "danger")
        return
    try:
        summary = summarize_video_map(
            load_video_map(session.path), files_on_disk(cfg), cfg.wordpress.languages
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


# --- Every file, row by row ------------------------------------------------------------------


def rows(cfg: ClientConfig, cid: str, session: MappingSession, changed: Callable[[], None]) -> None:
    """The mapping row by row: pick a row, read its hints, stage a GTIN, save them all at once.

    ``changed`` is called after every write, so whatever else counts from the mapping — coverage,
    the batch grid's Video column, the report — recounts without a reload.
    """
    ui.label(
        "A product needs a confirmed video in every language before it can be published at "
        "all. `skip` is a decision — a video that maps to no product — and is not a gap."
    ).classes("note")
    if session.problem is not None:
        theme.band(session.problem, "danger")
        return

    files = files_on_disk(cfg)
    products = context.load_products(cid)
    table, status = _table(session, files)
    editor = ui.column().classes("w-full mt-4")

    def refresh_status() -> None:
        n = len(session.pending)
        status.text = f"{n} unsaved change(s)" if n else "no unsaved changes"

    def select(event: Any) -> None:
        key = event.args[1]["file"], event.args[1]["language"]
        row = next(r for r in session.rows if r.file == key[0] and r.language == key[1])
        editor.clear()
        with editor:
            _row_editor(row, products, session, table, refresh_status)

    table.on("rowClick", select)

    def save() -> None:
        if not session.pending:
            theme.notify_warning("Nothing to save")
            return
        if session.stale_on_disk():
            # Somebody changed the file since it was read. Apply the edits to what is there now
            # rather than to the copy in memory, which would quietly undo their change.
            session.reload()
            theme.notify_warning("The mapping changed on disk; your edits go onto the new version.")
        saved = len(session.pending)
        backup = session.write(video_map_edit.apply_edits(session.text, session.pending))
        if backup is None:
            return
        session.pending.clear()
        theme.notify_ok(f"Saved {saved} row(s). Previous version kept at {backup.name}")
        changed()

    def add_missing() -> None:
        session.reload()
        absent = video_map_edit.files_missing_from_map(session.text, files)
        if not absent:
            theme.notify_ok("Every file on disk is already in the mapping")
            return
        candidate = session.text
        for language, names in absent.items():
            candidate = video_map_edit.append_rows(candidate, language, names)
        backup = session.write(candidate)
        if backup is None:
            return
        total = sum(len(names) for names in absent.values())
        theme.notify_ok(f"Added {total} unset row(s). Previous version kept at {backup.name}.")
        changed()

    with ui.row().classes("gap-3 mt-4 items-center"):
        theme.quiet_action("Add files that are on disk but not in the mapping", add_missing)
        theme.action("Save the mapping", save)
    refresh_status()


def _row_editor(
    row: video_map_edit.VideoRow,
    products: list[ProductRecord],
    session: MappingSession,
    table: ui.table,
    refresh_status: Callable[[], None],
) -> None:
    """The panel for one selected row: its hints, and a box to put a GTIN in."""
    theme.subhead(f"{row.file}  ·  {row.language}")
    if row.note:
        # The file's own note, not a fresh hint: it was written when the row was drafted, and the
        # suggestions below are recomputed now. On a confirmed row the note is the evidence for the
        # GTIN; on an unset one it is an older, worse guess than the buttons below it.
        ui.label(f"Noted in the file: {row.note.lstrip('# ')}").classes("note")

    current = session.pending.get((row.language, row.file), row.gtin)
    box = ui.input("GTIN", value=current).props("outlined dense").classes("w-96")

    hints = rank_candidates(normalize_video_name(row.file), products, top_n=_HINTS)
    if hints:
        ui.label(
            "Suggestions — a fuzzy match of the filename against the feed, nothing more. "
            "The filenames are English marketing names that mostly do not appear in the feed."
        ).classes("note mt-3")
        with ui.row().classes("gap-2 flex-wrap"):
            for hint in hints:
                label = f"{hint.gtin} · {hint.name} ({hint.score:.2f})"
                ui.button(label, on_click=lambda _e=None, g=hint.gtin: box.set_value(g)).props(
                    "no-caps outline color=grey-8 size=sm"
                )

    def stage(value: str) -> None:
        session.pending[(row.language, row.file)] = value
        box.set_value(value)
        for entry in table.rows:
            if entry["file"] == row.file and entry["language"] == row.language:
                entry.update(_cells(value, unsaved=True))
        table.update()
        refresh_status()

    with ui.row().classes("gap-3 mt-3"):
        theme.quiet_action("Use this GTIN", lambda: stage(box.value.strip()))
        theme.quiet_action("No product for this video", lambda: stage(video_map_edit.SKIP))
        theme.quiet_action("Clear", lambda: stage(""))


def _cells(gtin: str, *, unsaved: bool) -> dict[str, str]:
    state = _STATE_LABEL[video_map_edit.state_of(gtin)]
    return {"gtin": gtin or "—", "state": f"{state} (unsaved)" if unsaved else state}


def _table(session: MappingSession, files: dict[str, list[str]]) -> tuple[ui.table, Any]:
    """The whole mapping as one table — staged edits shown as such — plus the unsaved count."""
    on_disk = {(language, name) for language, names in files.items() for name in names}
    data = []
    for row in session.rows:
        staged = session.pending.get((row.language, row.file))
        data.append(
            {
                "file": row.file,
                "language": row.language,
                **_cells(row.gtin if staged is None else staged, unsaved=staged is not None),
                "disk": "yes" if (row.language, row.file) in on_disk else "not on disk",
            }
        )
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
