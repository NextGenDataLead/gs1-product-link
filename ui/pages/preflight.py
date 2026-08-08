"""Screen 2 — the preflight, as a list to work down.

Runs ``python -m scripts.doctor --json`` in a subprocess and renders what it says. The checks are
not reimplemented here; a second implementation would be a second thing to keep true.

Subprocessing is what makes the credential checks work at all: ``load_env()`` runs in the
script's ``__main__`` block, so this process holds no secrets and the child resolves them itself.

Offline by default. The full run reads credentials and opens sockets, and an operator who lands
on a screen should not have that happen because they landed on it — the network checks are a
deliberate act, one button away.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from ui import context, runner, theme

#: The four statuses, only for tallying here — the rendering of a check lives in the theme, so
#: this screen and the Setup screen's Test buttons cannot start showing the same check differently.
_STATUSES = ("ok", "warn", "fail", "n/a")


def render() -> None:
    cid = context.client_id()
    cfg = context.client_config(cid)

    with theme.page("Preflight", client_id=cid, environment=cfg.gs1.environment if cfg else None):
        theme.heading(
            "Step 2",
            "Preflight",
            "Everything that can be checked before anything is written — so a missing secret or "
            "a stale copy cache surfaces now, not after live pages exist.",
        )

        def show(payload: Any, result: runner.CommandResult) -> None:
            results.clear()
            with results:
                if payload is None:
                    theme.band("The preflight did not return readable results.", "danger")
                    ui.label(result.stderr or result.stdout or "(no output)").classes("console")
                    return
                _summary(payload)
                for check in payload:
                    theme.check_row(
                        str(check["status"]),
                        str(check["title"]),
                        str(check["detail"]),
                        str(check.get("remedy") or ""),
                    )
            status.text = f"exit {result.returncode} · {result.display_command}"

        def go(*, offline: bool) -> None:
            argv = runner.doctor_argv(cid, offline=offline)
            status.text = "running…"
            payload, result = runner.run_json(argv)
            show(payload, result)

        with ui.row().classes("gap-3 items-center mt-6"):
            theme.quiet_action("Run offline checks", lambda: go(offline=True))
            theme.action("Run everything, including credentials", lambda: go(offline=False))
        ui.label(
            "The full run authenticates against WordPress and mints a GS1 token. Both are "
            "read-only — nothing is written, and the GS1 request is a GET against a GTIN from "
            "your own catalogue."
        ).classes("note")

        ui.separator().classes("my-6")
        status = ui.label("").classes("note")
        results = ui.column().classes("w-full gap-0")

        go(offline=True)


def _summary(payload: list[dict[str, Any]]) -> None:
    """The verdict first, so the list below is read as detail rather than as news."""
    tally = {key: sum(1 for c in payload if c["status"] == key) for key in _STATUSES}
    with ui.row().classes("gap-12 mb-6"):
        theme.figure(str(tally["ok"]), "passed")
        if tally["warn"]:
            theme.figure(str(tally["warn"]), "warnings")
        if tally["fail"]:
            theme.figure(str(tally["fail"]), "failures")
        if tally["n/a"]:
            theme.figure(str(tally["n/a"]), "not applicable")

    if tally["fail"]:
        theme.band("Not ready. Fix the failures below before publishing.", "danger")
    elif tally["warn"]:
        theme.band("Ready, but read the warnings below first.", "warn")
    else:
        theme.band("Ready.", "quiet")
