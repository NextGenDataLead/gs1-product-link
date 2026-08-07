"""Screen 4 — the generated copy, which was written somewhere else.

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

from pathlib import Path
from typing import Any

from nicegui import events, ui

from ui import REPO_ROOT, context, runner, theme


def render() -> None:
    cid = context.client_id()
    cfg = context.client_config(cid)

    with theme.page("Content", client_id=cid, environment=cfg.gs1.environment if cfg else None):
        theme.heading(
            "Step 4",
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
        _import(cache_path)
        _coverage(cid)
        _review(cache_path, cfg.wordpress.languages)


def _import(cache_path: Path) -> None:
    with theme.section("Import"):
        ui.label(
            "Generation runs on the maintainer's machine, in a Claude Code session with the "
            "content-generator skill. That keeps this machine free of an API key and of any "
            "connection to Anthropic — and it is why the file arrives by hand."
        ).classes("note")

        def upload(event: events.UploadEventArguments) -> None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            if cache_path.exists():
                cache_path.with_suffix(".bak.json").write_bytes(cache_path.read_bytes())
            cache_path.write_bytes(event.content.read())
            ui.notify("Cache imported. Re-check coverage below.", type="positive")

        ui.upload(on_upload=upload, auto_upload=True, max_files=1).props(
            'accept=".json" flat bordered'
        ).classes("w-full max-w-xl")

        fact = context.file_fact(cache_path)
        ui.label(
            f"{cache_path.relative_to(REPO_ROOT)} — {fact.age}"
            if fact.exists
            else "No cache imported yet."
        ).classes("mono mt-2")


def _coverage(cid: str) -> None:
    with theme.section("Coverage against the current export"):

        def check() -> None:
            payload, result = runner.run_json(runner.doctor_argv(cid, offline=True))
            body.clear()
            with body:
                entry = _find(payload, "cache_coverage")
                if entry is None:
                    theme.band(result.stderr or "Could not read the coverage check.", "warn")
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

        theme.quiet_action("Re-check against the current export", check)
        body = ui.column().classes("w-full mt-4")
        check()


def _review(cache_path: Path, languages: list[str]) -> None:
    with theme.section("Review the copy"):
        ui.label(
            "The second gate on this text is the plan, and execution is draft-first — but this is "
            "the last place it is read as text rather than as a count. Check it against the real "
            "product: this pipeline fails silently, and an 'ingested N' figure proves only that "
            "N things were stored."
        ).classes("note")

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

        ui.label(f"{len(entries)} GTIN(s) in the cache").classes("note mb-3")
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


def _find(payload: Any, name: str) -> dict[str, Any] | None:
    if not isinstance(payload, list):
        return None
    return next((entry for entry in payload if entry.get("name") == name), None)
