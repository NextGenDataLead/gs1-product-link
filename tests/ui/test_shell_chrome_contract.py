"""The chrome every screen inherits: how it speaks, and what it may cost to draw.

Two rules that are cheap to hold and expensive to notice the loss of.

**One helper per outcome, not twenty-one calls.** The screens reached for ``ui.notify`` directly
for a long time, and drifted: four different timeouts, two spellings of the ``type`` argument, and
no dismiss button anywhere — so the failure message, the one that is the only account of why
nothing happened, was as likely as any other to have already vanished when the operator looked up.

**The rail must stay cheap.** ``theme.page`` runs on every render of every screen, so anything it
reads is read seven times a walk. ``context.rail_facts`` exists to give each step a fact, and the
tempting next fact — units with copy, checks passing — costs a ``scripts.doctor`` subprocess of
about a quarter-second. Adding one there would slow every screen in the shell to buy a number that
is already on the screen that owns it.

AST-only, so this needs no NiceGUI and runs in the required CI job rather than the optional one.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_ROOT: Final = Path(__file__).resolve().parent.parent.parent
_PAGES_DIR: Final = _ROOT / "ui" / "pages"
_CONTEXT: Final = _ROOT / "ui" / "context.py"
_THEME: Final = _ROOT / "ui" / "theme.py"

#: What a screen says instead. Each owns its own duration, so how long a message stays up is a
#: property of the outcome rather than of whoever wrote the call.
_NOTIFY_HELPERS: Final = frozenset({"notify_ok", "notify_warning", "notify_problem"})


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text("utf-8"), filename=str(path))


def _calls(node: ast.AST) -> list[ast.Call]:
    return [inner for inner in ast.walk(node) if isinstance(inner, ast.Call)]


def _attribute_call(call: ast.Call) -> tuple[str, str] | None:
    """``theme.notify_ok(...)`` → ``("theme", "notify_ok")``; anything else → ``None``."""
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id, func.attr
    return None


def test_no_screen_calls_ui_notify_directly() -> None:
    """A toast an operator can miss is a toast that did not happen."""
    offenders = [
        f"{path.name}:{call.lineno}"
        for path in sorted(_PAGES_DIR.glob("*.py"))
        for call in _calls(_tree(path))
        if _attribute_call(call) == ("ui", "notify")
    ]
    assert not offenders, (
        f"raw ui.notify at {offenders} — use theme.notify_ok / notify_warning / notify_problem, "
        "which own the duration and the dismiss button"
    )


def test_the_theme_offers_a_helper_for_every_outcome() -> None:
    """The rule above is only enforceable while there is somewhere to go instead."""
    defined = {
        node.name
        for node in ast.walk(_tree(_THEME))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert defined >= _NOTIFY_HELPERS, (
        f"missing from ui/theme.py: {sorted(_NOTIFY_HELPERS - defined)}"
    )


def test_the_rail_facts_run_no_subprocess() -> None:
    """Seven screens pay for whatever this function does. Keep it to ``stat``.

    Named by call rather than by import: ``ui.context`` importing ``ui.runner`` would be the
    obvious tell, but the function only has to *call* one to cost the quarter-second, and a later
    edit that adds ``subprocess`` or ``asyncio.to_thread`` directly would slip past an import
    check entirely.
    """
    functions = {
        node.name: node
        for node in ast.walk(_tree(_CONTEXT))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert "rail_facts" in functions, "ui/context.py no longer defines rail_facts"

    forbidden = {
        "run",
        "run_json",
        "run_off_the_loop",
        "run_json_off_the_loop",
        "Popen",
        "to_thread",
    }
    called = set()
    for call in _calls(functions["rail_facts"]):
        pair = _attribute_call(call)
        if pair:
            called.add(pair[1])
        elif isinstance(call.func, ast.Name):
            called.add(call.func.id)

    assert not called & forbidden, (
        f"rail_facts calls {sorted(called & forbidden)} — it runs on every render of every "
        "screen, so a subprocess here is a subprocess seven times a walk. The counts that need "
        "one belong on the screen that owns them."
    )


def test_the_theme_imports_nothing_but_nicegui() -> None:
    """The facts are passed in, not fetched.

    ``theme.page`` taking a ``facts`` mapping rather than calling ``ui.context`` itself is what
    makes the check above meaningful: with the import absent there is no path from the chrome to
    a subprocess at all, whatever a later edit to ``rail_facts`` does.
    """
    imported = {
        node.module.split(".")[0]
        for node in ast.walk(_tree(_THEME))
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name.split(".")[0]
        for node in ast.walk(_tree(_THEME))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not (imported & {"lib", "ui"}), (
        f"ui/theme.py imports {sorted(imported & {'lib', 'ui'})} — the chrome renders what it is "
        "given, so that it cannot acquire a way to read files or run commands on every page load"
    )
