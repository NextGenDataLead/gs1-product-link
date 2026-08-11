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


def test_every_gate_offers_the_options_it_declares_for_this_surface() -> None:
    """No exception list: what a screen must render is derived from the gates themselves.

    This used to be a hand-maintained set of two "known unrendered" gates, which is a note about
    debt rather than a check — it goes stale in both directions, and it cannot tell an option
    that is *missing* from one that could never have been here. ``chat_only`` says which is which
    in the data, so a gate with nothing for this surface is exempt by construction, and anything
    else must offer what it declares.
    """
    unrendered = {
        gate_id
        for gate_id, node in _renderers().items()
        if BY_ID[gate_id].shell_options and not _answerable(node)
    }
    assert not unrendered, (
        f"gates declaring options this screen never offers: {sorted(unrendered)}. Either render "
        "them, or mark the ones only the chat flow can honour `chat_only=True` in lib/gates.py "
        "with the reason in the gate's purpose"
    )


def test_a_chat_only_option_is_never_rendered_as_a_button() -> None:
    """The screen renders ``shell_options``, so this holds by construction — asserted anyway.

    A button reading "Explain each error" that explains nothing is worse than no button: it
    teaches an operator that the controls on this screen are decorative, on the one screen where
    reading before clicking is the entire safety mechanism.
    """
    source = _PUBLISH.read_text("utf-8")
    assert "gate.options" not in source, (
        "the publish screen iterates `gate.options`; it must render `gate.shell_options` so a "
        "chat-only option cannot become a button"
    )
    chat_only = [o for gate in BY_ID.values() for o in gate.options if o.chat_only]
    assert chat_only, "nothing is marked chat_only — this check would pass vacuously"
    for option in chat_only:
        assert option not in [o for gate in BY_ID.values() for o in gate.shell_options]
