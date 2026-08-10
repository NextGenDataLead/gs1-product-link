"""The contract between the Publish screen and the gates it renders.

``lib/gates.py`` declares, for each gate, the options an operator may choose. A test already
checks those ids against ``flow-orchestrator/SKILL.md`` in both directions — but nothing checked
that the *screen* offers what a gate declares, and one gate quietly stopped.

The dry run declared ``Proceed`` and ``Cancel`` and rendered neither. Its handler set
``session.answers["dry_run"] = "proceed"`` when the subprocess finished, so the gate was answered
by the run *completing* rather than by anyone approving what it printed, and Cancel was
unreachable at the one gate whose entire purpose is to be read before the real write. A gate that
answers itself is not a gate.

Both checks are AST-only, so they need no NiceGUI and run in CI, which installs ``.[dev]``.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

from lib.gates import BY_ID

_PUBLISH: Final = Path(__file__).resolve().parent.parent.parent / "ui" / "pages" / "publish.py"
_PREFIX: Final = "_gate_"


def _renderers() -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every ``_gate_<id>`` renderer whose id is a real gate, keyed by gate id."""
    tree = ast.parse(_PUBLISH.read_text("utf-8"), filename=str(_PUBLISH))
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            _PREFIX
        ):
            gate_id = node.name[len(_PREFIX) :]
            if gate_id in BY_ID:
                found[gate_id] = node
    return found


def test_the_renderers_are_where_we_think() -> None:
    """A guard on the guard: these checks are worthless if they match nothing."""
    rendered = _renderers()
    assert {"intent", "plan_review", "dry_run", "production"} <= set(rendered), (
        f"expected renderers for the load-bearing gates; found {sorted(rendered)}"
    )


def test_no_gate_with_options_answers_itself() -> None:
    """Writing into ``session.answers`` is the screen deciding on the operator's behalf.

    ``languages`` is exempt by construction rather than by exception: it declares no options, so
    there is nothing for an operator to pick and the selection *is* the answer.
    """
    for gate_id, node in _renderers().items():
        if not BY_ID[gate_id].options:
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Subscript)
                and isinstance(inner.ctx, ast.Store)
                and isinstance(inner.value, ast.Attribute)
                and inner.value.attr == "answers"
            ):
                raise AssertionError(
                    f"_gate_{gate_id} (line {inner.lineno}) writes into session.answers, so the "
                    f"gate answers itself — gate {gate_id!r} declares "
                    f"{[o.value for o in BY_ID[gate_id].options]} for the operator to choose"
                )


#: Gates that declare options the screen does not yet render. Both are informational today and
#: neither is required, so nothing blocks — but their declared options promise behaviour that does
#: not exist (`show-full-diff` renders no fuller diff; `detail` opens nothing), and adding buttons
#: that do not do what they say would be worse than having none. Listed rather than skipped, so
#: the gap is visible and a *new* one still fails.
_KNOWN_UNRENDERED: Final = frozenset({"row_diff", "post_run"})


def _answerable(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether a renderer offers the shared option buttons, or bespoke ones that answer."""
    calls = {
        inner.func.attr
        for inner in ast.walk(node)
        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
    }
    return bool({"_options", "_answer", "answer"} & calls)


def test_every_required_gate_with_options_offers_a_way_to_answer_them() -> None:
    """A required gate the screen cannot answer stops the run dead, or answers itself."""
    for gate_id, node in _renderers().items():
        gate = BY_ID[gate_id]
        if not gate.options or not gate.required:
            continue
        assert _answerable(node), (
            f"_gate_{gate_id} renders no way to answer it: gate {gate_id!r} is REQUIRED and "
            f"declares {[o.value for o in gate.options]}, so the run cannot proceed without the "
            "screen either offering them or answering on the operator's behalf"
        )


def test_the_unrendered_gates_are_exactly_the_known_ones() -> None:
    """Fails if the debt grows — and if it is paid off without updating this list."""
    unrendered = {
        gate_id
        for gate_id, node in _renderers().items()
        if BY_ID[gate_id].options and not _answerable(node)
    }
    assert unrendered == set(_KNOWN_UNRENDERED), (
        f"gates declaring options that the screen never offers: {sorted(unrendered)}; "
        f"the known set is {sorted(_KNOWN_UNRENDERED)}"
    )
