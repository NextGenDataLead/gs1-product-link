"""Screen 2 — the two files a batch is made of, and what the data quality report says.

A run reads **two** operator files, and this screen is where both arrive:

* the **GS1 Data Source export** — the product data, parsed into ``products.json``;
* the **product scope list** — which barcodes this run may touch.

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
delta rather than the end state, and Restore puts the uploaded list back in one click.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from nicegui import events, ui

from lib.errors import ProcessListError
from lib.preflight import held_for_video, in_scope
from lib.process_list import rows_in_export
from ui import REPO_ROOT, context, process_list_edit, runner, theme


def _resolve(path: str) -> Path:
    """A configured path, against the repository root — every path in clients.yml is relative."""
    resolved = Path(path)
    return resolved if resolved.is_absolute() else REPO_ROOT / resolved


def _export_status(count: int | None, export: context.FileFact) -> str:
    """Whether step 1 is done, in one sentence rather than in two numerals.

    The screen used to answer this with ``theme.figure`` twice — "products parsed" beside "export
    modified" — at hero size, which is the size reserved for a number somebody acts on. Neither is:
    they are the *state of a step*, and side by side they read as one fact about one file while
    being two facts about two, with two modification times, either of which can be the stale one.
    """
    if not export.exists:
        return "No export here yet. Upload one to begin."
    if not count:
        return "Uploaded, not read yet — press Read the export."
    return f"{count} products read from this export."


def _export_is_newer(export: context.FileFact, parsed: context.FileFact) -> bool:
    """Whether the export has changed since it was parsed — so the product count is stale.

    False when either file is missing: an unparsed export is already reported as "0 products
    parsed", and a second warning about the same absence would only add noise.
    """
    if export.modified is None or parsed.modified is None:
        return False
    return export.modified > parsed.modified


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
            "Two files, in order: the product data, then the products you want in this batch.",
        )
        if cfg is None or cid is None:
            theme.blocked(
                "clients.yml did not load, so this screen has nothing to work from.",
                link_label="Open Setup →",
                route="/",
            )
            return

        _export(cfg, cid)
        _scope_list(cfg, cid)
        _quality(cid)


# --- Export -------------------------------------------------------------------


def _export(cfg: Any, cid: str) -> None:
    target = Path(cfg.export.path)
    if not target.is_absolute():
        target = REPO_ROOT / target

    fact = context.file_fact(cfg.export.path)
    parsed = context.file_fact(context.output_dir(cid) / "data" / "products.json")

    with theme.section(
        "Upload the GS1 export",
        step=1,
        explain=(
            f"The product data itself, straight from GS1 Data Source. Uploading replaces "
            f"{cfg.export.path} in place and keeps the previous file beside it. That path is fixed "
            "in clients.yml and has no command-line override, so a workbook saved anywhere else is "
            "invisible to the tool — the single most common way a run quietly uses last quarter's "
            "data."
        ),
    ):
        # Two files with two modification times. Uploading without reading the export leaves last
        # quarter's product count standing, and nothing else on the screen would say so. A band,
        # not an ⓘ: something an operator must not miss cannot live behind an affordance they have
        # to discover.
        if _export_is_newer(fact, parsed):
            theme.band(
                f"This export was uploaded {fact.age} and has not been read yet — it was last read "
                f"{parsed.age}. Press *Read the export* below before going on.",
                "warn",
            )

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
            theme.notify_ok(f"Saved to {cfg.export.path} (previous kept as .bak)")

        ui.upload(on_upload=upload, auto_upload=True, max_files=1).props(
            'accept=".xlsx" flat bordered'
        ).classes("w-full max-w-xl")

        # One sentence saying whether this step is done. It replaced two figures — a product count
        # and a file date — set side by side at hero size, which read as one fact about one file
        # and were about two.
        ui.label(_export_status(context.product_count(cid), fact)).classes("note mt-3")

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
            if result.ok:
                theme.notify_ok("Parse clean")
            else:
                theme.notify_warning("Parse finished with errors")

        with ui.row().classes("gap-3 mt-4"):
            theme.quiet_action("Check it first (changes nothing)", lambda: parse(dry_run=True))
            theme.action("Read the export", lambda: parse(dry_run=False))


# --- Product scope list ---------------------------------------------------------
#
# Named "Process list" on screen until now, which is the config key. It is not what the file is
# to the person who maintains it: a list of the products in this batch. The key stays; the words
# the operator reads are the ones that describe their own document.

#: The row key: a row's position in the sheet as first read. Fixed when the grid is built and
#: never renumbered — see ``ProcessListSheet.keeping`` for what accumulating edits does instead.
_ROW = "_row"

#: The per-row hold mark. A synthetic column, so it is prefixed like the row key to keep it out of
#: the namespace the operator's own headers live in.
_HELD = "_held"

#: Height of the matched table. Long enough to work in, short enough that Save stays on screen.
_TABLE_HEIGHT = "55vh"


def _scope_list(cfg: Any, cid: str) -> None:
    if cfg.process_list is None:
        with theme.section("Choose the products for this batch", step=2):
            ui.label(
                "No `process_list` block in clients.yml — every product in the export is planned."
            ).classes("note")
        return

    control = _resolve(cfg.process_list.path)

    with theme.section(
        "Upload the products you want to process",
        step=2,
        explain=(
            "A spreadsheet of the barcodes this batch may touch. Being on the list is the whole "
            "meaning — the tool reads no other column and interprets no cell value — so you "
            "prepare a batch by ticking rows below, and your own columns are kept exactly as they "
            f"are. It is saved as {cfg.process_list.path}, and your upload is kept beside it as "
            f"{process_list_edit.archive_path(control).name}, which is what Restore puts back. "
            "That path is fixed in clients.yml and has no command-line override, so a list saved "
            "anywhere else is invisible to the tool."
        ),
    ):
        # Async because NiceGUI 3 reads an upload through awaitable methods on ``event.file`` —
        # see the export upload above for what the 2.x form did instead.
        async def upload(event: events.UploadEventArguments) -> None:
            try:
                kept = process_list_edit.archive(cfg.process_list, await event.file.read())
            except (OSError, ProcessListError) as exc:
                # Refused before anything was written, so the list they were working from is
                # still there. Saying so is the difference between a rejected file and a lost one.
                theme.notify_problem(str(exc))
                return
            # Redrawn rather than left for the operator to reload: every count and both tables
            # below now describe the file that was just replaced, and a screen that keeps showing
            # the previous list after a successful upload is the silent staleness this project
            # keeps designing against.
            redraw()
            theme.notify_ok(f"Scope list installed. Your upload is kept at {kept.name}.")

        ui.upload(on_upload=upload, auto_upload=True, max_files=1).props(
            'accept=".xlsx" flat bordered'
        ).classes("w-full max-w-xl mt-2")

        # Declared after the upload so the grid renders below it, as on the export section above:
        # the file arrives, then what is in it.
        body = ui.column().classes("w-full gap-0")

        def redraw() -> None:
            body.clear()
            with body:
                _scope_grid(cfg, cid, redraw)

        redraw()


def _scope_grid(cfg: Any, cid: str, redraw: Callable[[], None]) -> None:
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
        # Nothing has been parsed, so the join is not a finding — it is the absence of one. Every
        # row would land in "not in the export", which is both useless and would leave the screen
        # with no checkboxes at all: the operator could no longer choose a batch before parsing,
        # which they have always been able to do.
        theme.band(
            "The GS1 Data Source export has not been parsed yet, so no row can be matched against "
            "it. Parse it above; until then this is simply the whole list.",
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

    _missing_table(columns, [row_of(n) for n in unmatched])
    below, search = _scope_table(columns, [row_of(n) for n in matched])

    # Written out here and not only in the handler, because the handler is async — it may have to
    # ask the browser what the filter is showing — and the first value has to be on the page before
    # there is a browser to ask.
    count = ui.label(_scope_count(len(below.selected), len(matched), None)).classes("note mt-2")

    async def recount() -> None:
        # Asked of the table rather than re-implemented: Quasar's filter matches every visible
        # column, and a second version of that rule here would drift from the one on screen. Only
        # reached once the operator has typed, so the client is connected by then.
        shown = (
            len(await below.get_filtered_sorted_rows()) if (search.value or "").strip() else None
        )
        count.text = _scope_count(len(below.selected), len(matched), shown)

    below.on_select(recount)
    search.on_value_change(recount)
    # After the count, so that "these" refers to the number just given rather than to the table.
    _held_note(cfg, sum(1 for n in matched if sheet.gtin14_at(n) in held))

    def save() -> None:
        # The rows the export has nothing for are kept, always, and are not counted as chosen.
        # They carry no checkbox because the only question this screen asks is "does this run?",
        # and for them the answer is no whatever anyone ticks.
        keep = {int(row[_ROW]) for row in below.selected} | set(unmatched)
        dropped = len(sheet.rows) - len(keep)
        try:
            backup = process_list_edit.save_sheet(sheet.keeping(keep))
        except ProcessListError as exc:
            theme.notify_problem(str(exc))
            return
        # The delta, not the end state. A tick used to mean "remove this row" on this screen, and
        # an operator with that habit deselects the rows they want *gone*; "5 dropped" is the
        # sentence that contradicts them while the previous list is still one click away.
        theme.notify_ok(
            f"Saved {len(keep)} row(s). {dropped} dropped; previous list at {backup.name}"
        )
        redraw()

    def restore() -> None:
        try:
            backup = process_list_edit.restore(cfg.process_list)
        except (OSError, ProcessListError) as exc:
            theme.notify_problem(str(exc))
            return
        theme.notify_ok(f"The uploaded list is back. The list you had is at {backup.name}")
        redraw()

    with ui.row().classes("gap-3 mt-3"):
        theme.action("Save the list", save, danger=True)
        theme.quiet_action("Restore the uploaded list", restore)

    # Named here because this is where the operator is looking at the list, and said as a
    # location rather than offered as a button: the sheet is about one particular run, and a
    # screen that shows no run would have to guess which.
    with ui.row().classes("items-baseline gap-1 mt-3"):
        ui.label("After a run, this list comes back with what happened to each row —").classes(
            "note"
        )
        ui.link("build it on Runs", "/runs").classes("note")


def _scope_count(chosen: int, matched: int, shown: int | None) -> str:
    """The sentence beside the table: how many rows a run will take, out of how many it could.

    ``matched`` is the rows the export carries, **not** the rows in the file. The barcodes above
    are in the file and can never be processed, so counting them here would put a number on screen
    that no run can reach — which is the sort of quiet over-claim this screen exists to remove.

    It exists at all because the header checkbox cannot say this. Quasar's tri-state describes the
    rows the *filter* is showing, so with a filter typed it reads "all selected" over a file where
    most rows are not — and the operator is one click from saving a list they never looked at.
    ``shown`` is appended only while a filter is on, and names what it is: a view, not the count
    that matters.
    """
    text = f"{chosen} of {matched} row(s) will be processed"
    return text if shown is None else f"{text} — {shown} shown"


def _held_note(cfg: Any, held: int) -> None:
    """One line where a whole section used to be.

    The Data screen carried a *Video mapping* block — two figures and a link — which the Video
    mapping screen already shows, and which the per-row "no video yet" mark now says better,
    because it says it against the product it is about. What that block had and a per-row mark
    does not is the **consequence**, so that is what survives: a mark reading "no video yet" does
    not tell you the run will skip the product.
    """
    if not held or cfg.media is None or not cfg.media.restrict_to_mapped_gtins:
        return
    ui.label(
        f"{held} of them have no client-confirmed video in every language, so a run skips those "
        f"and reports success (media.restrict_to_mapped_gtins)."
    ).classes("note")
    ui.link("Open the video mapping", "/videos").classes("note")


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


def _scope_table(
    columns: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> tuple[ui.table, ui.input]:
    """The rows a run will act on, all selected, searchable."""
    theme.subhead(
        f"In the GS1 export ({len(rows)}) — tick the ones to process",
        explain=(
            "Every row arrives ticked. Untick a product to leave it out of this batch, then press "
            "Save the list. Filtering changes only what you can see, never what is ticked."
        ),
    )
    search = (
        ui.input(placeholder="Filter — matches every column")
        .props("dense clearable")
        .classes("w-full max-w-sm")
    )
    held_column = {
        "name": _HELD,
        "label": "Video",
        "field": _HELD,
        "align": "left",
        "sortable": True,
    }
    table = ui.table(
        columns=[*columns, held_column],
        rows=rows,
        row_key=_ROW,
        selection="multiple",
        # Mandatory, not cosmetic: with pagination on, the header checkbox selects *this page*,
        # and a save would then quietly drop every row the operator never scrolled to.
        pagination=0,
    ).classes("w-full mt-2")
    table.props(f'dense flat bordered virtual-scroll style="height: {_TABLE_HEIGHT}"')
    # Independent of the filter in Quasar 2.18, so a selection survives typing in the box. What
    # does not survive is the header checkbox's meaning — its tri-state describes the rows the
    # filter is showing, not the file — which is what the count label beside the table is for.
    table.selected = list(rows)
    table.bind_filter_from(search, "value")
    return table, search


# --- Quality ------------------------------------------------------------------


def _quality(cid: str) -> None:
    with theme.section(
        "Data quality",
        explain=(
            "What is blank or wrong in the export itself. Those values get fixed in MyGS1, at the "
            "source — never invented here — so this report is the work list to send upstream."
        ),
    ):
        report = REPO_ROOT / "output" / cid / "data-quality-report.md"
        fact = context.file_fact(report)
        # Which run's worklist this is. `show()` rebuilds the file and renders it, so a command
        # that succeeded without writing anything new leaves last week's report on screen looking
        # exactly like this week's.
        ui.label(
            f"{report.relative_to(REPO_ROOT)} — built {fact.age}"
            if fact.exists
            else "No report built yet."
        ).classes("mono mt-2")

        body = ui.column().classes("w-full mt-4")

        # Async, and the subprocess runs off the event loop. Both halves are needed, for the
        # reason `runner.run_off_the_loop` gives: a blocking call in a sync handler holds the
        # loop until the command has already finished, so the running-state the button paints
        # arrives at the browser only once there is nothing left to report.
        async def show() -> None:
            result = await runner.run_off_the_loop(runner.report_quality_argv(cid))
            body.clear()
            with body:
                if not result.ok or not report.is_file():
                    theme.band(result.stderr or "The report could not be built.", "warn")
                    return
                ui.markdown(report.read_text(encoding="utf-8")).classes("prose max-w-none")

        theme.action("Rebuild and show the report", show)
