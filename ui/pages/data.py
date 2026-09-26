"""Screen 2 — the two files a batch is made of, and what the data quality report says.

A run reads **two** operator files, and this screen is where both arrive:

* the **GS1 Data Source export** — the product data, parsed into ``products.json``;
* the **product selection list** — which barcodes this run may touch.

They are different documents from different places, and confusing them is the most expensive
mistake available here, so each has its own section, its own upload and its own name. The config
key is still ``process_list``: it appears in ``clients.yml``, the schema, the doctor payload and
five call sites, and renaming it would break every install. Only what the operator reads changed.

Three deliberate constraints:

**Both uploads go to the configured path, never to a new one.** Neither ``parse_export`` nor the
scope-list reader has an input-path override, so a file dropped anywhere else is invisible to the
tool — the single most common novice failure. Writing to the configured path is what makes an
upload mean anything, and it is also what gate 0's cross-check is guarding.

**The scope list is joined against the export before it is shown.** A barcode that is on the list
and carried by no export row produces no error, no plan row and no count anywhere else in the
tool; the operator's only evidence is a number one smaller than they expected. It gets its own
table, above the rest, with its own count.

**A row is selected to keep it.** The previous version of this screen had the opposite verb — a
tick meant *remove this* — so no control here may carry the old wording, the save reports the
delta rather than the end state, and a mis-tick is undone by uploading the list again.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nicegui import events, ui

from lib import input_layout, provenance
from lib.errors import ProcessListError
from lib.input_layout import export_archive_path, write_readme
from lib.preflight import held_for_video, in_scope
from lib.process_list import rows_in_export
from ui import REPO_ROOT, context, process_list_edit, runner, theme


@dataclass
class _Batch:
    """Which of the two files have arrived, and whether a selection has been saved.

    **Module-level, keyed by client, and deliberately not persisted.** The screen rebuilds on every
    visit — NiceGUI runs a ``@ui.page`` function per request — so a local would reset the moment
    the operator stepped to Content and back, and demanding both uploads again for a trip to the
    next screen is not what "every run brings both files" means. A run is a batch, not a page view.

    The boundary is the **process**: restarting the shell starts a fresh batch and both uploads are
    required again. That is the operator's own loop — open it, do a wave, close it — and it needs
    no storage secret, no cookie and no connected client, none of which this shell has today.

    What it gives up is the two-window case, where both windows would share one batch. For a
    loopback native window driven by one person that is not a real configuration, and the wrong
    answer there is mild: a screen that offers to choose products, not a run that inherits a scope.
    """

    export: bool = False
    listed: bool = False
    saved: bool = False


#: One per client, for the life of the process. See :class:`_Batch`.
_BATCHES: dict[str, _Batch] = {}


def _resolve(path: str) -> Path:
    """A configured path, against the repository root — every path in clients.yml is relative."""
    resolved = Path(path)
    return resolved if resolved.is_absolute() else REPO_ROOT / resolved


def render() -> None:
    cid = context.client_id()
    cfg = context.client_config(cid)

    with theme.page(
        "Data",
        client_id=cid,
        environment=cfg.gs1.environment if cfg else None,
        facts=context.rail_facts(cid, cfg),
    ):
        theme.heading(
            theme.eyebrow("Data"),
            "Data",
            "The two files a batch is made of, and which products you want in it.",
        )
        if cfg is None or cid is None:
            theme.blocked(
                "clients.yml did not load, so this screen has nothing to work from.",
                link_label="Open Setup →",
                route="/",
            )
            return

        # **A run brings both files.** Until they have both arrived *this visit*, the selection
        # and the quality report are not shown — not shown empty, not shown stale, not shown at
        # all. A screen that offered a batch built from whatever was left on disk is a screen that
        # lets a run inherit the previous one's scope without anybody deciding to.
        batch = _BATCHES.setdefault(cid, _Batch())
        #: The grid's save, hoisted so the Next button can call it. ``None`` until there is a grid.
        commit: dict[str, Callable[[], bool]] = {}

        def refresh(which: str) -> None:
            if which == "export":
                batch.export = True
            elif which == "list":
                batch.listed = True
            ready = batch.export and batch.listed
            selection.clear()
            quality.clear()
            commit.clear()
            with selection:
                if ready:
                    _scope_grid(cfg, cid, commit, caption, batch)
                else:
                    theme.band(
                        "Upload both files above to choose the products for this run.", "quiet"
                    )
                    if batch.saved:
                        # Only reachable by restarting the shell, since arrival otherwise survives
                        # a trip to another screen. The selection is still on disk and a run would
                        # use it — but uploading the list again replaces it, and that is the part
                        # worth saying before they do it rather than after.
                        theme.band(
                            "Your last selection was saved and a run would use it. Uploading the "
                            "selection list again replaces it with the whole list.",
                            "warn",
                        )
            if ready:
                with quality:
                    _quality(cid)
            else:
                caption.text = ""
            onward.set_enabled(ready)

        with ui.element("div").classes("steps-2up"):
            _export(cfg, cid, refresh)
            _scope_list(cfg, batch, refresh)

        # Bound after the row so they render below it, and before ``refresh`` is ever called.
        selection = ui.column().classes("w-full gap-0")
        quality = ui.column().classes("w-full gap-0")

        def save_and_go() -> None:
            # One intention, one button. On a screen with unsaved work, "go on" and "commit what
            # I chose" are the same act, and two buttons is how the second gets missed.
            save = commit.get("save")
            if save is not None and not save():
                return  # refused, and it said why — stay put rather than carry the refusal away
            # A beat before leaving, because the save reports the **delta** — "2 dropped" — and
            # that sentence is the one thing on this screen that contradicts an operator who still
            # thinks a tick means *remove*. Navigating on the same frame clips the toast, which
            # would leave the write silent on a screen whose tick box means the opposite of what
            # it used to. Notifications do not survive a page change, so the beat is the fix.
            # Disabled for the wait: four seconds of an enabled button that does nothing visible
            # is four seconds in which it gets pressed again.
            onward.disable()
            ui.timer(_TOAST_BEAT, lambda: ui.navigate.to("/content"), once=True)

        onward, caption = theme.onward("Next", save_and_go)
        refresh("")


# --- Step 1: the export -------------------------------------------------------


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
        step=1,
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


# --- Product scope list ---------------------------------------------------------
#
# Named "Process list" on screen until two renames ago, which is the config key; then "product
# list", which is one word away from "the product data" in step 1 — the exact confusion this
# screen is built to prevent. It is a **selection**: which of the exported products this batch
# touches. The config key `process_list` stays (it is in clients.yml, the schema, the doctor
# payload and five call sites); only what the operator reads changes.

#: The row key: a row's position in the sheet as first read. Fixed when the grid is built and
#: never renumbered — see ``ProcessListSheet.keeping`` for what accumulating edits does instead.
_ROW = "_row"

#: The per-row hold mark. A synthetic column, so it is prefixed like the row key to keep it out of
#: the namespace the operator's own headers live in.
_HELD = "_held"

#: How long the success message stands before the screen changes under it. A notification does not
#: survive a page change, so this — not ``theme.notify_ok``'s own timeout — is how long it is
#: actually on screen. It was 1.6s, chosen to make the toast *appear*; nobody checked it was long
#: enough to *read*, and it was not. The message is one word now and the wait is four seconds, so
#: the two agree. What the save will do is said before the click, in the button's caption.
_TOAST_BEAT = 4.0

#: Height of the matched table. Long enough to work in, short enough that Save stays on screen.
_TABLE_HEIGHT = "55vh"


def _scope_list(cfg: Any, batch: _Batch, arrived: Callable[[str], None]) -> None:
    if cfg.process_list is None:
        with theme.section("Choose the products for this batch", step=2):
            ui.label(
                "No `process_list` block in clients.yml — every product in the export is planned."
            ).classes("note")
        return

    control = _resolve(cfg.process_list.path)

    with theme.section(
        "Upload the product selection list",
        step=2,
        explain=(
            "A spreadsheet of the barcodes this batch may touch. Being on the list is the whole "
            "meaning — the tool reads no other column and interprets no cell value — so you "
            "prepare a batch by ticking rows below, and your own columns are kept exactly as they "
            f"are. It is saved as {cfg.process_list.path}, and your upload is kept beside it as "
            f"{process_list_edit.archive_path(control).name}, which is what the per-run result "
            "sheet reads to name the rows you dropped. "
            "That path is fixed in clients.yml and has no command-line override, so a list saved "
            "anywhere else is invisible to the tool."
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
            replaced, batch.saved = batch.saved, False
            arrived("list")
            theme.notify_ok("Selection list installed.")
            if replaced:
                return "Installed — this replaced the selection you saved earlier."
            return f"Installed. Your upload is kept as {kept.name}."

        theme.upload("Product selection list (.xlsx)", receive, busy="Checking the list…")

        def restore() -> None:
            """Put the operator's own upload back as the list, ticks and all."""
            try:
                rows = process_list_edit.restore_from_upload(cfg.process_list)
            except ProcessListError as exc:
                theme.announce("Nothing to restore", str(exc), kind="warn")
                return
            batch.saved = False
            arrived("list")
            theme.announce(
                "Back to your original list",
                f"All {rows} row(s) from the file you uploaded are back and ticked. Every "
                f"selection you saved is still dated under selection/selections/.",
            )

        theme.quiet_action("Start again from my uploaded file", restore)
        ui.label(
            "Ticked too many rows off? This puts your own upload back exactly as you sent it — "
            "no need to find the file again."
        ).classes("note mt-2")


def _scope_grid(
    cfg: Any, cid: str, commit: dict[str, Callable[[], bool]], caption: ui.label, batch: _Batch
) -> None:
    """The list joined against the export: what is missing above, what will run below."""
    try:
        sheet = process_list_edit.read_sheet(cfg.process_list)
    except ProcessListError as exc:
        theme.band(str(exc), "danger")
        return

    products = context.load_products(cid)
    # ``product.gtin14`` against the sheet's own normalisation, which is the exact pair
    # ``lib.preflight.in_scope`` joins on. A third opinion about what makes two barcodes equal
    # would report every good product as missing, and read as bad data rather than as a bug.
    matched, unmatched = rows_in_export(sheet, {product.gtin14 for product in products})
    held = {product.gtin14 for product in held_for_video(cfg, in_scope(cfg, products))}

    if not products:
        # Nothing has been read, so the join is not a finding — it is the absence of one. Every
        # row would land in "not in the export", which is both useless and would leave the screen
        # with no checkboxes at all: the operator could no longer choose a batch before the export
        # arrives, which they have always been able to do.
        #
        # "read", not "parsed". The word left the screen with the two buttons; a band is the worst
        # place for the one survivor, since it is read by somebody who has just hit a problem.
        theme.band(
            "No GS1 export has been read yet, so no row can be matched against one. Upload it in "
            "step 1; until then this is simply the whole list.",
            "warn",
        )
        matched, unmatched = list(range(len(sheet.rows))), []

    columns = [
        # Positional field names. The operator's headers are their own text: two may be the same
        # word and one may be blank, and either collapses a keyed-by-label row into fewer cells
        # than the file has.
        {"name": f"c{n}", "label": name or "—", "field": f"c{n}", "align": "left", "sortable": True}
        for n, name in enumerate(sheet.header)
    ]

    def row_of(index: int) -> dict[str, Any]:
        cells = {f"c{n}": value for n, value in enumerate(sheet.rows[index])}
        gtin = sheet.gtin14_at(index)
        return {_ROW: index, **cells, _HELD: "no video yet" if gtin in held else ""}

    with theme.section(
        "Choose the products and save",
        step=3,
        explain=(
            "Every row arrives ticked, and a run processes the ticked ones. Untick a product to "
            "leave it out of this batch. Next saves your choice and moves on — there is no "
            "separate save button. The filter changes only what you can see, "
            "never what is ticked, so you can search, untick, clear the filter, and nothing you "
            "did is lost. Saving keeps the previous version beside the file as .bak.xlsx, and if "
            "the ticks come out wrong the way back is to upload the list again — every batch "
            "starts "
            "with both "
            "files anyway. Nothing is "
            "published here; this only settles which products are in the batch. A product with no "
            "client-confirmed video in every language is marked in the Video column and a run "
            "skips it, reporting success (media.restrict_to_mapped_gtins)."
        ),
    ):
        _missing_table(columns, [row_of(n) for n in unmatched])
        below = _scope_table(columns, [row_of(n) for n in matched])

        def describe() -> None:
            caption.text = _save_line(len(below.selected) + len(unmatched), len(sheet.rows))

        below.on_select(describe)
        describe()

        def save() -> bool:
            # The rows the export has nothing for are kept, always, and are not counted as chosen.
            # They carry no checkbox because the only question this screen asks is "does this
            # run?", and for them the answer is no whatever anyone ticks.
            keep = {int(row[_ROW]) for row in below.selected} | set(unmatched)
            chosen = sheet.keeping(keep)
            try:
                saved = process_list_edit.save_sheet(chosen)
            except ProcessListError as exc:
                theme.notify_problem(str(exc))
                return False
            # Which export these ticks were made against. Recorded here rather than inside
            # ``save_sheet`` because this is the layer that knows the whole batch; that one knows
            # only the sheet it was handed.
            # Every path here goes through ``_resolve``, including the one ``save_sheet`` just
            # returned. ``start.command`` cds to the repository so the two spellings are the same
            # file, but anchoring some of them and not others would put the ledger in one place and
            # the files it describes in another the first time that stopped being true. Resolved,
            # the worst case is a record that is not written — ``describe`` returns None for a file
            # it cannot read — rather than a ledger split across two trees.
            export = _resolve(cfg.export.path)
            provenance.record_selection(
                provenance.history_path(export),
                _resolve(str(saved)),
                product_list=input_layout.archive_path(_resolve(cfg.process_list.path)),
                export=export,
                rows=len(chosen.rows),
            )
            # One word. The numbers are in the caption above the button, where the operator read
            # them *before* pressing it — a receipt racing a page change is the wrong place for a
            # fact somebody has to act on, and the long version of this sentence was unreadable in
            # the time it had. The backup path is not lost: it is in the ⓘ on this step.
            batch.saved = True
            theme.notify_ok("Saved")
            return True

        commit["save"] = save


def _save_line(keep: int, total: int) -> str:
    """What Next will do, said before it is pressed.

    This is the mitigation that survives. The tick box inverted its meaning one release ago — a
    tick used to mean *remove this row* — and an operator with that habit unticks the rows they
    want gone and saves exactly those. "2 dropped" is the sentence that contradicts them, and it
    has to be legible *while they can still change their mind*, not afterwards in a toast that a
    page change is about to destroy.

    Counted the way the save counts: the ticked rows plus the ones the export has nothing for,
    which are kept regardless and carry no checkbox.
    """
    dropped = total - keep
    if not dropped:
        return f"Next saves all {total} row(s) and goes on to the copy."
    return f"Next saves {keep} of {total} row(s) — {dropped} dropped — and goes on to the copy."


def _missing_table(columns: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    """The rows the export has nothing for. Read-only, deliberately — there is no choice to make.

    They are shown *above* the rest and not merely counted, because this is the one fact about a
    scope list that nothing else in the tool reports: a barcode that is listed and not exported
    produces no error, no plan row and no count.

    **No checkboxes.** They had them, and it was wrong twice over. The tick would have meant "keep
    this row in the file" while the identical tick below means "keep it *and* run it" — one control
    answering two questions, in two tables, a few pixels apart. And it made the count beside the
    table read "38 of 38 row(s) will be processed" when 37 was the most any run could touch.

    So these rows are simply kept, every time. Unticking one would not stop it being processed —
    nothing was going to process it — it would only delete the evidence that a barcode on the list
    has no product behind it. That evidence is the whole point of the table.
    """
    if not rows:
        return

    theme.subhead(
        f"Not in the GS1 export ({len(rows)})",
        explain=(
            "The export carries no row for these barcodes, so a run will publish nothing for them "
            "and say nothing about them. Either the product is missing from the export — fix it in "
            "MyGS1 and export again — or the barcode is wrong. They stay in your list either way, "
            "and have no tick box because there is nothing to choose: nothing can process them. To "
            "drop one, remove it in the spreadsheet and upload the list again."
        ),
    )

    table = ui.table(columns=columns, rows=rows, row_key=_ROW, pagination=0).classes("w-full mt-2")
    table.props("dense flat bordered")


class _Selection:
    """The ticked rows, as a set of row keys that filtering never touches.

    **This is the Google Sheets model, not the Excel one.** A tick is a property of a *row*, not of
    what happens to be on screen. Filtering changes the view and nothing else; a bulk tick or
    untick applies to the rows the filter is showing; the running total counts the whole file,
    visible or not. So: 100 ticked, filter to 30, untick those → 70. Or nothing ticked, filter to
    20, tick all → 20.

    Held in Python rather than read off ``table.selected``, because that list only ever holds rows
    the table is currently rendering. Filter a ticked row out of view and it drops out of
    ``selected``; save then, and the operator loses rows they never touched — silently, since the
    count would agree with itself the whole way down.
    """

    def __init__(self, keys: set[int]) -> None:
        self.keys = keys

    def sync_from(self, visible: list[dict[str, Any]], selected: list[dict[str, Any]]) -> None:
        """Fold a table event back in: only the visible rows can have changed."""
        shown = {int(row[_ROW]) for row in visible}
        self.keys = (self.keys - shown) | {int(row[_ROW]) for row in selected}

    def add(self, rows: list[dict[str, Any]]) -> None:
        self.keys |= {int(row[_ROW]) for row in rows}

    def remove(self, rows: list[dict[str, Any]]) -> None:
        self.keys -= {int(row[_ROW]) for row in rows}


#: How many distinct values a column may hold before its filter becomes free text. Below this a
#: picker is better — the operator sees what the column *contains*, which is most of why they
#: filter; above it, a list of 300 barcodes is a worse way to find one than typing four digits.
_PICKER_MAX = 12


def _scope_table(columns: list[dict[str, Any]], rows: list[dict[str, Any]]) -> Any:
    """The rows a run will act on, all ticked, filterable per column.

    One box matching every column was the whole filter. It is still here — it is the fastest way
    to find one barcode and nothing per-column replaces it — but it could not answer the question
    the operator actually has, which is "show me the rows where *this* column says *that*".
    """
    theme.subhead(
        f"In the GS1 export ({len(rows)}) — tick the ones to process",
        explain=(
            "Every row arrives ticked. Untick a product to leave it out of this batch. Filters "
            "change only what you can see, never what is ticked, and the tick buttons act on the "
            "rows the filters are showing — so you can filter to twenty rows, untick all twenty, "
            "clear the filters, and the other eighty are exactly as you left them."
        ),
    )
    selection = _Selection({int(row[_ROW]) for row in rows})
    # Created before the table so they render above it. You filter, then look — controls under the
    # thing they control are read as a footer, and on a table this tall they are off screen.
    filters = ui.row().classes("items-end gap-3 w-full flex-wrap mt-3")
    bulk = ui.row().classes("items-center gap-3 mt-2 mb-1 flex-wrap")
    held_column = {
        "name": _HELD,
        "label": "Video",
        "field": _HELD,
        "align": "left",
        "sortable": True,
    }
    all_columns = [*columns, held_column]
    table = ui.table(
        columns=all_columns,
        rows=list(rows),
        row_key=_ROW,
        selection="multiple",
        # Mandatory, not cosmetic: with pagination on, the header checkbox selects *this page*,
        # and a save would then quietly drop every row the operator never scrolled to.
        pagination=0,
    ).classes("w-full mt-2")
    table.props(f'dense flat bordered virtual-scroll style="height: {_TABLE_HEIGHT}"')

    #: Column field -> the operator's filter for it. Read on every redraw; empty means "no filter".
    per_column: dict[str, Any] = {}
    search: Any = None
    listeners: list[Callable[[], None]] = []

    def visible() -> list[dict[str, Any]]:
        """The rows every active filter admits — all of them, ANDed."""
        text = str(getattr(search, "value", "") or "").strip().lower()
        kept = []
        for row in rows:
            if text and not any(text in str(value).lower() for value in row.values()):
                continue
            if all(_admits(per_column.get(field), row.get(field)) for field in per_column):
                kept.append(row)
        return kept

    def redraw() -> None:
        shown = visible()
        table.rows = shown
        # Rebuilt from the selection, never from what the table was showing a moment ago: the two
        # disagree the instant a filter hides a ticked row, and the table's copy is the lossy one.
        table.selected = [row for row in shown if int(row[_ROW]) in selection.keys]
        table.update()
        for listen in listeners:
            listen()

    def on_table_select() -> None:
        selection.sync_from(visible(), list(table.selected))
        for listen in listeners:
            listen()

    table.on_select(on_table_select)

    with filters:
        search = (
            ui.input(placeholder="Find in any column")
            .props("dense clearable")
            .classes("w-full max-w-xs")
        )
        search.on_value_change(redraw)
        for column in all_columns:
            per_column[column["field"]] = _column_filter(column, rows, redraw)

    with bulk:
        tick = ui.button("Tick all shown", on_click=lambda: (selection.add(visible()), redraw()))
        untick = ui.button(
            "Untick all shown", on_click=lambda: (selection.remove(visible()), redraw())
        )
        for button in (tick, untick):
            button.props("flat dense no-caps")
        shown_label = ui.label("").classes("note")

    def describe_shown() -> None:
        count = len(visible())
        ticked = sum(1 for row in visible() if int(row[_ROW]) in selection.keys)
        shown_label.text = (
            f"{count} of {len(rows)} row(s) shown; {ticked} of those ticked."
            if count != len(rows)
            else f"All {len(rows)} row(s) shown; {ticked} ticked."
        )

    listeners.append(describe_shown)
    redraw()
    return _Grid(selection, rows, listeners)


class _Grid:
    """What the save and the caption need from the table, without reaching into the widget."""

    def __init__(
        self, selection: _Selection, rows: list[dict[str, Any]], listeners: list[Callable[[], None]]
    ) -> None:
        self._selection = selection
        self._rows = rows
        self._listeners = listeners

    @property
    def selected(self) -> list[dict[str, Any]]:
        """Every ticked row in the file — not merely the ticked rows on screen."""
        return [row for row in self._rows if int(row[_ROW]) in self._selection.keys]

    def on_select(self, handler: Callable[[], None]) -> None:
        self._listeners.append(handler)
        handler()


def _column_filter(column: dict[str, Any], rows: list[dict[str, Any]], redraw: Any) -> Any:
    """One column's filter: a value picker when it has few values, free text when it has many.

    A picker is what the operator means by "filter on column D" — they want to see what D
    contains. It stops being that the moment the column is a barcode or a description, where the
    list is as long as the file and typing four characters is faster than finding one entry in
    three hundred.
    """
    field = column["field"]
    values = sorted({str(row.get(field) or "").strip() for row in rows} - {""})
    label = str(column["label"])
    if len(values) <= _PICKER_MAX:
        control = (
            ui.select(values, multiple=True, label=label, clearable=True)
            .props("dense outlined use-chips")
            .classes("min-w-40")
        )
    else:
        control = ui.input(placeholder=label).props("dense clearable outlined").classes("w-40")
    control.on_value_change(redraw)
    return control


def _admits(control: Any, value: Any) -> bool:
    """Whether one column's filter lets a cell through. No filter admits everything."""
    chosen = getattr(control, "value", None)
    if not chosen:
        return True
    cell = str(value or "").strip()
    if isinstance(chosen, list):
        return cell in chosen
    return str(chosen).strip().lower() in cell.lower()


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
