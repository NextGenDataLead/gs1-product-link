"""The batch: what it is made of, what each product still needs, and which products are in it.

**The selection list is the spine.** Everything else on this screen is measured against it, in the
order an operator assembles a batch:

1. the **product selection list** — which barcodes this batch may touch;
2. the **GS1 Data Source export** — the product data, parsed into ``products.json``;
3. the client's **video sign-off sheet** — which video is which product's, applied to
   ``mapping.yml``. The mapping itself is edited in that file, never on this screen;

side by side, because they are three documents arriving from three places and none waits on
another. Under them: what the sign-off sheet would change, the **coverage** funnel — in product
list → eligible → selected — and

4. **choose and save** — the list split into not in the export, not eligible (and why), missing
   video(s), and the eligible products, the only ones with a tick box (:mod:`ui.batch_grid`).

They are different documents from different places, and confusing them is the most expensive
mistake available here, so each has its own upload and its own name — and the sign-off upload
refuses a GS1 export outright. The config key is still ``process_list``: it appears in
``clients.yml``, the schema, the doctor payload and five call sites, and renaming it would break
every install. Only what the operator reads changed.

**Both batch uploads go to the configured path, never to a new one.** Neither ``parse_export`` nor
the list reader has an input-path override, so a file dropped anywhere else is invisible to the
tool. Writing to the configured path is what makes an upload mean anything.

**Everything is remembered between sittings** — the batch is read from disk — so the way to start
over is *Clear all — start fresh*, which sets this batch's live inputs aside
(:mod:`lib.batch_reset`). The client's video mapping is never part of that: it outlives batches.

**A row is ticked to keep it.** The previous version of this screen had the opposite verb — a tick
meant *remove this* — so no control here may carry the old wording, and the caption above Next
says what the save will do before it is pressed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nicegui import events, ui

from lib import batch_reset, input_layout, provenance
from lib.config import ClientConfig, ProcessListConfig
from lib.eligibility import eligibility
from lib.errors import ProcessListError
from lib.input_layout import export_archive_path, write_readme
from lib.process_list import ProcessListSheet
from ui import (
    REPO_ROOT,
    batch_grid,
    context,
    process_list_edit,
    progress,
    runner,
    theme,
    video_map_panel,
    video_signoff_panel,
)


@dataclass
class _Session:
    """The one thing about this screen that is genuinely about *this sitting*, and not about disk.

    It used to hold three booleans — export arrived, list arrived, selection saved — and the first
    two gated whether the grid appeared at all. That made the batch **invisible and live at the same
    time**: restarting the shell hid the selection while a run went on consuming it, as the band
    that replaced the grid admitted in so many words.

    So arrival is no longer remembered. What is in force is read from the files, by
    :func:`lib.batch.in_force`, which is what a run reads too — and the risk the old rule was
    reaching for, a batch whose ticks were chosen against a *different* export, is now detected and
    said out loud instead of being hidden behind an absent grid.

    ``saved`` stays, because it is not a fact about disk: it answers "did *I*, in this sitting,
    already choose a batch?", which is what makes "this replaced the selection you saved earlier"
    true or false. A file's timestamp cannot tell you that.
    """

    saved: bool = False


#: One per client, for the life of the process. See :class:`_Session`.
_BATCHES: dict[str, _Session] = {}

#: The rows unticked in step 4, per client, for the life of the process — see :mod:`ui.batch_grid`.
_TICKS: dict[str, batch_grid.Ticks] = {}


def _resolve(path: str) -> Path:
    """A configured path, against the repository root — every path in clients.yml is relative."""
    resolved = Path(path)
    return resolved if resolved.is_absolute() else REPO_ROOT / resolved


def _resolve(path: str) -> Path:
    """A configured path, against the repository root — every path in clients.yml is relative."""
    resolved = Path(path)
    return resolved if resolved.is_absolute() else REPO_ROOT / resolved


def render() -> None:  # noqa: PLR0915 — the wiring: four redraws share one set of containers
    cid = context.client_id()
    cfg = context.client_config(cid)

    with theme.page(
        "Data",
        client_id=cid,
        environment=cfg.gs1.environment if cfg else None,
        facts=context.rail_facts(cid, cfg),
        locked=context.locked_steps(cid, cfg),
    ):
        theme.heading(
            theme.eyebrow("Data"),
            "Data",
            "What a batch is made of, what each product still needs, and which ones are in it.",
        )
        if cfg is None or cid is None:
            theme.blocked(
                "clients.yml did not load, so this screen has nothing to work from.",
                link_label="Open Setup →",
                route="/setup",
            )
            return

        session = _BATCHES.setdefault(cid, _Session())
        ticks = _TICKS.setdefault(cid, batch_grid.Ticks())
        mapping = video_map_panel.session_for(cid, cfg)
        #: The grid's save, hoisted so the Next button can call it. Empty until there is a grid.
        commit: dict[str, Callable[[], bool]] = {}
        ready = False

        theme.jumps(
            [
                ("Uploads", "uploads"),
                ("Coverage", "coverage"),
                ("Choose", "choose"),
                ("Data quality", "data-quality"),
            ]
        )

        def batch_changed(which: str) -> None:
            """A list or an export arrived, or the batch was cleared: redraw from **disk**.

            A new list (or none) renumbers every row, so the remembered unticks go with it.
            """
            nonlocal ready
            if which in {"list", "cleared"}:
                ticks.unticked.clear()
                ticks.seeded = False
            in_force = context.batch_in_force(cfg)
            ready = in_force is not None and in_force.ready
            draw_choose()
            onward.set_enabled(ready)
            report_changed()

        def mapping_changed() -> None:
            """The sign-off sheet was applied: eligibility (a video can unblock a product) and the
            report. Ticks survive — :class:`ui.batch_grid.Ticks` outlives the rebuild."""
            draw_choose()
            report_changed()

        def report_changed() -> None:
            """Rebuild the report — also when a sheet only *arrived*, which changes §1d alone."""
            quality.clear()
            if ready:
                with quality:
                    _quality(cid)

        def draw_choose() -> None:
            selection.clear()
            commit.clear()
            coverage.clear()
            with selection:
                if ready:
                    _choose(cfg, cid, commit, caption, ticks, coverage)
                else:
                    caption.text = ""
                    theme.band(
                        "Upload the selection list and the export (steps 1 and 2) to choose the "
                        "products for this run.",
                        "quiet",
                    )

        signoff_area: list[ui.column] = []
        with ui.element("div").classes("steps-3up").props("id=uploads"):
            _scope_list(cfg, session, batch_changed)
            _export(cfg, cid, batch_changed)
            _signoff(cfg, cid, mapping, mapping_changed, report_changed, signoff_area)
        _clear_all(cfg, cid, session, batch_changed)
        signoff_area.append(ui.column().classes("w-full mt-8"))

        coverage = ui.element("section").classes("section").props("id=coverage")
        selection = ui.column().classes("w-full gap-0")
        quality = ui.column().classes("w-full gap-0")

        def save_and_go() -> None:
            # One intention, one button. On a screen with unsaved work, "go on" and "commit what
            # I chose" are the same act, and two buttons is how the second gets missed.
            save = commit.get("save")
            if save is not None and not save():
                return  # refused, and it said why — stay put rather than carry the refusal away
            # Straight on. There was a four-second beat here so the one-word "Saved" could be
            # read before the page changed, and it read as a click that did nothing — the
            # operator clicked again and took the second click for the one that worked. Content
            # opening is the receipt.
            progress.of(cid).advance("/data")
            ui.navigate.to("/content")

        onward, caption = theme.onward("Next", save_and_go)
        batch_changed("")


# --- Step 4 and the funnel ---------------------------------------------------------


def _choose(  # noqa: PLR0913 — the client, the save, its caption, the ticks, the funnel's box
    cfg: ClientConfig,
    cid: str,
    commit: dict[str, Callable[[], bool]],
    caption: ui.label,
    ticks: batch_grid.Ticks,
    coverage: ui.element,
) -> None:
    """Read the list and the export, decide eligibility once, and build step 4 under it."""
    assert cfg.process_list is not None  # a batch is only ready with a list
    try:
        batch = process_list_edit.read_sheet(cfg.process_list)
        sheet = _uploaded(cfg.process_list, batch)
    except ProcessListError as exc:
        theme.band(str(exc), "danger")
        return
    products = context.load_products(cid)
    named = {sheet.gtin14_at(index) for index in range(len(sheet.rows))}
    # Eligibility over the products *this list* names — the same set ``in_scope`` would give once
    # the list is saved, read from the sheet on screen so a fresh upload is judged at once.
    verdict = eligibility(cfg, [product for product in products if product.gtin14 in named])

    def record(saved: Path, chosen: ProcessListSheet) -> None:
        # Which export these ticks were made against — so a run can say what it was chosen from.
        export = _resolve(cfg.export.path)
        assert cfg.process_list is not None
        provenance.record_selection(
            provenance.history_path(export),
            _resolve(str(saved)),
            product_list=input_layout.archive_path(_resolve(cfg.process_list.path)),
            export=export,
            rows=len(chosen.rows),
        )

    with theme.section(
        "Choose the products and save",
        step=4,
        anchor="choose",
        explain=(
            "Your list, split the way a run will treat it: not in the export, not eligible (and "
            "why), missing a video, and the eligible products — the only ones you choose between. "
            "Your saved batch arrives ticked, or every eligible row for a list just uploaded; "
            "untick a product to leave it out of this batch. Next saves the ticked products — "
            "exactly those, and nothing else, are what the next screens work on. Every save is "
            "kept, dated, under process/selection/. Nothing is published here."
        ),
    ):
        batch_grid.choose(
            sheet,
            products,
            verdict,
            record=record,
            commit=commit,
            caption=caption,
            ticks=ticks,
            counted=lambda counts: batch_grid.draw_funnel(coverage, counts),
            saved=batch.listed_gtins(),
            target=batch.path,
        )


def _uploaded(config: ProcessListConfig, batch: ProcessListSheet) -> ProcessListSheet:
    """The list as it arrived, which the grid shows whatever was saved since.

    The saved batch holds only the ticked rows, so drawing the grid from it would show three rows
    after a restart and lose the rest until the list was uploaded again. The upload is kept beside
    it from the moment it lands; a control file placed by hand has none until its first save, and
    then it *is* the list as it arrived.
    """
    upload = input_layout.archive_path(_resolve(config.path))
    if not upload.is_file():
        return batch
    return process_list_edit.read_sheet(
        ProcessListConfig(path=str(upload), gtin_column=config.gtin_column)
    )


# --- Step 3: the videos ------------------------------------------------------------


def _signoff(  # noqa: PLR0913 — the client, the mapping, what to redraw, and where the review goes
    cfg: ClientConfig,
    cid: str,
    mapping: video_map_panel.MappingSession | None,
    changed: Callable[[], None],
    archived: Callable[[], None],
    plan_into: list[ui.column],
) -> None:
    """Step 3, the upload only — its review renders full width below the row (``plan_into``)."""
    if mapping is None:
        with theme.section("The client's video sign-off sheet", step=3, anchor="video-signoff"):
            ui.label(
                "No `media.video_map_path` in clients.yml — this client attaches no videos, so "
                "there is nothing to sign off."
            ).classes("note")
        return
    video_signoff_panel.render(
        cfg, cid, mapping, applied=changed, archived=archived, step=3, plan_into=plan_into
    )


# --- Clear all ---------------------------------------------------------------------


def _clear_all(
    cfg: ClientConfig, cid: str, session: _Session, cleared: Callable[[str], None]
) -> None:
    """*Clear all — start fresh*, behind a confirmation that says exactly what moves.

    Everything on this screen is remembered between sittings, because it is read from disk — so
    starting over needs a way to set the batch aside. This moves the live list, selection, export
    and parsed products into ``superseded/cleared-{stamp}/`` (:mod:`lib.batch_reset`); nothing is
    deleted, and the client's video mapping is never touched.
    """
    if cfg.process_list is None:
        return
    process_list = cfg.process_list

    def clear() -> None:
        dialog.close()
        try:
            moved = batch_reset.clear_batch(
                export=_resolve(cfg.export.path),
                selection=_resolve(process_list.path),
                products=REPO_ROOT / "output" / cid / "data" / "products.json",
                stamp=datetime.now(UTC).strftime("%Y%m%dT%H%M%S"),
            )
        except (OSError, ValueError) as exc:
            theme.notify_problem(f"Nothing was cleared: {exc}")
            return
        session.saved = False
        cleared("cleared")
        if not moved:
            theme.notify_ok("There was nothing to clear.")
            return
        theme.announce(
            "Cleared — start fresh",
            f"{len(moved)} file(s) set aside in {moved[0].parent.relative_to(REPO_ROOT)}. Upload "
            "the selection list and the export to begin a new batch. The video mapping is as it "
            "was.",
        )

    with ui.dialog() as dialog, ui.element("div").classes("card dialog-card"):
        ui.label("Clear this batch and start fresh?").classes("section-head")
        ui.label(
            "The selection list, the saved selection, the GS1 export and the products read from "
            "it are moved to input/…/superseded/cleared-<date>/ — nothing is deleted, and every "
            "dated copy stays where it is. The video mapping, the client's sign-off sheets, "
            "generated copy, run history and everything live are not touched."
        ).classes("note mt-2")
        with ui.row().classes("gap-3 mt-4"):
            theme.quiet_action("Cancel", dialog.close)
            theme.action("Clear all", clear)

    with ui.row().classes("items-center gap-3 mt-4"):
        theme.quiet_action("Clear all — start fresh", dialog.open)
        ui.label(
            "Everything here is remembered between sittings. This sets the batch aside so the "
            "screen is empty again."
        ).classes("note")


# --- Step 2: the export -------------------------------------------------------


def _keep_upload(target: Path, data: bytes) -> Path | None:
    """Keep this export upload forever, dated, beside the live file. Returns where, or ``None``.

    ``process/uploads/GS1 export/export-{stamp}.xlsx``, named by
    :func:`lib.input_layout.export_archive_path` rather than here — this had its own copy of the
    naming and its own collision loop, which is four places that spell one layout and the reason
    two of them were wrong for a week.

    Written on the way *in* — the moment the file has proved readable — rather than when the next
    upload displaces it. Archiving on replacement means a file uploaded once and never replaced has
    no dated copy at all, which is the ordinary case for a quarterly export.

    Best-effort, and now actually so: this said "best-effort" while letting an ``OSError`` out into
    ``theme.upload``, which shows the exception and re-raises — so an upload that had already
    landed and parsed would report as failed because a *copy* of it could not be filed.

    ``None`` is not swallowed: the caller says so in the sentence it leaves on screen. There is no
    logger anywhere under ``ui/``, and the handler's return value *is* this screen's way of
    reporting — a failure nobody is told about would leave the operator believing they have a dated
    copy of an export they do not.
    """
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        kept = export_archive_path(target, datetime.now(UTC).strftime("%Y%m%dT%H%M%S"))
        kept.write_bytes(data)
    except OSError:
        return None
    return kept


def _export(cfg: Any, cid: str, arrived: Callable[[str], None]) -> None:
    target = _resolve(cfg.export.path)

    with theme.section(
        "Upload the GS1 export",
        step=2,
        anchor="export",
        explain=(
            f"The product data itself, straight from GS1 Data Source. Uploading replaces "
            f"{cfg.export.path} in place and keeps the previous file beside it. That path is fixed "
            "in clients.yml and has no command-line override, so a workbook saved anywhere else is "
            "invisible to the tool — the single most common way a run quietly uses last quarter's "
            "data. The file is read as soon as it arrives: if it is not a GS1 Data Source export, "
            "or an attribute the pipeline needs is absent, it is put back and nothing changes."
        ),
    ):
        problems = ui.log().classes("console mt-3").style("display:none")

        # Async because NiceGUI 3 reads an upload through awaitable methods on ``event.file``. The
        # 2.x form — a synchronous ``event.content.read()`` — raised AttributeError *inside* the
        # handler, where NiceGUI logs it and the browser still shows a completed upload: it wrote
        # nothing while looking exactly like success.
        async def receive(event: events.UploadEventArguments) -> str:
            problems.style("display:none")
            target.parent.mkdir(parents=True, exist_ok=True)
            # Held in memory rather than written to a ``.bak``: it exists only to put the previous
            # export back if this one will not parse, which is a rollback and not an archive. The
            # archive is the dated copy below, written only once the file has proved readable —
            # an unreadable upload is not one of the operator's originals, it is a mistake.
            previous = target.read_bytes() if target.exists() else None
            await event.file.save(target)

            # Reading it *is* the check. There was a "check it" button and a "read it" button, and
            # the second was the one that mattered, so the first was a step an operator could skip
            # into a run built on a file nobody had opened.
            result = await runner.run_off_the_loop(runner.parse_export_argv(cid))
            if result.ok:
                kept = _keep_upload(target, target.read_bytes())
                # What the operator called it, before the upload renamed it to export.xlsx. Without
                # this, "which export did that run use?" can only ever be answered with a timestamp.
                provenance.record_upload(
                    provenance.history_path(target),
                    "export",
                    kept=kept,
                    given_name=event.file.name,
                    rows=context.product_count(cid),
                )
                write_readme(cfg)
                # The selection below is a join against this export, so it now describes a
                # different one — and this is the upload that unlocks it.
                arrived("export")
                theme.notify_ok("Export read.")
                read = f"{context.product_count(cid) or 0} products read from this export."
                if kept is None:
                    return f"{read} A dated copy of it could not be written beside it."
                return read

            # Put the old one back. A failed read that leaves the bad file in place would mean the
            # next screen describes a workbook nobody can use, with no way back but a re-upload of
            # a file the operator may no longer have.
            if previous is not None:
                target.write_bytes(previous)
            problems.style("display:block")
            problems.clear()
            problems.push(result.stderr or result.stdout or "(no output)")
            theme.notify_problem(
                "That file could not be read as a GS1 Data Source export, so it was put back. "
                "The reason is on screen."
            )
            return "Not read — the previous export is still in place."

        theme.upload(
            "GS1 Data Source export (.xlsx)",
            receive,
            busy="Reading the export — this can take a moment…",
        )


# --- Step 1: the selection list --------------------------------------------------
#
# Named "Process list" on screen until two renames ago, which is the config key. It is a
# **selection**: which of the exported products this batch touches.


def _scope_list(cfg: Any, session: _Session, arrived: Callable[[str], None]) -> None:
    if cfg.process_list is None:
        with theme.section("Choose the products for this batch", step=1, anchor="selection-list"):
            ui.label(
                "No `process_list` block in clients.yml — every product in the export is planned."
            ).classes("note")
        return

    control = _resolve(cfg.process_list.path)

    with theme.section(
        "Upload the product selection list",
        step=1,
        anchor="selection-list",
        explain=(
            "A spreadsheet of the barcodes this batch may touch. Being on the list is the whole "
            "meaning — the tool reads no other column and interprets no cell value — so you "
            "prepare a batch by ticking rows below, and your own columns are kept exactly as they "
            f"are. It is saved as {cfg.process_list.path}, and your upload is kept under "
            f"uploads/ as {process_list_edit.archive_path(control).name}. Every run copies that "
            "file into its own folder, which is what lets the result sheet afterwards name the "
            "rows you dropped as dropped rather than leaving them out. "
            "That path is fixed in clients.yml and has no command-line override, so a list saved "
            "anywhere else is invisible to the tool. Until the export (step 2) arrives, nothing "
            "can be matched, so step 4 waits for both."
        ),
    ):
        # Async because NiceGUI 3 reads an upload through awaitable methods on ``event.file`` —
        # see the export upload above for what the 2.x form did instead.
        async def receive(event: events.UploadEventArguments) -> str:
            # Read before it is installed, with the run's own reader, so a file that would fail on
            # Preflight is refused while the operator is still looking at the picker and the list
            # they were working from is untouched.
            data = await event.file.read()
            kept = process_list_edit.archive(cfg.process_list, data)
            provenance.record_upload(
                provenance.history_path(_resolve(cfg.export.path)),
                "product-list",
                # The dated copy, not the stable name: a selection months from now points at the
                # document that will still be there, and ``product-list.xlsx`` is replaced by the
                # next upload.
                kept=next(iter(input_layout.list_archives(control)), None),
                given_name=event.file.name,
                rows=len(process_list_edit.read_sheet(cfg.process_list).rows),
            )
            # Redrawn rather than left for the operator to reload: the tables below now describe
            # the file that was just replaced, and a screen that keeps showing the previous list
            # after a successful upload is the silent staleness this project designs against.
            write_readme(cfg)
            replaced, session.saved = session.saved, False
            arrived("list")
            theme.notify_ok("Selection list installed.")
            if replaced:
                return "Installed — this replaced the selection you saved earlier."
            return f"Installed. Your upload is kept as {kept.name}."

        theme.upload("Product selection list (.xlsx)", receive, busy="Checking the list…")


# --- Quality ------------------------------------------------------------------


def _quality(cid: str) -> None:
    """The worklist, built every visit and folded away until it is wanted.

    It used to be a button. That made a fresh report something the operator had to think of asking
    for, and the thing they were most likely to skip on the visit where it mattered — so it now
    rebuilds on every load, from the files a run has just written.

    **Collapsed, and built after the page paints.** The command takes about half a second, which is
    half a second of blank screen if it runs during the render, on a screen nobody opened to read a
    report. `ui.timer(once=True)` puts it just after the first paint instead, so the cost lands
    somewhere nobody is waiting.
    """
    report = REPO_ROOT / "output" / cid / "data-quality-report.md"

    with theme.section(
        "Data quality",
        collapsed=True,
        tight=True,
        anchor="data-quality",
        explain=(
            "What is blank or wrong in the export itself. Those values get fixed in MyGS1, at the "
            "source — never invented here — so this report is the work list to send upstream. It "
            "is rebuilt every time this screen opens."
        ),
    ):
        stamp = ui.label("Building…").classes("mono")
        body = ui.column().classes("w-full mt-4")

        # Async, and the subprocess runs off the event loop, for the reason
        # `runner.run_off_the_loop` gives: a blocking call holds the loop until the command has
        # already finished, so anything queued before it reaches the browser too late to matter.
        async def build() -> None:
            result = await runner.run_off_the_loop(runner.report_quality_argv(cid))
            body.clear()
            with body:
                if not result.ok or not report.is_file():
                    stamp.text = "The report could not be built."
                    theme.band(result.stderr or "The report could not be built.", "warn")
                    return
                # Dated from the file that was just written, so a command that succeeded without
                # writing anything new cannot leave last week's worklist looking like this week's.
                stamp.text = (
                    f"{report.relative_to(REPO_ROOT)} — built {context.file_fact(report).age}"
                )
                ui.markdown(report.read_text(encoding="utf-8")).classes("prose max-w-none")

        ui.timer(0.1, build, once=True)
