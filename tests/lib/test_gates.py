"""Tests for lib/gates.py — including the drift check that keeps it honest.

The gates are this project's safety mechanism and they are written twice: as prose a model reads
in ``.claude/skills/flow-orchestrator/SKILL.md``, and as structure a form-rendering UI reads in
``lib/gates.py``. Two implementations of one contract drift, and this one drifts *silently* — a
gate that quietly stops being shown raises nothing at all.

So the SKILL carries a **Gate index** table and these tests assert the two agree in both
directions. Adding a gate to either without the other fails CI.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

from lib.gates import GATES, PERMANENCE_WARNING, Gate, Mode, gates_for, run_execute_argv

_SKILL: Final = (
    Path(__file__).resolve().parent.parent.parent
    / ".claude"
    / "skills"
    / "flow-orchestrator"
    / "SKILL.md"
)

#: One row of the Gate index table: | `id` | step | required | modes |
_ROW: Final = re.compile(r"^\|\s*`(?P<id>[a-z_]+)`\s*\|\s*(?P<step>[\d.]+)\s*\|", re.MULTILINE)


def _indexed_gates() -> dict[str, str]:
    """The gate id → step mapping the SKILL's Gate index declares."""
    return {m.group("id"): m.group("step") for m in _ROW.finditer(_SKILL.read_text("utf-8"))}


# --- Drift ---------------------------------------------------------------------


def test_every_gate_in_code_is_documented_in_the_skill() -> None:
    documented = _indexed_gates()
    assert {gate.id for gate in GATES} <= set(documented), (
        "a gate exists in lib/gates.py with no entry in the SKILL's Gate index — the model "
        "driving the flow would never present it"
    )


def test_every_gate_in_the_skill_exists_in_code() -> None:
    documented = _indexed_gates()
    assert set(documented) <= {gate.id for gate in GATES}, (
        "the SKILL's Gate index names a gate lib/gates.py does not define — a UI rendering the "
        "gates would silently skip it"
    )


def test_the_step_numbers_agree() -> None:
    """The numbering is load-bearing: cross-references to "step 8" must keep meaning step 8."""
    documented = _indexed_gates()
    assert {gate.id: gate.step for gate in GATES} == documented


def test_the_skill_still_carries_the_permanence_warning_verbatim() -> None:
    """The one fact that makes the flow's caution proportionate. It must not be paraphrased.

    Compared with whitespace collapsed, since the SKILL wraps it inside a fenced block and where
    the line breaks fall is not part of the claim.
    """
    assert _collapse(PERMANENCE_WARNING) in _collapse(_SKILL.read_text("utf-8"))


def _collapse(text: str) -> str:
    return " ".join(text.split())


# --- Applicability -------------------------------------------------------------


def test_the_production_gate_never_fires_in_pages_mode() -> None:
    """Not laxity: gate 0 already named the environment and nothing irreversible follows.

    A second production prompt for a page you can delete only trains the operator to click
    through them, which costs at the gate that matters.
    """
    fired = {gate.id for gate in gates_for(mode=Mode.PAGES, has_generator=True, is_production=True)}
    assert "production" not in fired


@pytest.mark.parametrize("mode", [Mode.LINKS, Mode.BOTH])
def test_the_production_gate_always_fires_on_a_permanent_production_run(mode: Mode) -> None:
    fired = {gate.id for gate in gates_for(mode=mode, has_generator=False, is_production=True)}
    assert "production" in fired


@pytest.mark.parametrize("mode", list(Mode))
def test_the_dry_run_gate_fires_in_every_mode(mode: Mode) -> None:
    """Mandatory, and the cheapest thing in the flow."""
    for production in (True, False):
        fired = gates_for(mode=mode, has_generator=False, is_production=production)
        assert any(gate.id == "dry_run" and gate.required for gate in fired)


def test_content_review_fires_in_links_mode_too(  # noqa: D401 — the name is the assertion
) -> None:
    """No page is written, but an empty cache still empties the plan.

    With a generator configured, run_plan omits any unit with no generated tagline (E21), so a
    links run against an unfilled cache publishes nothing and reports success.
    """
    fired = {
        gate.id for gate in gates_for(mode=Mode.LINKS, has_generator=True, is_production=False)
    }
    assert "content_review" in fired


def test_content_review_does_not_fire_without_a_generator() -> None:
    fired = {
        gate.id for gate in gates_for(mode=Mode.BOTH, has_generator=False, is_production=False)
    }
    assert "content_review" not in fired


def test_gates_come_back_in_step_order() -> None:
    """A UI walks them in the order returned; out of order it would ask to confirm a plan
    it has not built yet."""
    steps = [
        float(gate.step)
        for gate in gates_for(mode=Mode.BOTH, has_generator=True, is_production=True)
    ]
    assert steps == sorted(steps)


def test_every_required_gate_says_why_it_exists() -> None:
    """A UI that shows only the question trains an operator to answer without reading."""
    for gate in GATES:
        assert gate.purpose, gate.id
        if gate.required:
            assert len(gate.purpose) > _MIN_PURPOSE_CHARS, gate.id


_MIN_PURPOSE_CHARS: Final = 80


# --- The command the gates authorise -------------------------------------------


def test_both_mode_omits_only_because_omitting_it_is_what_both_means() -> None:
    argv = run_execute_argv("acme", mode=Mode.BOTH, confirmed_path="p.json", dry_run=False)
    assert "--only" not in argv


@pytest.mark.parametrize("mode", [Mode.PAGES, Mode.LINKS])
def test_single_leg_modes_pass_only(mode: Mode) -> None:
    argv = run_execute_argv("acme", mode=mode, confirmed_path="p.json", dry_run=False)
    assert argv[argv.index("--only") + 1] == mode.value


def test_the_production_flag_is_absent_until_the_gate_is_answered() -> None:
    """The flag records a decision, not a fact about the environment.

    Deriving it from `gs1.environment` would make it a description of where the run points
    rather than a record that someone confirmed it — which is the whole point of the guard.
    """
    argv = run_execute_argv(
        "acme",
        mode=Mode.BOTH,
        confirmed_path="p.json",
        dry_run=False,
        production_acknowledged=False,
    )
    assert "--i-understand-production" not in argv


def test_the_dry_run_never_carries_the_production_flag() -> None:
    """It writes nothing, so it needs no authorisation — and must not consume one."""
    argv = run_execute_argv(
        "acme", mode=Mode.BOTH, confirmed_path="p.json", dry_run=True, production_acknowledged=True
    )
    assert "--dry-run" in argv
    assert "--i-understand-production" not in argv


def test_the_real_run_carries_the_flag_once_the_gate_is_answered() -> None:
    argv = run_execute_argv(
        "acme",
        mode=Mode.LINKS,
        confirmed_path="output/acme/plan.confirmed.json",
        dry_run=False,
        production_acknowledged=True,
    )
    assert argv == [
        "-m",
        "scripts.run_execute",
        "acme",
        "--confirmed",
        "output/acme/plan.confirmed.json",
        "--only",
        "links",
        "--i-understand-production",
    ]


def test_the_dry_run_argv_matches_the_real_one_but_for_the_two_flags() -> None:
    """SKILL step 8.5: "the *same* command with --dry-run added and every other flag identical"."""
    common = {"mode": Mode.PAGES, "confirmed_path": "p.json", "production_acknowledged": True}
    dry = run_execute_argv("acme", dry_run=True, **common)  # type: ignore[arg-type]
    real = run_execute_argv("acme", dry_run=False, **common)  # type: ignore[arg-type]

    assert [a for a in dry if a != "--dry-run"] == [
        a for a in real if a != "--i-understand-production"
    ]


def test_gate_ids_are_unique() -> None:
    ids = [gate.id for gate in GATES]
    assert len(ids) == len(set(ids))


def test_a_gate_knows_its_own_applicability() -> None:
    gate = Gate(
        id="x",
        step="1",
        title="X",
        purpose="why",
        options=(),
        required=False,
        modes=frozenset({Mode.LINKS}),
        needs_production=True,
    )
    assert gate.applies(mode=Mode.LINKS, has_generator=False, is_production=True)
    assert not gate.applies(mode=Mode.LINKS, has_generator=False, is_production=False)
    assert not gate.applies(mode=Mode.PAGES, has_generator=False, is_production=True)
