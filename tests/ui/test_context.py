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

from ui.context import Scope, doctor_check, group_results, scope_from, split_results


def _payload(**overrides: Any) -> list[dict[str, Any]]:
    """A doctor payload shaped exactly like the real one, scope check included."""
    scope: dict[str, Any] = {
        "name": "scope",
        "title": "What a run would touch",
        "status": "ok",
        "detail": "15 of 127 product(s) in the export are in scope, after process list (x.xlsx).",
        "remedy": "",
        "data": {"in_scope": 15, "total": 127, "in_scope_gtins": ["08713195000001"]},
    }
    scope.update(overrides)
    return [{"name": "config", "status": "ok", "data": {}}, scope]


# --- doctor_check --------------------------------------------------------------


def test_finds_a_check_by_name() -> None:
    assert doctor_check(_payload(), "scope") is not None
    assert doctor_check(_payload(), "config") is not None


def test_a_check_that_did_not_run_is_none_rather_than_an_error() -> None:
    assert doctor_check(_payload(), "generation_results") is None


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
        gtins=frozenset({"08713195000001"}),
    )


def test_an_empty_scope_is_flagged_so_gate_zero_can_say_so() -> None:
    """The doctor FAILs this check when nothing is in scope, and that is the loudest case.

    A run against an empty scope writes nothing and reports success — the one outcome
    indistinguishable from working, and the failure this project keeps designing against.
    """
    scope = scope_from(
        _payload(status="fail", data={"in_scope": 0, "total": 127, "in_scope_gtins": []})
    )
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


# --- the in-scope GTIN list ----------------------------------------------------


def test_carries_the_gtins_so_a_screen_can_filter_by_them() -> None:
    """Counts let a screen *report* scope; the list lets it *filter* by it.

    The Content screen needs the second. Without it, showing this run's copy rather than every
    unit ever generated would mean re-deriving scope in the shell — a second implementation of
    the thing `lib.preflight.in_scope` exists to be the only one of.
    """
    scope = scope_from(_payload(data={"in_scope": 2, "total": 9, "in_scope_gtins": ["a", "b"]}))
    assert scope is not None
    assert scope.gtins == frozenset({"a", "b"})


def test_a_doctor_that_never_reported_gtins_yields_an_empty_set_not_a_crash() -> None:
    """Back-compat, and the caller's contract: empty means *unknown*, never *nothing in scope*.

    A screen that filtered to an empty set here would hide the whole cache and read as "there is
    no copy" — the opposite of the truth, and a worse failure than the one being fixed.
    """
    scope = scope_from(_payload(data={"in_scope": 15, "total": 127}))
    assert scope is not None
    assert scope.gtins == frozenset()
    assert scope.in_scope == 15


def test_junk_in_the_gtin_list_is_dropped_rather_than_carried() -> None:
    """It is used as a set-membership filter, so a non-string can only ever fail to match."""
    scope = scope_from(_payload(data={"in_scope": 1, "total": 1, "in_scope_gtins": ["a", 7, None]}))
    assert scope is not None
    assert scope.gtins == frozenset({"a"})


def test_a_gtin_list_that_is_not_a_list_is_treated_as_unknown() -> None:
    scope = scope_from(_payload(data={"in_scope": 1, "total": 1, "in_scope_gtins": "08713195"}))
    assert scope is not None
    assert scope.gtins == frozenset()


# --- splitting the cache into this run and everything else ---------------------


def _scope(*gtins: str) -> Scope:
    return Scope(in_scope=len(gtins), total=99, detail="", empty=False, gtins=frozenset(gtins))


_COPY: dict[str, dict[str, Any]] = {"a": {"nl": {}}, "b": {"nl": {}}, "c": {"nl": {}}}


def test_the_batch_is_separated_from_copy_written_for_another_scope() -> None:
    """The defect: the review listed every GTIN in the file, under a correctly scoped figure.

    It mattered most when the file was a cache that accumulated for the machine's lifetime. It
    still matters: a results file written against a longer process list carries GTINs this run
    will not touch, and the screen must not present them as the batch.
    """
    split = split_results(_COPY, _scope("a", "c"))

    assert set(split.in_scope) == {"a", "c"}
    assert set(split.others) == {"b"}
    assert split.scoped


def test_in_scope_gtins_with_no_copy_are_named() -> None:
    """The interesting case: it is the copy that still has to be written."""
    split = split_results(_COPY, _scope("a", "zz", "yy"))

    assert split.missing == ("yy", "zz")


def test_an_unknown_scope_shows_everything_rather_than_nothing() -> None:
    """Wrong in the safe direction, and the direction matters.

    Filtering to an empty set would hide the copy entirely and read as "there is none" — worse
    than the unscoped list being replaced, because it stops the operator looking. ``scoped`` is
    what lets the screen label it honestly instead.
    """
    for scope in (None, _scope()):
        split = split_results(_COPY, scope)
        assert split.in_scope == _COPY
        assert split.others == {}
        assert split.missing == ()
        assert not split.scoped


def test_a_batch_with_no_generated_copy_at_all_is_empty_not_unscoped() -> None:
    """Distinct from an unknown scope: here the answer is known, and the answer is none."""
    split = split_results(_COPY, _scope("zz"))

    assert split.in_scope == {}
    assert split.scoped
    assert split.missing == ("zz",)


def test_the_split_does_not_mutate_what_it_was_given() -> None:
    original = dict(_COPY)
    split_results(_COPY, _scope("a"))
    assert original == _COPY


def test_the_flat_results_list_is_grouped_by_gtin_and_language() -> None:
    """A producer writes one item at a time; a screen reads one product at a time."""
    grouped = group_results(
        [
            {"gtin": "a", "language": "nl", "usps": ["NL"]},
            {"gtin": "a", "language": "fr", "usps": ["FR"]},
            {"gtin": "b", "language": "nl", "usps": ["B"]},
        ]
    )

    assert set(grouped) == {"a", "b"}
    assert set(grouped["a"]) == {"nl", "fr"}
    assert grouped["a"]["fr"]["usps"] == ["FR"]


def test_grouping_drops_malformed_items_rather_than_raising() -> None:
    """This reads a file a human may have hand-edited, on a screen that must still render.

    A crash here takes out the copy review — the last place the text is read as text — over one
    bad line in a file the rest of which is fine.
    """
    grouped = group_results(["not an object", {"language": "nl"}, {"gtin": "a", "language": "nl"}])

    assert set(grouped) == {"a"}
