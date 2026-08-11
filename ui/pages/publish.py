"""Screen 5 — the gated publish. The screen this whole shell exists for.

Every gate in ``lib.gates`` is rendered here as a form, in step order, and
:class:`ui.session.PublishSession` refuses to build a command while a required one is outstanding.
That refusal is the improvement over prose: a paragraph can be paraphrased, compressed or skipped
when the context is long, and a function that raises cannot.

The screen adds nothing to the contract. It renders the gates the contract declares, in the order
it declares, with the reason each exists shown alongside the question — a form that asks without
saying why teaches an operator to answer without reading, and this flow's whole cost is
concentrated in one unreviewed click.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nicegui import ui

from lib.gates import PERMANENCE_WARNING, REVERSIBLE_NOTE, Gate, Mode
from lib.records import PlanClassification
from ui import REPO_ROOT, context, runner, theme
from ui.session import GateNotAnsweredError, PublishSession


def render() -> None:
    cid = context.client_id()
    cfg = context.client_config(cid)

    with theme.page("Publish", client_id=cid, environment=cfg.gs1.environment if cfg else None):
        theme.heading(
            theme.step("Publish"),
            "Publish",
            "One gate at a time. Nothing is written until every required one is answered.",
        )
        if cfg is None or cid is None:
            theme.band("clients.yml did not load. Fix that on the Setup screen first.", "danger")
            return

        _Flow(cid, cfg).build()


class _Flow:
    """The gate walk for one run, held together by a :class:`PublishSession`."""

    def __init__(self, cid: str, cfg: Any) -> None:
        self.cid = cid
        self.cfg = cfg
        self.production = context.is_production(cfg)
        self.session = PublishSession(
            client_id=cid,
            mode=Mode.PAGES,
            has_generator=cfg.generator is not None,
            is_production=self.production,
            languages=list(cfg.wordpress.languages),
        )
        #: Whether the dry run has been run at all. Its Proceed/Cancel buttons appear only after
        #: there is output to approve — offering them beforehand invites approving nothing.
        self.has_run_dry = False
        self.body = ui.column().classes("w-full gap-6")

    # -- assembly -------------------------------------------------------------

    def build(self) -> None:
        self._redraw()

    def _redraw(self) -> None:
        self.body.clear()
        with self.body:
            if self.session.mode.is_permanent:
                theme.band(PERMANENCE_WARNING, "danger")
            else:
                theme.band(REVERSIBLE_NOTE)
            for gate in self.session.gates:
                self._gate(gate)
            self._execute_panel()

    def _gate(self, gate: Gate) -> None:
        answered = gate.id in self.session.answers
        classes = "gate gate-done" if answered else "gate"
        with ui.element("div").classes(classes):
            ui.label(f"STEP {gate.step}{' · REQUIRED' if gate.required else ''}").classes(
                "gate-step"
            )
            ui.label(gate.title).classes("gate-title")
            ui.label(gate.purpose).classes("gate-why")
            getattr(self, f"_gate_{gate.id}", self._gate_default)(gate)

    # -- per-gate bodies ------------------------------------------------------

    def _gate_default(self, gate: Gate) -> None:
        self._options(gate)

    def _gate_intent(self, gate: Gate) -> None:
        fact = context.file_fact(self.cfg.export.path)
        with ui.row().classes("gap-12 items-end mb-4 flex-wrap"):
            theme.figure(str(context.product_count(self.cid) or 0), "products in the catalogue")
            theme.figure(fact.age, "export modified")
            theme.figure(self.cfg.gs1.environment, "environment")
        ui.label(f"Export: {self.cfg.export.path}").classes("mono mb-1")
        ui.label(
            "That path comes from clients.yml and has no command-line override. If the workbook "
            "you mean is somewhere else, this run will silently use the old one."
        ).classes("note mb-4")

        def pick(value: str) -> None:
            self.session.mode = Mode(value)
            self._redraw()

        ui.label("Mode").classes("figure-label")
        ui.toggle(
            {mode.value: f"{mode.value} — {mode.summary}" for mode in Mode},
            value=self.session.mode.value,
            on_change=lambda event: pick(event.value),
        ).props("no-caps").classes("mb-4")
        self._options(gate)

    def _gate_languages(self, gate: Gate) -> None:
        def choose(values: list[str]) -> None:
            # Clearing every language means "all", not "none". A run scoped to no language would
            # confirm nothing, publish nothing and report success — and an empty multi-select is
            # far more often a mis-click than a decision.
            self.session.languages = values or list(self.cfg.wordpress.languages)
            self.session.answers["languages"] = ",".join(self.session.languages)

        ui.select(
            list(self.cfg.wordpress.languages),
            value=list(self.session.languages),
            multiple=True,
            label="Languages this run covers",
            on_change=lambda event: choose(list(event.value)),
        ).props("outlined use-chips").classes("max-w-md")
        ui.label(
            "All of them unless you narrow it. Every other gate stays as it is when this changes."
        ).classes("note mt-2")

    def _gate_content_review(self, gate: Gate) -> None:
        payload, _ = runner.run_json(runner.doctor_argv(self.cid, offline=True))
        entry = _find(payload, "cache_coverage")
        if entry is not None:
            data = entry.get("data") or {}
            with ui.row().classes("gap-12 items-end mb-3"):
                theme.figure(str(data.get("total", "—")), "units in scope")
                theme.figure(str(data.get("covered", "—")), "have copy")
                theme.figure(str(data.get("pending", "—")), "pending")
            if entry["status"] != "ok":
                theme.band(str(entry["detail"]), "danger")
        ui.link("Read the copy on the Content screen →", "/content").classes("mono mb-3")
        self._options(gate)

    def _gate_plan_review(self, gate: Gate) -> None:
        plan = context.load_plan(self.cid)
        summary = context.load_plan_summary(self.cid)

        def build_plan() -> None:
            result = runner.run(runner.run_plan_argv(self.cid))
            ui.notify(
                result.stderr.strip().splitlines()[-1] if result.stderr else "run_plan finished",
                type="positive" if result.ok else "negative",
                timeout=8000,
            )
            self._redraw()

        theme.command(runner.run_plan_argv(self.cid))
        theme.action("Build the plan", build_plan)

        if summary is None or plan is None:
            ui.label("No plan yet. Build one to see the counts.").classes("note mt-3")
            return

        # E19 leads, above the counts. The counts alone read as a routine first run, and
        # confirming past them would rewrite every live page.
        if summary.state_reset_from_corrupt:
            theme.band(
                "Prior state was corrupt and has been reset"
                + (
                    f" (backup: {summary.state_corrupt_backup})"
                    if summary.state_corrupt_backup
                    else ""
                )
                + ". Every row therefore re-plans as NEW. Re-running them is idempotent — pages "
                "are matched by slug and updated in place, not duplicated — but this will rewrite "
                "live pages and resolver targets rather than skip them.",
                "danger",
            )

        with ui.row().classes("gap-12 items-end my-4 flex-wrap"):
            for classification in PlanClassification:
                theme.figure(str(plan.counts.get(classification, 0)), classification.value)

        # An empty plan is the failure this project keeps designing against: executing it would
        # report success having published nothing. Said here, permanently, rather than left to a
        # toast at the moment the run is refused.
        if not plan.rows:
            theme.band(
                "This plan has no rows. A run against it would write nothing and still report "
                "success — so nothing here will run until the plan has something in it. The "
                "reasons are above and on the Preflight screen.",
                "danger",
            )

        if plan.skipped:
            reasons: dict[str, int] = {}
            for unit in plan.skipped:
                reasons[unit.reason.value] = reasons.get(unit.reason.value, 0) + 1
            theme.band(
                f"{len(plan.skipped)} unit(s) never became rows and are NOT in the counts above: "
                + ", ".join(f"{n} {reason}" for reason, n in reasons.items())
                + ". Confirming will not publish them.",
                "warn",
            )
        if summary.excluded:
            ui.label(
                "Excluded by the gates: "
                + ", ".join(
                    f"{n} {reason.replace('_', ' ')}" for reason, n in summary.excluded.items()
                )
            ).classes("note")

        self._options(gate)

    def _gate_row_diff(self, gate: Gate) -> None:
        plan = context.load_plan(self.cid)
        changed = [row for row in plan.rows if row.diff] if plan else []
        if self.session.answers.get("plan_review") != "changed-review":
            ui.label("Only shown when the plan review asks to walk the changed rows.").classes(
                "note"
            )
            return
        if not changed:
            ui.label("No changed rows carry a diff.").classes("note")
            return
        # `show-full-diff` is the gate's own option, and here it lifts the row cap. The cap is not
        # a summary — every field of every row shown is already printed — so the only thing left
        # for "show me everything" to mean is the rows past it.
        full = self.session.answers.get(gate.id) == "show-full-diff"
        shown = changed if full else changed[:_MAX_DIFFS]
        for row in shown:
            with ui.element("div").classes("mb-3"):
                ui.label(f"{row.gtin} ({row.language}) — {row.title}").classes("mono")
                for field, (before, after) in (row.diff or {}).items():
                    ui.label(f"  {field}: {before} → {after}").classes("note mono scroll-x")
        if len(changed) > len(shown):
            ui.label(f"…and {len(changed) - len(shown)} more changed row(s).").classes("note mb-3")
        self._options(gate)

    def _gate_production(self, gate: Gate) -> None:
        theme.band(
            f"About to execute against PRODUCTION. This will make live changes to "
            f"{self.cfg.wordpress.site_url} and register permanent GS1 records.",
            "danger",
        )
        typed = (
            ui.input(label="Type the client id to confirm", placeholder=self.cid)
            .props("outlined")
            .classes("max-w-sm my-3")
        )

        def confirm() -> None:
            if typed.value.strip() != self.cid:
                ui.notify("That is not the client id.", type="negative")
                return
            self.session.answer("production", "confirm")
            self._redraw()

        with ui.row().classes("gap-3"):
            theme.action("Confirm production", confirm, danger=True)
            theme.quiet_action(
                "Switch to test", lambda: self._answer("production", "switch-to-test")
            )
            theme.quiet_action("Cancel", lambda: self._answer("production", "cancel"))

    def _gate_dry_run(self, gate: Gate) -> None:

        async def go() -> None:
            confirmed = self._write_confirmed()
            if confirmed is None:
                return
            try:
                argv = self.session.execute_argv(confirmed, dry_run=True)
            except GateNotAnsweredError as exc:
                ui.notify(str(exc), type="warning", timeout=10000)
                return
            log.style("display:block")
            log.clear()
            log.push(" ".join(["python", *argv]))
            result = await runner.stream(argv, log.push)
            ui.notify(
                "Dry run finished — now read it, then Proceed or Cancel"
                if result.ok
                else f"Dry run exited {result.returncode}",
                type="positive" if result.ok else "warning",
            )
            # Running it is not answering it. The output is the thing to be approved, so the
            # answer comes from the operator below — this used to set "proceed" here, which made
            # the gate self-answering and left Cancel unreachable at the one gate whose whole
            # purpose is to be read before the real write.
            self.has_run_dry = True
            self._redraw()

        ui.label(
            "It builds no clients, needs no credentials and writes nothing. It cannot verify that "
            "resolver targets serve, and it cannot prove the ACF fields will land — so read it "
            "for what it is."
        ).classes("note mb-3")
        theme.action("Run the dry run", go)
        log = ui.log().classes("console mt-4").style("display:none")
        if self.has_run_dry:
            self._options(gate)
        else:
            ui.label("Run it, read the output, then answer.").classes("note mt-3")

    def _gate_post_run(self, gate: Gate) -> None:
        ui.link("Every run, with its per-row outcomes →", "/runs").classes("mono")
        ui.label(
            "That screen also compares the site against state.json, which is the one question a "
            "run log cannot answer: a row logged as an error may still have left a live page."
        ).classes("note")
        self._options(gate)

    # -- shared ---------------------------------------------------------------

    def _options(self, gate: Gate) -> None:
        # `shell_options`, not `options`: an option only the conversational surface can honour —
        # one that needs a model to read the run log, or a per-row walk this screen does not do —
        # would otherwise become a button that does not do what it says.
        if not gate.shell_options:
            return
        with ui.row().classes("gap-3 flex-wrap"):
            for option in gate.shell_options:
                # Filled for the answer that carries the flow on, outlined for the ones that do
                # not. Red is never used here: it belongs to the buttons that write.
                place = theme.action if option.proceeds else theme.quiet_action
                place(
                    option.label,
                    lambda o=option: self._answer(gate.id, o.value),  # type: ignore[misc]
                ).tooltip(option.consequence)
        chosen = self.session.chosen(gate.id)
        if chosen is not None:
            ui.label(f"Answered: {chosen.label}").classes("note mt-2")

    def _answer(self, gate_id: str, value: str) -> None:
        self.session.answer(gate_id, value)
        self._redraw()

    def _execute_panel(self) -> None:
        with ui.element("div").classes("gate"):
            ui.label("EXECUTE").classes("gate-step")
            ui.label("Write it").classes("gate-title")

            outstanding = self.session.outstanding
            if outstanding:
                theme.band(
                    "Still to answer: "
                    + ", ".join(f"{gate.title} (step {gate.step})" for gate in outstanding),
                    "warn",
                )
                return
            if self.session.cancelled:
                theme.band("A gate was answered with cancel. Nothing will run.", "quiet")
                return
            plan = context.load_plan(self.cid)
            if plan is None or not plan.rows:
                theme.band(
                    "Every gate is answered, but the plan has no rows — so there is nothing to "
                    "run. Publishing an empty plan is the one outcome indistinguishable from "
                    "success, which is why it is refused rather than attempted.",
                    "danger",
                )
                return

            async def go() -> None:
                confirmed = self._write_confirmed()
                if confirmed is None:
                    return
                try:
                    argv = self.session.execute_argv(confirmed, dry_run=False)
                except GateNotAnsweredError as exc:
                    ui.notify(str(exc), type="negative", timeout=12000)
                    return
                log.style("display:block")
                log.clear()
                log.push(" ".join(["python", *argv]))
                result = await runner.stream(argv, log.push)
                ui.notify(
                    "Run finished with no errors"
                    if result.ok
                    else f"Run exited {result.returncode} — read the log",
                    type="positive" if result.ok else "negative",
                    timeout=12000,
                )

            log = ui.log().classes("console mt-4").style("display:none")
            theme.action(
                f"Run {self.session.mode.value} for real",
                go,
                danger=self.session.mode.is_permanent,
            )

    def _write_confirmed(self) -> str | None:
        """Serialise the plan and the confirmed subset, and return the path.

        ``confirmed_gtins_by_lang`` pairs must match ``PlanRow.gtin`` **byte for byte** — not
        zero-padded, not normalised. A pair with no matching row is silently ignored, so a
        well-meant reformat here would drop rows from the run without saying anything.
        """
        plan = context.load_plan(self.cid)
        if plan is None:
            ui.notify("No plan to confirm. Build one at the plan review gate.", type="warning")
            return None

        languages = set(self.session.languages or self.cfg.wordpress.languages)
        wanted = _confirmed_classifications(self.session.answers.get("plan_review"))
        pairs = [
            [row.gtin, row.language]
            for row in plan.rows
            if row.classification in wanted and row.language in languages
        ]
        if not pairs:
            ui.notify(
                "Nothing is confirmed — every row was filtered out by the plan choice or the "
                "language selection. Not running.",
                type="warning",
                timeout=10000,
            )
            return None

        path = REPO_ROOT / "output" / self.cid / "plan.confirmed.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"plan": plan.model_dump(mode="json"), "confirmed_gtins_by_lang": pairs},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return str(Path("output") / self.cid / "plan.confirmed.json")


_MAX_DIFFS = 50


def _confirmed_classifications(choice: str | None) -> set[PlanClassification]:
    """Which classifications the plan-review answer confirms.

    UNCHANGED is never executed and HELD is dropped by ``run_execute`` regardless, so neither is
    ever confirmed here — offering them would be offering a choice the script overrules.
    """
    if choice == "new-only":
        return {PlanClassification.NEW}
    return {PlanClassification.NEW, PlanClassification.CHANGED}


def _find(payload: Any, name: str) -> dict[str, Any] | None:
    if not isinstance(payload, list):
        return None
    return next((entry for entry in payload if entry.get("name") == name), None)
