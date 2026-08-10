"""Tests for ui/session.py — the gate enforcement.

This is the safety-critical file in the shell, and the tests are written as the claims a reviewer
would want to check: that no command can be built past an unanswered gate, that cancel is never
read as consent, and that the production flag is a record of a decision rather than a restatement
of configuration.
"""

from __future__ import annotations

import pytest

from lib.gates import Mode
from ui.session import GateNotAnsweredError, PublishSession

_CONFIRMED = "output/acme/plan.confirmed.json"


def _session(**overrides: object) -> PublishSession:
    params: dict[str, object] = {
        "client_id": "acme",
        "mode": Mode.BOTH,
        "has_generator": True,
        "is_production": True,
    }
    params.update(overrides)
    return PublishSession(**params)  # type: ignore[arg-type]


def _answer_everything(session: PublishSession) -> PublishSession:
    """Answer every required gate affirmatively — the only way to a real run."""
    session.answer("intent", "confirm")
    session.answer("content_review", "confirm")
    session.answer("plan_review", "all")
    session.answer("production", "confirm")
    session.answer("dry_run", "proceed")
    return session


# --- The refusal ---------------------------------------------------------------


def test_no_command_is_built_while_a_required_gate_is_outstanding() -> None:
    """The one rule. Not a warning, not a log line — a refusal.

    A shell that could be talked into building the command anyway would be prose again,
    wearing a form.
    """
    with pytest.raises(GateNotAnsweredError):
        _session().execute_argv(_CONFIRMED, dry_run=False)


def test_the_refusal_names_the_gate_so_a_screen_can_say_which() -> None:
    session = _session()
    session.answer("intent", "confirm")

    with pytest.raises(GateNotAnsweredError) as caught:
        session.execute_argv(_CONFIRMED, dry_run=False)

    assert "Plan review" in str(caught.value)
    assert "step 5" in str(caught.value)


def test_cancelling_one_gate_blocks_the_run_even_with_every_other_answered() -> None:
    session = _answer_everything(_session())
    session.answer("plan_review", "cancel")

    with pytest.raises(GateNotAnsweredError):
        session.execute_argv(_CONFIRMED, dry_run=False)
    assert session.cancelled


def test_switch_to_test_is_not_consent_to_run_against_production() -> None:
    """It reads like an answer. It is a refusal with a suggestion attached."""
    session = _answer_everything(_session())
    session.answer("production", "switch-to-test")

    with pytest.raises(GateNotAnsweredError):
        session.execute_argv(_CONFIRMED, dry_run=False)


def test_changed_review_proceeds_because_it_confirms_rather_than_aborts() -> None:
    session = _answer_everything(_session())
    session.answer("plan_review", "changed-review")

    assert session.execute_argv(_CONFIRMED, dry_run=False)


# --- The dry run ---------------------------------------------------------------


def test_the_dry_run_does_not_need_its_own_answer_but_needs_every_other() -> None:
    """It is the thing being authorised at that point; requiring its answer first is a loop."""
    session = _session()
    session.answer("intent", "confirm")
    session.answer("content_review", "confirm")
    session.answer("plan_review", "all")
    session.answer("production", "confirm")

    argv = session.execute_argv(_CONFIRMED, dry_run=True)

    assert "--dry-run" in argv


def test_a_dry_run_still_needs_the_intent_gate() -> None:
    """A preview of a decision nobody made is not a preview, it is a suggestion."""
    with pytest.raises(GateNotAnsweredError):
        _session().execute_argv(_CONFIRMED, dry_run=True)


def test_the_dry_run_never_carries_the_production_flag() -> None:
    session = _answer_everything(_session())
    assert "--i-understand-production" not in session.execute_argv(_CONFIRMED, dry_run=True)


# --- The command --------------------------------------------------------------


def test_a_fully_answered_production_run_carries_the_flag() -> None:
    argv = _answer_everything(_session(mode=Mode.LINKS)).execute_argv(_CONFIRMED, dry_run=False)
    assert argv == [
        "-m",
        "scripts.run_execute",
        "acme",
        "--confirmed",
        _CONFIRMED,
        "--only",
        "links",
        "--i-understand-production",
    ]


def test_a_test_environment_run_never_carries_the_production_flag() -> None:
    """The flag is a record of a decision, and on test there is no such decision to record."""
    session = _session(is_production=False)
    session.answer("intent", "confirm")
    session.answer("content_review", "confirm")
    session.answer("plan_review", "all")
    session.answer("dry_run", "proceed")

    argv = session.execute_argv(_CONFIRMED, dry_run=False)

    assert "--i-understand-production" not in argv


def test_pages_mode_needs_no_production_gate_and_still_runs() -> None:
    """Gate 0 already named the environment, and nothing irreversible follows.

    The flag is still required. ``run_execute`` refuses **every** real run against a production
    client without ``--i-understand-production`` — its condition does not look at ``--only`` — so
    a pages command without it is refused at exit 2 and nothing publishes. This test previously
    asserted the flag was absent, and passed, because it checked the shape of the argv and never
    that the argv would be accepted.
    """
    session = _session(mode=Mode.PAGES)
    session.answer("intent", "confirm")
    session.answer("content_review", "confirm")
    session.answer("plan_review", "all")
    session.answer("dry_run", "proceed")

    argv = session.execute_argv(_CONFIRMED, dry_run=False)

    assert "--only" in argv and argv[argv.index("--only") + 1] == "pages"
    assert "--i-understand-production" in argv


def test_pages_mode_takes_the_acknowledgement_from_gate_zero() -> None:
    """Gate 0 is the substitute, so an unanswered gate 0 is not an acknowledgement."""
    session = _session(mode=Mode.PAGES)

    assert not session.production_acknowledged()

    session.answer("intent", "confirm")

    assert session.production_acknowledged()


def test_cancelling_gate_zero_is_not_an_acknowledgement() -> None:
    session = _session(mode=Mode.PAGES)
    session.answer("intent", "cancel")

    assert not session.production_acknowledged()


def test_links_mode_still_requires_its_own_production_gate() -> None:
    """The substitution applies only where the production gate is absent from the walk."""
    session = _session(mode=Mode.LINKS)
    session.answer("intent", "confirm")

    assert not session.production_acknowledged(), (
        "gate 0 must not stand in for the production gate when that gate is being asked"
    )

    session.answer("production", "confirm")

    assert session.production_acknowledged()


def test_a_client_without_a_generator_skips_the_copy_review() -> None:
    session = _session(has_generator=False, is_production=False)
    session.answer("intent", "confirm")
    session.answer("plan_review", "all")
    session.answer("dry_run", "proceed")

    assert session.execute_argv(_CONFIRMED, dry_run=False)
    assert "content_review" not in {gate.id for gate in session.gates}


# --- Bookkeeping ---------------------------------------------------------------


def test_an_answer_that_is_not_an_option_is_rejected() -> None:
    """A session that looks answered and is not would be worse than one that raises."""
    with pytest.raises(KeyError):
        _session().answer("plan_review", "yes-please")


def test_next_gate_walks_the_flow_in_step_order() -> None:
    session = _session()
    assert session.next_gate is not None
    assert session.next_gate.id == "intent"
    session.answer("intent", "confirm")
    assert session.next_gate is not None
    assert session.next_gate.id == "languages"


def test_outstanding_lists_only_the_required_ones() -> None:
    session = _session()
    session.answer("intent", "confirm")
    outstanding = {gate.id for gate in session.outstanding}
    assert outstanding == {"content_review", "plan_review", "production", "dry_run"}
    assert "languages" not in outstanding  # answered or not, it never blocks a run
