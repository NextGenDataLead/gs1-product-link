"""Screen 2 — the export, the process list, and what the data quality report says.

The recurring loop starts here: drop a new export, prune the process list, look at what the parse
found. Two deliberate constraints:

**The upload goes to the configured ``export.path``, never to a new path.** ``parse_export`` has no
input-path override, so a file dropped anywhere else is invisible to the tool — the single most
common novice failure. Writing to the configured path is what makes the upload mean anything, and
it is also what gate 0's cross-check is guarding.

**Pruning the process list is a deletion, so the previous version is kept.** There is no undo in a
web form, and losing a pruned list means redoing the pruning.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nicegui import events, ui

from lib.errors import ProcessListError, VideoMapError
from lib.media_video import list_video_files, load_video_map, summarize_video_map
from ui import REPO_ROOT, context, process_list_edit, runner, theme


def _resolve(path: str) -> Path:
    """A configured path, against the repository root — every path in clients.yml is relative."""
    resolved = Path(path)
    return resolved if resolved.is_absolute() else REPO_ROOT / resolved


def render() -> None:
    cid = context.client_id()
    cfg = context.client_config(cid)

    with theme.page("Data", client_id=cid, environment=cfg.gs1.environment if cfg else None):
        theme.heading(
            theme.step("Data"),
            "Data",
            "The product export and the list of GTINs this run may touch.",
        )
        if cfg is None or cid is None:
            theme.band("clients.yml did not load. Fix that on the Setup screen first.", "danger")
            return

        _export(cfg, cid)
        _process_list(cfg)
        _video_map(cfg)
        _quality(cid)


# --- Export -------------------------------------------------------------------


def _export(cfg: Any, cid: str) -> None:
    target = Path(cfg.export.path)
    if not target.is_absolute():
        target = REPO_ROOT / target

    with theme.section("Product export"):
        fact = context.file_fact(cfg.export.path)
        with ui.row().classes("gap-12 items-end mb-4"):
            theme.figure(str(context.product_count(cid) or 0), "products parsed")
            theme.figure(fact.age, "export modified")

        ui.label(
            f"Uploading replaces {cfg.export.path} in place. That path is fixed in clients.yml and "
            "has no command-line override, so a workbook saved anywhere else is invisible to the "
            "tool — this is the single most common way a run silently uses last quarter's data."
        ).classes("note")

        # Async because NiceGUI 3 reads an upload through awaitable methods on ``event.file``.
        # The 2.x form — a synchronous ``event.content.read()`` — raises AttributeError *inside*
        # the handler, where NiceGUI logs it and the browser still shows a completed upload. That
        # failure wrote nothing while looking exactly like success, which is the one outcome this
        # project refuses everywhere else.
        async def upload(event: events.UploadEventArguments) -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                target.with_suffix(f".bak{target.suffix}").write_bytes(target.read_bytes())
            await event.file.save(target)
            ui.notify(f"Saved to {cfg.export.path} (previous kept as .bak)", type="positive")

        ui.upload(on_upload=upload, auto_upload=True, max_files=1).props(
            'accept=".xlsx" flat bordered'
        ).classes("w-full max-w-xl")

        output = ui.log().classes("console mt-4").style("display:none")

        # Async, and the subprocess runs off the event loop. Both halves are needed, for the
        # reason `runner.run_off_the_loop` gives: a blocking call in a sync handler holds the
        # loop until the command has already finished, so the running-state the button paints
        # arrives at the browser only once there is nothing left to report.
        async def parse(*, dry_run: bool) -> None:
            argv = runner.parse_export_argv(cid, dry_run=dry_run)
            output.style("display:block")
            output.clear()
            result = await runner.run_off_the_loop(argv)
            output.push(result.display_command)
            output.push(result.stderr or result.stdout or "(no output)")
            ui.notify(
                "Parse finished with errors" if not result.ok else "Parse clean",
                type="warning" if not result.ok else "positive",
            )

        with ui.row().classes("gap-3 mt-4"):
            theme.quiet_action("Check the parse (writes nothing)", lambda: parse(dry_run=True))
            theme.action("Parse and save products.json", lambda: parse(dry_run=False))


# --- Process list -------------------------------------------------------------


def _process_list(cfg: Any) -> None:
    if cfg.process_list is None:
        with theme.section("Process list"):
            ui.label("No `process_list` block — every product in the export is planned.").classes(
                "note"
            )
        return

    with theme.section("Process list"):
        ui.label(
            "Every GTIN in this file is processed. The tool reads no other column and interprets "
            "no cell value: being on the list is the whole meaning, so preparing a batch means "
            "deleting the rows that should not run. Your other columns are kept untouched."
        ).classes("note")

        try:
            sheet = process_list_edit.read_sheet(cfg.process_list)
        except ProcessListError as exc:
            theme.band(str(exc), "danger")
            return

        state: dict[str, Any] = {"sheet": sheet}
        rows = [
            {"_row": n, **{col: value for col, value in zip(sheet.header, row, strict=False)}}
            for n, row in enumerate(sheet.rows)
        ]
        columns = [
            {"name": col, "label": col, "field": col, "align": "left"} for col in sheet.header
        ]
        table = ui.table(columns=columns, rows=rows, row_key="_row", selection="multiple").classes(
            "w-full"
        )
        count = ui.label(f"{len(rows)} GTIN(s) will be processed").classes("note mt-2")

        def remove() -> None:
            selected = {int(row["_row"]) for row in table.selected}
            if not selected:
                ui.notify("Select the rows to remove first", type="warning")
                return
            table.rows = [row for row in table.rows if int(row["_row"]) not in selected]
            table.selected = []
            table.update()
            # Rebuilt from the surviving `_row` keys against the sheet as first read, never by
            # applying each removal to the previous result. `_row` is fixed when the grid is
            # built; a sheet renumbers on every edit, so accumulating removals drifts from the
            # second one onward — and drifts silently, saving rows other than the ones on screen.
            state["sheet"] = sheet.keeping({int(row["_row"]) for row in table.rows})
            count.text = f"{len(table.rows)} GTIN(s) will be processed — not saved yet"

        def save() -> None:
            try:
                backup = process_list_edit.save_sheet(state["sheet"])
            except ProcessListError as exc:
                ui.notify(str(exc), type="negative", timeout=10000)
                return
            ui.notify(f"Saved. Previous version kept at {backup.name}", type="positive")
            count.text = f"{len(table.rows)} GTIN(s) will be processed"

        with ui.row().classes("gap-3 mt-3"):
            theme.quiet_action("Remove selected rows", remove)
            theme.action("Save the list", save, danger=True)


# --- Video mapping --------------------------------------------------------------


def _video_map(cfg: Any) -> None:
    """A summary and a way in. The editor itself is its own screen — the file has 166 rows."""
    if cfg.media is None or not cfg.media.video_map_path:
        return

    with theme.section("Video mapping"):
        try:
            summary = summarize_video_map(
                load_video_map(_resolve(cfg.media.video_map_path)),
                {
                    language: [p.name for p in list_video_files(Path(folder))]
                    for language, folder in cfg.media.video_folders.items()
                },
                cfg.wordpress.languages,
            )
        except VideoMapError as exc:
            theme.band(str(exc), "danger")
            ui.link("Open the video mapping", "/videos").classes("note")
            return

        with ui.row().classes("gap-12 items-end mb-4"):
            theme.figure(str(summary.confirmed_gtins), "GTIN(s) publishable")
            theme.figure(str(summary.unconfirmed), "row(s) needing a GTIN")

        if cfg.media.restrict_to_mapped_gtins:
            ui.label(
                "media.restrict_to_mapped_gtins is on, so a product without a confirmed video in "
                "every language is out of scope — it never reaches the plan, and the run reports "
                "success having skipped it."
            ).classes("note")

        ui.link("Open the video mapping", "/videos").classes("note")


# --- Quality ------------------------------------------------------------------


def _quality(cid: str) -> None:
    with theme.section("Data quality"):
        ui.label(
            "Blank or wrong source values get fixed in MyGS1, at the source — never invented "
            "here. This report is the work list."
        ).classes("note")

        body = ui.column().classes("w-full mt-4")

        # Async, and the subprocess runs off the event loop. Both halves are needed, for the
        # reason `runner.run_off_the_loop` gives: a blocking call in a sync handler holds the
        # loop until the command has already finished, so the running-state the button paints
        # arrives at the browser only once there is nothing left to report.
        async def show() -> None:
            result = await runner.run_off_the_loop(runner.report_quality_argv(cid))
            body.clear()
            report = REPO_ROOT / "output" / cid / "data-quality-report.md"
            with body:
                if not result.ok or not report.is_file():
                    theme.band(result.stderr or "The report could not be built.", "warn")
                    return
                ui.markdown(report.read_text(encoding="utf-8")).classes("prose max-w-none")

        theme.action("Rebuild and show the report", show)
