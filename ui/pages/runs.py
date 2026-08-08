"""Screen 6 — what actually ran, per row.

The run log is the only durable account of a publish, and since it is appended row by row as the
run goes, a log that stops mid-way is a run that stopped mid-way. That is the case an operator
most needs to see: live pages and permanent GS1 records may already exist for the rows that
landed, and nothing else on this machine will tell them which.

So a partial log is shown as a partial log, not quietly rendered as a finished one.
"""

from __future__ import annotations

from nicegui import ui

from ui import REPO_ROOT, context, theme


def render() -> None:
    cid = context.client_id()
    cfg = context.client_config(cid)

    with theme.page("Runs", client_id=cid, environment=cfg.gs1.environment if cfg else None):
        theme.heading(
            "Step 6",
            "Runs",
            "Every row of every run, as it was recorded at the time.",
        )
        if cid is None:
            theme.band("clients.yml did not load. Fix that on the Setup screen first.", "danger")
            return

        runs = context.recent_runs(cid)
        if not runs:
            ui.label("No runs yet.").classes("note")
            return

        for run in runs:
            _run(run)


def _run(run: context.RunLog) -> None:
    with ui.element("div").classes("gate mb-4"):
        try:
            name = str(run.path.relative_to(REPO_ROOT))
        except ValueError:
            name = str(run.path)
        ui.label(name).classes("gate-step scroll-x")
        when = run.modified.strftime("%Y-%m-%d %H:%M UTC") if run.modified else "unknown time"
        ui.label(("Dry run · " if run.dry_run else "") + when).classes("gate-title")

        with ui.row().classes("gap-12 items-end my-3"):
            theme.figure(str(run.ok), "ok")
            theme.figure(str(run.errors), "error")
            theme.figure(str(len(run.outcomes)), "rows recorded")

        if run.unreadable_lines:
            theme.band(
                f"{run.unreadable_lines} line(s) could not be read — the last one is usually "
                "truncated when a run was killed mid-write. The rows above are what it managed "
                "to record before it stopped.",
                "warn",
            )

        errors = [o for o in run.outcomes if o.status == "error"]
        if errors:
            with ui.element("div").classes("mt-2"):
                for outcome in errors[:_MAX_ERRORS]:
                    ui.label(f"{outcome.gtin} ({outcome.language}): {outcome.error}").classes(
                        "note mono scroll-x"
                    )
            ui.label(
                "In a links-only run, 'refusing to point a permanent GS1 record at it' means the "
                "target URL did not serve — the page is not where the plan thinks it is. That is "
                "not a GS1 fault, and it is the refusal that makes the mode safe to offer."
            ).classes("note mt-2")

        with ui.expansion("Every row").classes("mt-2"):
            rows = [
                {
                    "gtin": o.gtin,
                    "lang": o.language,
                    "status": o.status,
                    "page": o.wp_page_id or "",
                    "gs1": "yes" if o.gs1_set else "",
                    "url": o.wp_url or "",
                }
                for o in run.outcomes
            ]
            ui.table(
                columns=[
                    {"name": key, "label": key, "field": key, "align": "left"}
                    for key in ("gtin", "lang", "status", "page", "gs1", "url")
                ],
                rows=rows,
                row_key="gtin",
            ).classes("w-full")


_MAX_ERRORS = 25
