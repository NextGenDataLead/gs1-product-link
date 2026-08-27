"""Screen 4 — the preflight, as a list to work down.

Runs ``python -m scripts.doctor --json`` in a subprocess and renders what it says. The checks are
not reimplemented here; a second implementation would be a second thing to keep true.

Subprocessing is what makes the credential checks work at all: ``load_env()`` runs in the
script's ``__main__`` block, so this process holds no secrets and the child resolves them itself.

Offline by default. The full run reads credentials and opens sockets, and an operator who lands
on a screen should not have that happen because they landed on it — the network checks are a
deliberate act, one button away.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from nicegui import ui

from ui import context, runner, theme

#: The four statuses, only for tallying here — the rendering of a check lives in the theme, so
#: this screen and the Setup screen's Test buttons cannot start showing the same check differently.
_STATUSES = ("ok", "warn", "fail", "n/a")

#: The checks that only an ``--offline``-less run performs — see ``lib.preflight.run_checks``,
#: which returns before appending these three. They are the whole reason the credentials button
#: exists, and they exist nowhere else in the batch: ``run_execute`` sets ``resolved_gs1 = None``
#: for a dry run, so a dry run never mints a token either.
_CREDENTIAL_CHECKS: Final = frozenset({"site_serves", "wordpress", "gs1"})


def render() -> None:
    cid = context.client_id()
    cfg = context.client_config(cid)

    with theme.page(
        "Preflight",
        client_id=cid,
        environment=cfg.gs1.environment if cfg else None,
        facts=context.rail_facts(cid, cfg),
    ):
        theme.heading(
            theme.eyebrow("Preflight"),
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
            status.text = f"{_finished_at()} · exit {result.returncode} · {result.display_command}"

        # Async, and the subprocess runs off the event loop. Both halves are needed: a blocking
        # `run_json` in a sync handler holds the loop until it is already finished, so "running…"
        # never painted and the screen looked identical from click to result. And because the
        # screen runs the offline checks on load, an unchanged list is exactly what a *working*
        # re-run produces — hence the timestamp, which is the one thing that always changes.
        async def go(*, offline: bool) -> None:
            argv = runner.doctor_argv(cid, offline=offline)
            for button in buttons:
                button.disable()
            status.text = "running…"
            try:
                payload, result = await runner.run_json_off_the_loop(argv)
            finally:
                for button in buttons:
                    button.enable()
            show(payload, result)

        with ui.row().classes("gap-3 items-center mt-6"):
            buttons = [
                theme.quiet_action("Run offline checks", lambda: go(offline=True)),
                theme.action("Run everything, including credentials", lambda: go(offline=False)),
            ]
        ui.label(
            "The full run authenticates against WordPress and mints a GS1 token. Both are "
            "read-only — nothing is written, and the GS1 request is a GET against a GTIN from "
            "your own catalogue."
        ).classes("note")

        ui.separator().classes("my-6")
        status = ui.label("").classes("note")
        results = ui.column().classes("w-full gap-0")

        # Run once on arrival: a screen that opens blank asks the operator to press a button
        # before it can tell them anything, and this one is cheap and touches no credential.
        ui.timer(0, lambda: go(offline=True), once=True)


def _finished_at() -> str:
    """When this run finished, in UTC.

    The screen runs on load, so a re-run of a healthy machine repaints an identical list — which
    is indistinguishable from a button that did nothing. This is the part that always differs.
    """
    return datetime.now(UTC).strftime("%H:%M:%S UTC")


def _verdict(payload: list[dict[str, Any]]) -> tuple[str, str]:
    """The sentence at the top of the screen, and the band kind to render it as.

    **"Ready." used to be said having tested no credential.** The screen runs the offline checks
    on arrival, and offline stops before WordPress, GS1 and the target URL — so the one state the
    operator most needs qualified was the one that read as an unqualified all-clear. A wrong
    password then survives every gate, because the dry run does not authenticate either, and
    surfaces at the first real write with some rows already live.

    The caveat rides on the verdict rather than in a band beneath it: the verdict is the line that
    gets read, and a second band is the one that gets skimmed.

    A *failure* is not qualified. When something offline is already broken the credential question
    is not yet the operator's problem, and diluting "Not ready" would cost more than it buys.
    """
    tally = {key: sum(1 for c in payload if c["status"] == key) for key in _STATUSES}
    if tally["fail"]:
        return "Not ready. Fix the failures below before publishing.", "danger"

    verdict, kind = (
        ("Ready, but read the warnings below first.", "warn")
        if tally["warn"]
        else ("Ready.", "quiet")
    )
    tested = _CREDENTIAL_CHECKS & {str(check.get("name", "")) for check in payload}
    if not tested:
        verdict += " Offline checks only — no credential was tested."
    return verdict, kind


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

    theme.band(*_verdict(payload))
