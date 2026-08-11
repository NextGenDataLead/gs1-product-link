"""Tests for ui/context.py — reading the doctor's answer rather than re-deriving it.

Gate 0 used to lead with the length of ``products.json`` under the label "products in the
catalogue". Honest, and the wrong number: it read **127** on a run scoped to one product, at the
gate where the operator forms their picture of what they are about to do.

The fix is not to compute scope in the shell. ``lib.preflight.in_scope`` already composes the two
gates that decide it, and a second implementation of "what will this run touch" is the same class
of mistake as a second implementation of the operator gates. So the shell reads the doctor's
``scope`` check, and these tests cover the reading — including every way it can be unreadable,
because the one thing that must never happen is a catalogue total shown under a scope label.

No NiceGUI: ``ui.context`` imports only ``lib`` and ``ui.REPO_ROOT``, so this runs in the required
CI job rather than the optional one.
"""

from __future__ import annotations

from typing import Any

from ui.context import Scope, doctor_check, scope_from


def _payload(**overrides: Any) -> list[dict[str, Any]]:
    """A doctor payload shaped exactly like the real one, scope check included."""
    scope: dict[str, Any] = {
        "name": "scope",
        "title": "What a run would touch",
        "status": "ok",
        "detail": "15 of 127 product(s) in the export are in scope, after process list (x.xlsx).",
        "remedy": "",
        "data": {"in_scope": 15, "total": 127},
    }
    scope.update(overrides)
    return [{"name": "config", "status": "ok", "data": {}}, scope]


# --- doctor_check --------------------------------------------------------------


def test_finds_a_check_by_name() -> None:
    assert doctor_check(_payload(), "scope") is not None
    assert doctor_check(_payload(), "config") is not None


def test_a_check_that_did_not_run_is_none_rather_than_an_error() -> None:
    assert doctor_check(_payload(), "cache_coverage") is None


def test_a_payload_that_is_not_a_list_is_none() -> None:
    """A crashed command still printed something, and a caller would rather show that than raise."""
    for payload in (None, "", "Traceback (most recent call last):", {"error": "boom"}, 3):
        assert doctor_check(payload, "scope") is None


# --- scope_from ----------------------------------------------------------------


def test_reads_the_two_numbers_and_the_sentence() -> None:
    scope = scope_from(_payload())
    assert scope == Scope(
        in_scope=15,
        total=127,
        detail="15 of 127 product(s) in the export are in scope, after process list (x.xlsx).",
        empty=False,
    )


def test_an_empty_scope_is_flagged_so_gate_zero_can_say_so() -> None:
    """The doctor FAILs this check when nothing is in scope, and that is the loudest case.

    A run against an empty scope writes nothing and reports success — the one outcome
    indistinguishable from working, and the failure this project keeps designing against.
    """
    scope = scope_from(_payload(status="fail", data={"in_scope": 0, "total": 127}))
    assert scope is not None
    assert scope.empty
    assert scope.in_scope == 0


def test_no_scope_check_reads_as_unknown_not_as_zero() -> None:
    """``None`` and ``Scope(in_scope=0)`` mean opposite things and must not be conflated.

    Zero is "this run would touch nothing" — actionable and alarming. Absent is "the preflight did
    not say", which warrants no conclusion at all.
    """
    assert scope_from([{"name": "config", "status": "ok"}]) is None


def test_an_unreadable_payload_never_yields_a_number() -> None:
    """The one outcome worth ruling out explicitly: a figure appearing under a scope label.

    Falling back to the catalogue count here would reproduce the exact defect this replaces,
    wearing the right words — which is worse than the original, because the label would now
    vouch for it.
    """
    for payload in (None, "Traceback", {"error": "boom"}, []):
        assert scope_from(payload) is None


def test_counts_that_are_not_integers_are_refused() -> None:
    """A malformed `data` block must not become a figure on the gate that authorises the run."""
    assert scope_from(_payload(data={})) is None
    assert scope_from(_payload(data={"in_scope": 15})) is None
    assert scope_from(_payload(data={"in_scope": "15", "total": "127"})) is None


def test_a_missing_detail_sentence_costs_the_sentence_and_nothing_else() -> None:
    """The numbers are the point; the sentence explains them. Losing it must not lose them."""
    scope = scope_from(_payload(detail=None))
    assert scope is not None
    assert scope.in_scope == 15
    assert scope.detail == ""
