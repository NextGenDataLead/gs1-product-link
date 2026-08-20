"""Screen 3 — the generated copy: written here when there is a key, imported when there is not.

Copy has two producers and one results file. A Claude Code session running the
``content-generator`` skill writes it on the maintainer's machine and hands over
``generation_results.json``; the Anthropic Messages API writes the same file headlessly. This
screen offers whichever is available, then says how much of the current export the file covers and
shows the text per language.

**Which one is offered is decided by the key, not by a setting.** With the client's
``generator.api_key_env`` unset, this machine holds no credential and reaches Anthropic not at
all — the documented arrangement, and still the default. Setting it turns generation on here, so
the shell becomes one access point from dataset to pages rather than a surface with a hole in the
middle of it. Either way the key stays out of this process: generating runs
``scripts.run_generate`` as a subprocess, which loads ``.env`` in its own ``__main__`` block.

**Coverage is the load-bearing part.** Copy is written fresh for each run and never stored, so the
question is not how much has piled up but whether *this* file answers every unit the run will
publish — and whether it still describes this export. Not every in-scope unit: copy is written for
the rows a run creates or changes, so an already-live unchanged unit needs none, and neither does
a product the plan will hold for a missing video or missing mandatory data. Both are excluded from
the count rather than reported as a shortfall, and the check's detail line names each separately —
one of them is finished and the other is waiting on the client. Its fingerprint covers
``{inputs, language, prompt_version}``, so editing one product in the feed, or bumping the prompt
version, leaves that unit uncovered. An uncovered unit with no producer on this machine is an E21
omission: it leaves the plan without a row. Before ``Plan.skipped`` existed it left without a trace
at all, and an empty plan looked exactly like a plan with nothing to do.

So the count is shown before the copy is, and a shortfall is stated as a shortfall.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from nicegui import events, ui

from lib.config import GeneratorConfig
from ui import REPO_ROOT, context, env_edit, runner, theme


def render() -> None:
    cid = context.client_id()
    cfg = context.client_config(cid)

    with theme.page(
        "Content",
        client_id=cid,
        environment=cfg.gs1.environment if cfg else None,
        facts=context.rail_facts(cid, cfg),
    ):
        theme.heading(
            theme.eyebrow("Content"),
            "Content",
            "The tagline and Eigenschappen text, generated elsewhere and reviewed here.",
        )
        if cfg is None or cid is None:
            theme.blocked(
                "clients.yml did not load, so this screen has nothing to work from.",
                link_label="Open Setup →",
                route="/",
            )
            return
        if cfg.generator is None:
            ui.label(
                "This client has no `generator` block, so pages are published from feed copy only "
                "and there is nothing to import."
            ).classes("note")
            return

        results_path = REPO_ROOT / "output" / cid / "data" / "generation_results.json"
        _coverage_and_review(cid, cfg.generator, results_path, list(cfg.wordpress.languages))


def _generate(cid: str, generator: GeneratorConfig, recheck: Callable[[], Awaitable[None]]) -> None:
    """Write this run's copy through the API backend — when a key makes that possible.

    The producer is chosen by whether the configured variable has a value, checked with
    :func:`ui.env_edit.describe`, which reads ``.env`` as text and returns presence and length
    without ever holding the value. No key means no button: an action that can only fail is worse
    than an absence, because the operator has to run it to find out.

    Generating is offered above importing rather than beside it. Both write the same file, and the
    one that needs no hand-off is the one to reach for first.

    This does not lower the bar on what publishes. Copy written here is read below as text, which
    is gate 1 of 2; ``plan.json`` is gate 2. Nothing about a machine-written tagline is more
    trustworthy than a session-written one — the pipeline fails just as silently either way.
    """
    with theme.section("Generate the copy"):
        secret = env_edit.describe([generator.api_key_env])[generator.api_key_env]
        ui.label(
            f"Writes the tagline and Eigenschappen for every unit this run will publish, through "
            f"the Anthropic API — model {generator.model}, voice {generator.prompt_version}. Copy "
            f"is written fresh for each run and never reused, so generating again replaces this "
            f"run's copy rather than adding to it."
        ).classes("note")

        if not secret.present:
            theme.band(
                f"{generator.api_key_env} is not set, so this machine reaches Anthropic not at "
                f"all. Set it on the Setup screen to generate here, or import a file written "
                f"elsewhere below.",
            )
            return

        async def go() -> None:
            argv = runner.run_generate_argv(cid)
            log.style("display:block")
            log.clear()
            log.push(" ".join(["python", *argv]))
            result = await runner.stream(argv, log.push)
            # Re-check for the same reason the upload handler does: the coverage figures and the
            # copy below now describe the file that was just written, and a screen still showing
            # the previous copy after a successful run is the silent staleness this project keeps
            # designing against.
            await recheck()
            if result.ok:
                theme.notify_ok("Copy written — read it below before planning.")
            else:
                theme.notify_warning(f"Generation exited {result.returncode} — read the output.")

        theme.action("Generate copy for this run", go)
        log = ui.log().classes("console mt-4").style("display:none")


def _import(results_path: Path, recheck: Callable[[], Awaitable[None]]) -> None:
    with theme.section("Import"):
        ui.label(
            "The other producer: a Claude Code session running the content-generator skill, on a "
            "machine that has a Claude subscription rather than an API key. It writes the same "
            "file, which arrives by hand. Written fresh for each run either way, so importing a "
            "newer one replaces this run's copy rather than adding to it."
        ).classes("note")

        # Async for the same reason as the export upload — see ui/pages/data.py.
        async def upload(event: events.UploadEventArguments) -> None:
            results_path.parent.mkdir(parents=True, exist_ok=True)
            if results_path.exists():
                results_path.with_suffix(".bak.json").write_bytes(results_path.read_bytes())
            await event.file.save(results_path)
            # Re-check rather than ask them to: both sections below now describe the file that
            # was just replaced, and a screen that keeps showing the previous copy after a
            # successful import is the silent-staleness this project keeps designing against.
            await recheck()
            theme.notify_ok("Copy imported — coverage and text below are for the new file.")

        ui.upload(on_upload=upload, auto_upload=True, max_files=1).props(
            'accept=".json" flat bordered'
        ).classes("w-full max-w-xl")

        fact = context.file_fact(results_path)
        ui.label(
            f"{results_path.relative_to(REPO_ROOT)} — {fact.age}"
            if fact.exists
            else "No copy imported yet."
        ).classes("mono mt-2")


def _coverage_and_review(
    cid: str, generator: GeneratorConfig, results_path: Path, languages: list[str]
) -> None:
    """Import, coverage and the copy itself — all fed by one preflight run.

    They used to be three independent sections, and the middle one was the only one that knew
    what this run covers. Coverage came from the doctor and was correctly scoped; the review read
    the copy file straight off disk and listed **every GTIN in it**. So one screen showed a scoped
    number above an unscoped list with nothing to tell them apart.

    Drawing them together is not tidiness. They answer the same question at two zoom levels, and
    a re-check that moved the count without moving the list would restore exactly the disagreement
    this replaces.
    """
    payload: Any = None
    result: Any = None

    def show(fetched: tuple[Any, runner.CommandResult]) -> None:
        nonlocal payload, result
        payload, result = fetched
        coverage_body.clear()
        review_body.clear()
        with coverage_body:
            _coverage_figures(payload, result)
        with review_body:
            _review(context.scope_from(payload), results_path, languages)

    def first_draw() -> None:
        """The blocking form, called once while the page is still being built.

        A page build is synchronous anyway — there is no rendered button waiting to show that it
        is working — so the quarter-second here costs nothing an operator can see. Every *click*
        goes through ``recheck`` instead, which is the one that must not freeze the screen.
        """
        show(runner.run_json(runner.doctor_argv(cid, offline=True)))

    async def recheck() -> None:
        show(await runner.run_json_off_the_loop(runner.doctor_argv(cid, offline=True)))

    _generate(cid, generator, recheck)
    _import(results_path, recheck)
    with theme.section("Coverage against the current export"):
        theme.quiet_action("Re-check against the current export", recheck)
        coverage_body = ui.column().classes("w-full mt-4")
    with theme.section("Review the copy"):
        ui.label(
            "The second gate on this text is the plan, and execution is draft-first — but this is "
            "the last place it is read as text rather than as a count. Check it against the real "
            "product: this pipeline fails silently, and a 'validated N' figure proves only that "
            "N things were shaped correctly."
        ).classes("note")
        review_body = ui.column().classes("w-full")
    first_draw()


def _coverage_figures(payload: Any, result: Any) -> None:
    entry = context.doctor_check(payload, "generation_results")
    if entry is None:
        theme.band(getattr(result, "stderr", "") or "Could not read the coverage check.", "warn")
        return
    data = entry.get("data") or {}
    with ui.row().classes("gap-12 items-end mb-4"):
        theme.figure(str(data.get("total", "—")), "units to publish")
        theme.figure(str(data.get("covered", "—")), "have copy")
        theme.figure(str(data.get("pending", "—")), "pending")
    if entry["status"] == "ok":
        theme.band(str(entry["detail"]).capitalize() + ".")
    else:
        theme.band(str(entry["detail"]), "danger")
        ui.label(str(entry.get("remedy", ""))).classes("remedy")
        pending = data.get("pending_units") or []
        if pending:
            named = ", ".join(f"{gtin} ({lang})" for gtin, lang in pending)
            ui.label(f"Pending: {named}").classes("mono mt-3 scroll-x")


def _review(scope: context.Scope | None, results_path: Path, languages: list[str]) -> None:
    """The copy for *this run*, with anything written for another scope called out.

    Scope is not recomputed here. It arrives from the doctor's ``scope`` check, whose GTINs are
    ``ProductRecord.gtin`` — the same field the results are keyed by — so the filter is a set
    membership test and not a second opinion about what a run covers.

    A GTIN outside that set used to be ordinary: the cache accumulated every unit ever generated
    on this machine. The file is per-run now, so copy for a GTIN this run will not touch means it
    was written against a different scope — worth saying, not worth hiding.
    """
    import json  # noqa: PLC0415 — only this section needs it

    try:
        data = json.loads(results_path.read_text(encoding="utf-8"))
        entries = context.group_results(data.get("results", []))
    except (OSError, json.JSONDecodeError, AttributeError):
        ui.label("No readable copy to review yet.").classes("note")
        return

    if not entries:
        ui.label("No copy has been written for this run yet.").classes("note")
        return

    split = context.split_results(entries, scope)
    if not split.scoped:
        ui.label(f"{len(entries)} GTIN(s) in this file").classes("note mb-1")
        theme.band(
            "Showing the whole file: the preflight did not report which GTINs are in scope, so "
            "this list is everything it holds and not necessarily this run's batch.",
            "warn",
        )
        _entries(split.in_scope, languages, results_path)
        return

    ui.label(
        f"{len(split.in_scope)} of {len(entries)} GTIN(s) in this file are in scope for this run"
    ).classes("note mb-3")
    if not split.in_scope:
        theme.band(
            "None of this run's GTINs have copy in this file — it was written for a different "
            "batch. The coverage figures above say how many units are uncovered.",
            "warn",
        )
    _entries(split.in_scope, languages, results_path)

    if split.missing:
        ui.label(
            f"{len(split.missing)} in-scope GTIN(s) have no copy at all: "
            + ", ".join(split.missing[:_MAX_NAMED])
            + (
                f" …and {len(split.missing) - _MAX_NAMED} more"
                if len(split.missing) > _MAX_NAMED
                else ""
            )
        ).classes("note mono scroll-x mt-3")

    if split.others:
        theme.band(
            f"{len(split.others)} GTIN(s) in this file are outside this run's scope, so it was "
            "written against a different process list than the one about to run. Confirming the "
            "plan will not publish them, but check the file is the one you meant to import.",
            "warn",
        )
        with ui.expansion(
            f"{len(split.others)} GTIN(s) outside this run's scope", icon="unfold_more"
        ).classes("w-full mt-2"):
            ui.label(", ".join(sorted(split.others)[:_MAX_NAMED])).classes("mono scroll-x")
            if len(split.others) > _MAX_NAMED:
                ui.label(f"…and {len(split.others) - _MAX_NAMED} more.").classes("note")


def _entries(entries: dict[str, Any], languages: list[str], results_path: Path) -> None:
    """Render the copy for each GTIN, one card per product, capped and counted.

    The tagline is ``usps[0]`` and the Eigenschappen bullets are the rest, which is the same
    reading ``_assemble_description`` uses. There is no product name here: the copy contract
    never carried one, and the field this used to render was silently absent on every entry — a
    column of em-dashes that looked like missing data rather than like a bug.
    """
    for gtin, per_language in list(entries.items())[:_MAX_SHOWN]:
        with ui.element("div").classes("card mb-3"):
            ui.label(gtin).classes("mono gate-step")
            with ui.row().classes("gap-8 items-start w-full flex-wrap"):
                for language in languages:
                    entry = per_language.get(language)
                    with ui.column().classes("gap-1 min-w-64 flex-1"):
                        ui.label(language.upper()).classes("figure-label")
                        if entry is None:
                            ui.label("no copy").classes("tag tag-fail")
                            continue
                        usps = entry.get("usps", [])
                        if usps:
                            ui.label(usps[0]).classes("font-medium")
                        for usp in usps[1:]:
                            ui.label(f"• {usp}").classes("note")
    if len(entries) > _MAX_SHOWN:
        ui.label(f"Showing the first {_MAX_SHOWN}. The rest are in {results_path.name}.").classes(
            "note"
        )


_MAX_SHOWN = 25
#: How many GTINs to name in a one-line list before summarising the remainder.
_MAX_NAMED = 20
