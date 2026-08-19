"""The contract between the Content screen and the scope it is supposed to respect.

The screen showed a *scoped* coverage figure directly above an *unscoped* list of copy, with
nothing to tell them apart. The figures came from the doctor; the list read
``generation_results.json`` off disk and rendered every GTIN in it. That file is written per run
now, but a results file produced against a longer process list carries the same trap.

AST-only, so this needs no NiceGUI and runs in the required CI job rather than the optional one.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_CONTENT: Final = Path(__file__).resolve().parent.parent.parent / "ui" / "pages" / "content.py"


def _tree() -> ast.Module:
    return ast.parse(_CONTENT.read_text("utf-8"), filename=str(_CONTENT))


def _function(name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    found = [
        node
        for node in ast.walk(_tree())
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
    ]
    assert len(found) == 1, f"expected exactly one {name}; found {len(found)}"
    return found[0]


def _calls(node: ast.AST) -> set[str]:
    return {
        name
        for inner in ast.walk(node)
        if isinstance(inner, ast.Call)
        for name in (getattr(inner.func, "id", None) or getattr(inner.func, "attr", None),)
        if name
    }


def _own_calls(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """The calls a function makes itself, not those made by functions nested inside it.

    ``ast.walk`` descends into nested ``def``s, so a closure's call is otherwise attributed to
    every function that encloses it — and this screen's shared refresh is a closure.
    """
    nested = {
        inner
        for child in node.body
        for inner in ast.walk(child)
        if isinstance(inner, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    owned = {inner for child in node.body for inner in ast.walk(child)} - {
        deep for fn in nested for deep in ast.walk(fn)
    }
    return {
        name
        for inner in owned
        if isinstance(inner, ast.Call)
        for name in (getattr(inner.func, "id", None) or getattr(inner.func, "attr", None),)
        if name
    }


def test_the_copy_review_filters_by_scope() -> None:
    """The review must show this run's batch, not every GTIN the file happens to carry.

    Asserted as "it calls the splitter" rather than by inspecting the rendering, because the
    splitter is where the decision lives and ``tests/ui/test_context.py`` covers what it decides.
    """
    review = _function("_review")
    assert "split_results" in _calls(review), (
        "_review does not split the copy by scope, so it is listing every GTIN in the file "
        "under a coverage figure that is scoped to this run"
    )
    # And that it hands over the scope it was given. Calling the splitter with `None` is a legal
    # call that reproduces the defect exactly — every entry comes back as in-scope — so asserting
    # the call alone is not enough.
    call = next(
        inner
        for inner in ast.walk(review)
        if isinstance(inner, ast.Call)
        and (getattr(inner.func, "attr", None) or getattr(inner.func, "id", None))
        == "split_results"
    )
    passed = {arg.id for arg in call.args if isinstance(arg, ast.Name)} | {
        kw.value.id for kw in call.keywords if isinstance(kw.value, ast.Name)
    }
    assert "scope" in passed, (
        "_review calls split_results without passing its `scope` argument, so every entry "
        f"comes back in scope and nothing is filtered; it passes {sorted(passed)}"
    )


def test_the_screen_runs_one_preflight_for_both_sections() -> None:
    """Coverage and the review answer the same question at two zoom levels.

    Fetched separately they would be two subprocesses per render, and — the expensive half — a
    re-check could move the count without moving the list, restoring the very disagreement this
    screen was fixed to remove.
    """
    callers = sorted(
        node.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and "run_json" in _own_calls(node)
    )
    assert callers == ["refresh"], (
        f"expected the single shared refresh to be the only preflight caller; found {callers}"
    )


def test_importing_a_cache_redraws_what_it_invalidated() -> None:
    """Both sections below describe the file the upload just replaced.

    A screen that keeps showing the previous cache after a successful import is the silent
    staleness this project keeps designing against — and the upload handler is the one place that
    knows the file changed.
    """
    assert "refresh" in _calls(_function("upload")), (
        "the upload handler does not refresh, so coverage and the copy below still describe the "
        "cache that was just overwritten"
    )


# --- the producer offered here ------------------------------------------------


def test_generate_is_offered_before_import() -> None:
    """Both write the same file; the one needing no hand-off is the one to reach for first."""
    body = _own_calls(_function("_coverage_and_review"))
    assert {"_generate", "_import"} <= body

    order = [
        node.func.id
        for node in ast.walk(_function("_coverage_and_review"))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"_generate", "_import"}
    ]
    assert order == ["_generate", "_import"]


def test_generate_renders_nothing_clickable_without_a_key() -> None:
    """An action that can only fail is worse than an absence — you must run it to find out.

    The presence check comes from ``env_edit.describe``, which reads ``.env`` as text and returns
    presence and length only. Asserting the early ``return`` is what stops a later edit from
    turning the guard into a band that merely sits above a live button.
    """
    generate = _function("_generate")
    assert "describe" in _own_calls(generate)

    guards = [
        node
        for node in ast.walk(generate)
        if isinstance(node, ast.If) and any(isinstance(stmt, ast.Return) for stmt in ast.walk(node))
    ]
    assert guards, "no early return guarding the key-absent branch"


def test_generate_refreshes_the_coverage_it_just_invalidated() -> None:
    """The figures and the copy below describe the previous file until something says otherwise.

    The upload handler is held to the same rule; a screen that keeps showing the old copy after a
    successful run is the silent staleness this project keeps designing against.
    """
    handlers = [
        node for node in ast.walk(_function("_generate")) if isinstance(node, ast.AsyncFunctionDef)
    ]
    assert len(handlers) == 1, "expected exactly one async click handler"
    assert "refresh" in _calls(handlers[0])
    assert "stream" in _calls(handlers[0]), "must stream, not block: the run takes minutes"
