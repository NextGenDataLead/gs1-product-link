"""Screen 3 — the generated copy, which was written somewhere else.

This machine has no LLM, no API key and no Anthropic egress. Copy is generated on the maintainer's
machine in a Claude Code session and handed over as ``generated_cache.json``; this screen imports
it, says how much of the current export it covers, and shows the text side by side per language.

**Coverage is the load-bearing part.** A cache entry's fingerprint covers
``{inputs, language, prompt_version}``, so editing one product in the feed — or bumping the prompt
version — makes that unit *pending* again. A pending unit with no producer on this machine is an
E21 omission: it leaves the plan without a row. Before ``Plan.skipped`` existed it left without a
trace at all, and an empty plan looked exactly like a plan with nothing to do.

So the count is shown before the copy is, and a shortfall is stated as a shortfall.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from nicegui import events, ui

from ui import REPO_ROOT, context, runner, theme


def render() -> None:
    cid = context.client_id()
    cfg = context.client_config(cid)

    with theme.page("Content", client_id=cid, environment=cfg.gs1.environment if cfg else None):
        theme.heading(
            theme.step("Content"),
            "Content",
            "The tagline and Eigenschappen text, generated elsewhere and reviewed here.",
        )
        if cfg is None or cid is None:
            theme.band("clients.yml did not load. Fix that on the Setup screen first.", "danger")
            return
        if cfg.generator is None:
            ui.label(
                "This client has no `generator` block, so pages are published from feed copy only "
                "and there is nothing to import."
            ).classes("note")
            return

        cache_path = REPO_ROOT / "output" / cid / "data" / "generated_cache.json"
        _coverage_and_review(cid, cache_path, list(cfg.wordpress.languages))


def _import(cache_path: Path, refresh: Callable[[], None]) -> None:
    with theme.section("Import"):
        ui.label(
            "Generation runs on the maintainer's machine, in a Claude Code session with the "
            "content-generator skill. That keeps this machine free of an API key and of any "
            "connection to Anthropic — and it is why the file arrives by hand."
        ).classes("note")

        # Async for the same reason as the export upload — see ui/pages/data.py.
        async def upload(event: events.UploadEventArguments) -> None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            if cache_path.exists():
                cache_path.with_suffix(".bak.json").write_bytes(cache_path.read_bytes())
            await event.file.save(cache_path)
            # Refresh rather than ask them to: both sections below now describe the file that was
            # just replaced, and a screen that keeps showing the previous cache after a successful
            # import is the silent-staleness this project keeps designing against.
            refresh()
            ui.notify("Cache imported — coverage and copy below are for the new file.", "positive")

        ui.upload(on_upload=upload, auto_upload=True, max_files=1).props(
            'accept=".json" flat bordered'
        ).classes("w-full max-w-xl")

        fact = context.file_fact(cache_path)
        ui.label(
            f"{cache_path.relative_to(REPO_ROOT)} — {fact.age}"
            if fact.exists
            else "No cache imported yet."
        ).classes("mono mt-2")


def _coverage_and_review(cid: str, cache_path: Path, languages: list[str]) -> None:
    """Import, coverage and the copy itself — all fed by one preflight run.

    They used to be three independent sections, and the middle one was the only one that knew
    what this run covers. Coverage came from the doctor and was correctly scoped; the review read
    ``generated_cache.json`` straight off disk and listed **every GTIN in it**, captioned "N
    GTIN(s) in the cache". So one screen showed a scoped number above an unscoped list with
    nothing to tell them apart — and because the cache accumulates every unit ever generated on
    that machine, the gap only widens: a two-product batch eventually sits under a list of
    hundreds.

    Drawing them together is not tidiness. They answer the same question at two zoom levels, and
    a re-check that moved the count without moving the list would restore exactly the disagreement
    this replaces.
    """
    payload: Any = None
    result: Any = None

    def refresh() -> None:
        nonlocal payload, result
        payload, result = runner.run_json(runner.doctor_argv(cid, offline=True))
        coverage_body.clear()
        review_body.clear()
        with coverage_body:
            _coverage_figures(payload, result)
        with review_body:
            _review(context.scope_from(payload), cache_path, languages)

    _import(cache_path, refresh)
    with theme.section("Coverage against the current export"):
        theme.quiet_action("Re-check against the current export", refresh)
        coverage_body = ui.column().classes("w-full mt-4")
    with theme.section("Review the copy"):
        ui.label(
            "The second gate on this text is the plan, and execution is draft-first — but this is "
            "the last place it is read as text rather than as a count. Check it against the real "
            "product: this pipeline fails silently, and an 'ingested N' figure proves only that "
            "N things were stored."
        ).classes("note")
        review_body = ui.column().classes("w-full")
    refresh()


def _coverage_figures(payload: Any, result: Any) -> None:
    entry = context.doctor_check(payload, "cache_coverage")
    if entry is None:
        theme.band(getattr(result, "stderr", "") or "Could not read the coverage check.", "warn")
        return
    data = entry.get("data") or {}
    with ui.row().classes("gap-12 items-end mb-4"):
        theme.figure(str(data.get("total", "—")), "units in scope")
        theme.figure(str(data.get("covered", "—")), "have copy")
        theme.figure(str(data.get("pending", "—")), "pending")
    if entry["status"] == "ok":
        theme.band("Every in-scope unit has copy for this version of the export.")
    else:
        theme.band(str(entry["detail"]), "danger")
        ui.label(str(entry.get("remedy", ""))).classes("remedy")
        pending = data.get("pending_units") or []
        if pending:
            named = ", ".join(f"{gtin} ({lang})" for gtin, lang in pending)
            ui.label(f"Pending: {named}").classes("mono mt-3 scroll-x")


def _review(scope: context.Scope | None, cache_path: Path, languages: list[str]) -> None:
    """The copy for *this run*, with everything else in the cache put behind a fold.

    The cache is a machine-lifetime accumulation: every unit ever generated for this client stays
    in it, and nothing prunes it. Listing it whole under a scoped coverage figure invited the
    reader to check copy for products this run will not touch, and to conclude a batch was larger
    than it is.

    Scope is not recomputed here. It arrives from the doctor's ``scope`` check, whose GTINs are
    ``ProductRecord.gtin`` — the same field the cache is keyed by — so the filter is a set
    membership test and not a second opinion about what a run covers.
    """
    import json  # noqa: PLC0415 — only this section needs it

    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        entries: dict[str, Any] = data.get("entries", {})
    except (OSError, json.JSONDecodeError, AttributeError):
        ui.label("No readable cache to review yet.").classes("note")
        return

    if not entries:
        ui.label("The cache is empty.").classes("note")
        return

    split = context.split_cache(entries, scope)
    if not split.scoped:
        ui.label(f"{len(entries)} GTIN(s) in the cache").classes("note mb-1")
        theme.band(
            "Showing the whole cache: the preflight did not report which GTINs are in scope, so "
            "this list is everything ever generated for this client and not this run's batch.",
            "warn",
        )
        _entries(split.in_scope, languages, cache_path)
        return

    ui.label(
        f"{len(split.in_scope)} of {len(entries)} GTIN(s) in the cache are in scope for this run"
    ).classes("note mb-3")
    if not split.in_scope:
        theme.band(
            "None of this run's GTINs have generated copy yet — everything in the cache belongs "
            "to other batches. The coverage figures above say how many units are pending.",
            "warn",
        )
    _entries(split.in_scope, languages, cache_path)

    if split.missing:
        ui.label(
            f"{len(split.missing)} in-scope GTIN(s) have no cache entry at all: "
            + ", ".join(split.missing[:_MAX_NAMED])
            + (
                f" …and {len(split.missing) - _MAX_NAMED} more"
                if len(split.missing) > _MAX_NAMED
                else ""
            )
        ).classes("note mono scroll-x mt-3")

    if split.others:
        # Folded rather than dropped. They are real entries and a reader who wants them should be
        # able to reach them; what they must not do is pad this run's list.
        with ui.expansion(
            f"{len(split.others)} cache entry(s) outside this run's scope", icon="unfold_more"
        ).classes("w-full mt-4"):
            ui.label(
                "Generated for other batches and kept — nothing prunes this file. They are not "
                "part of this run and confirming the plan will not publish them."
            ).classes("note mb-2")
            ui.label(", ".join(sorted(split.others)[:_MAX_NAMED])).classes("mono scroll-x")
            if len(split.others) > _MAX_NAMED:
                ui.label(f"…and {len(split.others) - _MAX_NAMED} more.").classes("note")


def _entries(entries: dict[str, Any], languages: list[str], cache_path: Path) -> None:
    """Render the copy for each GTIN, one card per product, capped and counted."""
    for gtin, per_language in list(entries.items())[:_MAX_SHOWN]:
        with ui.element("div").classes("gate mb-3"):
            ui.label(gtin).classes("mono gate-step")
            with ui.row().classes("gap-8 items-start w-full flex-wrap"):
                for language in languages:
                    entry = per_language.get(language)
                    with ui.column().classes("gap-1 min-w-64 flex-1"):
                        ui.label(language.upper()).classes("figure-label")
                        if entry is None:
                            ui.label("no copy").classes("tag tag-fail")
                            continue
                        ui.label(entry.get("product_name") or "—").classes("font-medium")
                        for usp in entry.get("usps", []):
                            ui.label(f"• {usp}").classes("note")
    if len(entries) > _MAX_SHOWN:
        ui.label(f"Showing the first {_MAX_SHOWN}. The rest are in {cache_path.name}.").classes(
            "note"
        )


_MAX_SHOWN = 25
#: How many GTINs to name in a one-line list before summarising the remainder.
_MAX_NAMED = 20
