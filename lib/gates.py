"""The operator gates, as data — one source for the skill's prose and any UI that renders them.

The gates are the safety mechanism of this project. ``CLAUDE.md`` says it outright: they live in
``.claude/skills/flow-orchestrator/SKILL.md``, and calling ``scripts/run_execute.py`` directly
bypasses every one of them. Only two guards are in code — the production refusal and the
``--only links`` target-serves check — and both are there *because prose can be skipped*.

That is fine while a language model reading the prose is the only thing driving the pipeline. The
moment a second consumer exists — a form-rendering UI, a test, a checklist — there are two
implementations of one safety contract, and two implementations drift. Silently, because a gate
that quietly stops being shown raises nothing.

So the contract lives here, and both consumers read it. ``SKILL.md`` keeps the verbatim prompt text
a model needs; this module keeps the *structure*: which gates exist, at which step, which are
non-negotiable, and which apply in which mode. ``SKILL.md`` also carries a **Gate index** table
listing every id, and ``tests/lib/test_gates.py`` asserts the two agree in **both directions** — so
adding a gate here without documenting it there, or the reverse, fails CI rather than diverging
quietly.

This module also owns :func:`run_execute_argv`. The command is part of the contract: ``--only``
comes from the intent gate and ``--i-understand-production`` from the production gate, and getting
either wrong turns a reviewed decision into an unreviewed write. A UI that builds its own argv is a
second chance to get it wrong.

Nothing here executes anything, prompts anything, or reads a file.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, NamedTuple


class Mode(StrEnum):
    """Which leg of the publish a run performs.

    Mirrors ``scripts.run_execute._Mode``, and deliberately not imported from it: ``lib`` does not
    depend on ``scripts``, and this is the *operator-facing* vocabulary — the thing gate 0 asks
    about — which happens to share its spelling with the flag.
    """

    PAGES = "pages"
    LINKS = "links"
    BOTH = "both"

    @property
    def is_permanent(self) -> bool:
        """Whether this mode writes a GS1 record, which can never be deleted."""
        return self is not Mode.PAGES

    @property
    def slash_command(self) -> str:
        """The skill that pins this mode."""
        return {Mode.PAGES: "/gs1-pages", Mode.LINKS: "/gs1-links", Mode.BOTH: "/gs1-publish"}[self]

    @property
    def summary(self) -> str:
        """One line naming what this mode writes, in the operator's words."""
        return {
            Mode.PAGES: "WordPress pages only — no GS1 record, no QR",
            Mode.LINKS: (
                "Digital Links and QR only, pointing at pages that already exist — "
                "no page is touched"
            ),
            Mode.BOTH: "WordPress pages first, then Digital Links pointing at them",
        }[self]


#: Shown at gate 0 for any mode that writes a GS1 record. Verbatim from ``SKILL.md``: this is the
#: single fact that makes the flow's caution proportionate, and it must not be paraphrased shorter.
PERMANENCE_WARNING: Final = (
    "A GS1 Digital Link record can never be deleted. Retraction only disables it; the "
    "record stays on the account permanently."
)

#: Its counterpart for ``pages`` — said out loud rather than left implied, because the difference
#: between the two modes is the entire reason there are three of them.
REVERSIBLE_NOTE: Final = "Pages only — no GS1 record is written, so this run is reversible."


class GateOption(NamedTuple):
    """One choice a gate offers.

    ``value`` is the token ``SKILL.md`` prints between the brackets, kept identical so an operator
    who has used one surface recognises the other.

    ``proceeds`` says whether this answer lets the run go on. It is data rather than something a
    consumer infers from the word, because the inference is not obvious in either direction:
    ``changed-review`` proceeds and ``switch-to-test`` does not, and both read like the opposite
    at a glance. A UI that guesses wrong here either blocks a legitimate run or — far worse —
    treats ``cancel`` as consent.
    """

    value: str
    label: str
    consequence: str
    proceeds: bool = True


@dataclass(frozen=True)
class Gate:
    """One operator touchpoint in the publish flow.

    ``step`` is a string because the sequence contains ``"8.5"``. The numbering is load-bearing:
    step 0 was added after the rest and the others kept their numbers, so every cross-reference to
    "step 8" — in ``SKILL.md``, in ``IMPLEMENTATION_SPEC.md`` §8.3, in ``docs/setup.md`` — still
    points at the same gate. Renumbering here would break all of them.

    ``required`` marks a gate that must not be skipped, defaulted through, or remembered from a
    previous run. ``purpose`` is the *why*, in one sentence: a UI that shows only the question
    trains an operator to answer it without reading, and the why is what stops that.
    """

    id: str
    step: str
    title: str
    purpose: str
    options: tuple[GateOption, ...]
    required: bool
    modes: frozenset[Mode]
    #: Only applicable when the client has a ``generator`` block configured.
    needs_generator: bool = False
    #: Only applicable when the resolved GS1 environment is ``production``.
    needs_production: bool = False

    def applies(self, *, mode: Mode, has_generator: bool, is_production: bool) -> bool:
        """Whether this gate fires for a given run."""
        if mode not in self.modes:
            return False
        if self.needs_generator and not has_generator:
            return False
        return not (self.needs_production and not is_production)


_ALL_MODES: Final = frozenset(Mode)
_PERMANENT_MODES: Final = frozenset({Mode.LINKS, Mode.BOTH})


GATES: Final[tuple[Gate, ...]] = (
    Gate(
        id="intent",
        step="0",
        title="Intent confirmation",
        purpose=(
            "States the mode, cross-checks the configured export file against the one the "
            "operator has in mind, gives the catalogue size and the environment, and — for "
            "anything that writes to GS1 — warns that the records are permanent. The export "
            "cross-check catches the likeliest real error: a fresh export dropped somewhere new "
            "while `export.path` still points at the old one, which nothing downstream notices."
        ),
        options=(
            GateOption("confirm", "Confirm", "Proceed with this mode"),
            GateOption("change-mode", "Change mode", "Re-present with a different mode", False),
            GateOption("cancel", "Cancel", "Abort; nothing runs", False),
        ),
        required=True,
        modes=_ALL_MODES,
    ),
    Gate(
        id="languages",
        step="2",
        title="Language selection",
        purpose=(
            "Which of the client's configured languages this run covers. Intersected with the "
            "confirmed rows at step 6."
        ),
        options=(),  # built from wordpress.languages at render time
        required=False,
        modes=_ALL_MODES,
    ),
    Gate(
        id="content_review",
        step="3",
        title="Generated copy review (gate 1 of 2)",
        purpose=(
            "The tagline and Eigenschappen are LLM-written, so they are read before they can "
            "reach a page. Review the copy against the real product, not the 'ingested N' count "
            "— this pipeline fails silently. Show the cache-coverage counts here too: a unit with "
            "no fresh cache entry is dropped from the plan entirely (E21), so an empty cache "
            "yields an empty plan and a run that reports success having published nothing."
        ),
        options=(
            GateOption("confirm", "Copy is good", "Proceed to planning"),
            GateOption("regenerate", "Regenerate", "Fill the cache again before planning", False),
            GateOption("cancel", "Cancel", "Abort; nothing runs", False),
        ),
        required=True,
        modes=_ALL_MODES,  # links mode too: an empty cache still empties the plan
        needs_generator=True,
    ),
    Gate(
        id="missing_field",
        step="4",
        title="Missing-field prompt",
        purpose=(
            "One per unit dropped for a missing `product_name` in that language (E18). Decide "
            "whether to accept the omission, batch the decision, or stop the run."
        ),
        options=(
            GateOption("skip-row", "Skip this unit", "Other languages proceed"),
            GateOption(
                "ask-me-later", "Ask me later", "Batch the prompts, present at the end", True
            ),
            GateOption("fail-run", "Stop the run", "Abort before execute", False),
        ),
        required=False,
        modes=_ALL_MODES,
    ),
    Gate(
        id="plan_review",
        step="5",
        title="Plan review (gate 2 of 2)",
        purpose=(
            "The last look before anything is written. Show the counts, the gate exclusions, and "
            "the units dropped before classification — an operator reading 'New: 0' alone "
            "concludes there is nothing to do, when in fact there is copy to generate. When "
            "prior state was reset from a corrupt file (E19), that warning goes **above** the "
            "counts: the counts alone read as a routine first run, and confirming would rewrite "
            "every live page."
        ),
        options=(
            GateOption("all", "All", "Confirm every NEW and CHANGED row"),
            GateOption("new-only", "New only", "Confirm NEW rows; skip CHANGED"),
            GateOption("changed-review", "Review changed", "Walk each CHANGED row's diff"),
            GateOption("cancel", "Cancel", "Abort; nothing is written", False),
        ),
        required=True,
        modes=_ALL_MODES,
    ),
    Gate(
        id="row_diff",
        step="6",
        title="Per-row diff",
        purpose=(
            "Only on `changed-review`. Shows the fields actually present in the row's diff and "
            "never invents an old value: state records the prior `title` and `wp_url` and nothing "
            "else, so those are the only two that can show a real before/after. A `gs1_link` key "
            "means the page is published but its resolver link was never written — nothing about "
            "the page is changing."
        ),
        options=(
            GateOption("apply", "Apply", "Include this row in the run"),
            GateOption("skip", "Skip", "Leave this row unchanged", True),
            GateOption("show-full-diff", "Show full diff", "Print every field, then re-ask", False),
        ),
        required=False,
        modes=_ALL_MODES,
    ),
    Gate(
        id="production",
        step="8",
        title="Production environment confirmation",
        purpose=(
            "Mandatory, non-overridable, and enforced per run rather than per session. Skipped in "
            "`pages` mode only because gate 0 has already named the environment and nothing "
            "irreversible follows — a second production prompt for a page you can delete trains "
            "the operator to click through them."
        ),
        options=(
            GateOption("confirm", "Confirm", "Execute against production"),
            GateOption(
                "switch-to-test", "Switch to test", "Re-resolve to the test environment", False
            ),
            GateOption("cancel", "Cancel", "Abort; nothing is written", False),
        ),
        required=True,
        modes=_PERMANENT_MODES,
        needs_production=True,
    ),
    Gate(
        id="dry_run",
        step="8.5",
        title="Dry run",
        purpose=(
            "The same command with `--dry-run` and every other flag identical. Catches a plan "
            "pointing at the wrong rows, the wrong leg or the wrong URLs while it still costs "
            "nothing. Two things it cannot catch, so do not read a clean dry run as more than it "
            "is: in `links` mode it does not verify that targets serve, and it never proves the "
            "ACF fields will land."
        ),
        options=(
            GateOption("proceed", "Proceed", "Run it for real"),
            GateOption("cancel", "Cancel", "Abort; nothing is written", False),
        ),
        required=True,
        modes=_ALL_MODES,
    ),
    Gate(
        id="post_run",
        step="11",
        title="Post-run summary",
        purpose=(
            "What actually ran, per row, with each error named. In `links` mode a refused GTIN "
            "means its target URL did not serve — read that as 'the page is not where the plan "
            "says it is', not as a GS1 fault."
        ),
        options=(
            GateOption("yes", "Retry the failures", "Re-run execute filtered to the failed GTINs"),
            GateOption("no", "Done", "Finish"),
            GateOption("detail", "Explain each error", "Read the run log and explain"),
        ),
        required=False,
        modes=_ALL_MODES,
    ),
)

#: Gate ids by id, for lookup.
BY_ID: Final[dict[str, Gate]] = {gate.id: gate for gate in GATES}


def gates_for(*, mode: Mode, has_generator: bool, is_production: bool) -> tuple[Gate, ...]:
    """The gates that fire for one run, in step order."""
    return tuple(
        gate
        for gate in GATES
        if gate.applies(mode=mode, has_generator=has_generator, is_production=is_production)
    )


def run_execute_argv(  # noqa: PLR0913 — one parameter per gate answer; bundling them hides them
    client_id: str,
    *,
    mode: Mode,
    confirmed_path: str,
    dry_run: bool,
    production_acknowledged: bool = False,
    revive: bool = False,
) -> list[str]:
    """Build the ``run_execute`` command line the gates authorise, and only that.

    The command *is* part of the gate contract, so it is built once rather than twice.

    * ``--only`` is appended for ``pages`` and ``links`` and omitted for ``both``, because
      omitting it is what ``both`` means — every invocation written before ``--only`` existed
      keeps its behaviour.
    * ``--i-understand-production`` is appended **only** when the production gate has been
      answered, and never on a dry run, which needs no acknowledgement because it writes nothing.
      Passing it earlier would hand a real run the authorisation before anyone granted it; the
      flag exists precisely so that the live write is a deliberate act.

    ``production_acknowledged`` is a positive statement — "someone confirmed" — rather than an
    ``is_production`` fact, so the flag cannot be derived from configuration alone. Deriving it
    would make it a description of the environment instead of a record of a decision.

    Args:
        client_id: The client to run.
        mode: The leg confirmed at gate 0.
        confirmed_path: Path to the ``ConfirmedPlan`` JSON.
        dry_run: Whether this is the mandatory preview (gate 8.5) rather than the real run.
        production_acknowledged: Whether the production gate (step 8) was answered ``confirm``.
        revive: Whether to re-publish GTINs that ``run_unpublish`` took down.

    Returns:
        The full argv, starting at the module name — ``["python", "-m", …]`` is the caller's job,
        since it owns the interpreter.
    """
    argv = ["-m", "scripts.run_execute", client_id, "--confirmed", confirmed_path]
    if mode is not Mode.BOTH:
        argv += ["--only", mode.value]
    if dry_run:
        argv.append("--dry-run")
    elif production_acknowledged:
        argv.append("--i-understand-production")
    if revive:
        argv.append("--revive")
    return argv
