"""One publish run's gate answers, and the refusal to build a command without them.

This is the part of the shell that matters. Everything else here is a nicer way to look at files;
this is the safety contract, and it is enforced **in code** rather than in prose — which is the
one thing the existing skill-based flow cannot do. `CLAUDE.md` is blunt about the cost of prose:
the operator gates live only in a Markdown file, and calling `run_execute` directly bypasses every
one of them. A required checkbox that a function refuses to proceed without is a stronger gate
than a paragraph a model might paraphrase, compress, or skip when the context is long.

The rule is one sentence: :meth:`PublishSession.execute_argv` raises unless every *required*
applicable gate has been answered with an option that proceeds. Not "warns". Not "logs". A shell
that could be talked into building the command anyway would be prose again, wearing a form.

Gate applicability, ordering and the argv itself all come from :mod:`lib.gates`, which
``flow-orchestrator/SKILL.md`` is checked against. Nothing here decides which gates exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lib.gates import BY_ID, Gate, GateOption, Mode, gates_for, run_execute_argv
from lib.records import SkippedUnit


class GateNotAnsweredError(RuntimeError):
    """A required gate has no answer, or was answered with something that does not proceed.

    A distinct type rather than a bare ``RuntimeError`` so a screen can catch exactly this and
    show the operator which gate is outstanding, instead of a traceback that says nothing.
    """


@dataclass
class PublishSession:
    """The gate answers collected so far for one publish run.

    Deliberately mutable and deliberately **not** persisted. A run's confirmations are about
    *this* run: the plan it was shown, the counts it was shown, the environment it named. Reusing
    them for the next run would be the "remembered consent" failure the production gate is
    explicitly enforced per-run to avoid.
    """

    client_id: str
    mode: Mode
    has_generator: bool
    is_production: bool
    #: The languages this run covers. Empty means "not chosen yet"; the languages gate fills it.
    languages: list[str] = field(default_factory=list)
    #: gate id → the option value the operator picked.
    answers: dict[str, str] = field(default_factory=dict)
    #: The units this run's plan dropped for a missing ``product_name`` (E18), as the plan
    #: recorded them. Not an answer and not a file read: a *fact about this run*, the same kind
    #: of thing as ``has_generator`` and ``is_production``, and it decides whether the
    #: missing-field gate is in the walk at all.
    #:
    #: The units themselves rather than a bare flag, on purpose. The screen both hides the gate
    #: and names the units it is about; if those two came from separate reads of ``plan.json``
    #: they could disagree, and the way they would disagree is the gate rendering over an empty
    #: list — the very defect this field exists to fix, with a condition on top. One value, one
    #: truth.
    #:
    #: Defaulted, unlike ``gates_for``'s parameter, because a session begins before there is a
    #: plan. That costs nothing here: the screen refreshes it on every redraw, and the drift
    #: protection lives one call down, at the contract boundary.
    units_missing_product_name: tuple[SkippedUnit, ...] = ()

    @property
    def gates(self) -> tuple[Gate, ...]:
        """The gates that fire for this run, in step order.

        Recomputed on every access rather than cached, which matters now that one of the inputs
        is a fact about the plan: the plan is built at step 5, in the middle of the walk, so the
        answer to "which gates?" legitimately changes partway through.
        """
        return gates_for(
            mode=self.mode,
            has_generator=self.has_generator,
            is_production=self.is_production,
            has_missing_product_name=bool(self.units_missing_product_name),
        )

    def answer(self, gate_id: str, value: str) -> None:
        """Record the operator's choice at one gate.

        Raises:
            KeyError: If ``gate_id`` is not a gate, or ``value`` is not one of its options. Both
                are programming errors in a screen, and both would otherwise produce a session
                that looks answered and is not.
        """
        gate = BY_ID[gate_id]
        if gate.options and value not in {option.value for option in gate.options}:
            raise KeyError(f"{value!r} is not an option at gate {gate_id!r}")
        self.answers[gate_id] = value

    def chosen(self, gate_id: str) -> GateOption | None:
        """The option picked at ``gate_id``, or ``None`` when it has not been answered."""
        value = self.answers.get(gate_id)
        if value is None:
            return None
        return next((o for o in BY_ID[gate_id].options if o.value == value), None)

    def production_acknowledged(self) -> bool:
        """Whether someone confirmed, at a gate, that this run writes to production.

        In ``links`` and ``both`` the production gate asks outright, and this is its answer.

        In ``pages`` that gate is **not in the walk** — nothing irreversible follows, so making
        the operator type the client id for a reversible run would be ceremony — and gate 0
        stands in: it is required, and it states the environment and the mode before anything
        else. That substitution is what ``CLAUDE.md`` already describes, and until it was
        implemented here the flag could never be set in ``pages`` mode, so ``run_execute``
        refused every real pages run against a production client. The reversible half of a
        publish was unreachable from this shell.

        Never derived from ``gs1.environment``. The flag records that a person confirmed, not a
        fact about the config — deriving it would turn a decision into a description.
        """
        if not self.is_production:
            return False
        if any(gate.id == "production" for gate in self.gates):
            return self.proceeded("production")
        return self.proceeded("intent")

    def proceeded(self, gate_id: str) -> bool:
        """Whether ``gate_id`` was answered with an option that lets the run continue.

        A gate with no fixed options — ``languages`` — counts as proceeded once answered at all.
        """
        gate = BY_ID[gate_id]
        if not gate.options:
            return gate_id in self.answers
        option = self.chosen(gate_id)
        return option is not None and option.proceeds

    @property
    def outstanding(self) -> tuple[Gate, ...]:
        """The required gates still standing between this session and a run."""
        return tuple(gate for gate in self.gates if gate.required and not self.proceeded(gate.id))

    @property
    def next_gate(self) -> Gate | None:
        """The next gate to present, required or not, or ``None`` when all have been answered."""
        return next((gate for gate in self.gates if gate.id not in self.answers), None)

    @property
    def cancelled(self) -> bool:
        """Whether any gate was answered with an option that stops the run."""
        return any(gate.id in self.answers and not self.proceeded(gate.id) for gate in self.gates)

    def execute_argv(
        self, confirmed_path: str, *, dry_run: bool, revive: bool = False
    ) -> list[str]:
        """The command this session authorises — or a refusal.

        The dry run (gate 8.5) is itself a required gate, so it is exempt from needing its *own*
        answer: it is the thing being authorised at that point, and requiring an answer before
        producing the command it previews would be a loop. Every other required gate must already
        have proceeded, including the production one — a dry run of a plan whose intent was never
        confirmed is a preview of a decision nobody made.

        Raises:
            GateNotAnsweredError: If any required gate is outstanding.
        """
        outstanding = [gate for gate in self.outstanding if not (dry_run and gate.id == "dry_run")]
        if outstanding:
            names = ", ".join(f"{gate.title} (step {gate.step})" for gate in outstanding)
            raise GateNotAnsweredError(f"not answered yet: {names}")

        return run_execute_argv(
            self.client_id,
            mode=self.mode,
            confirmed_path=confirmed_path,
            dry_run=dry_run,
            production_acknowledged=self.production_acknowledged(),
            revive=revive,
        )
