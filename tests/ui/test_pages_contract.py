"""The screens, against the NiceGUI they will actually run on.

Every other test under ``tests/ui/`` either parses source with :mod:`ast` or imports one of the
NiceGUI-free modules (``ui.session``, ``ui.config_edit``, ``ui.runner`` …). That is deliberate —
it is what lets them run in the required CI job, which installs ``.[dev]`` to prove ``lib`` never
grows a UI dependency. The cost was that **no test imported a screen at all**, so ``ui/pages/``
was the one surface CI never touched, and an unbounded ``nicegui>=2.0`` broke two screens without
anything going red (#52, and the pattern behind #53 and #54).

This module is the other side of that split. It skips wholesale where NiceGUI is absent and runs
in the ``Operator shell (ui extra)`` job, where it asserts the two things an AST check cannot:
that every screen still *imports* against the installed NiceGUI, and that the routes and the left
rail still agree about which screens exist.

It is not a rendering test. It cannot tell you a screen looks right — only that a version bump
has not made one unreachable or unimportable, which is the failure that actually happened.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path
from typing import Final

import pytest

pytest.importorskip("nicegui", reason="the ui extra is not installed here")

from nicegui import app as nicegui_app  # noqa: E402

from ui import theme  # noqa: E402

#: Every screen module. Listed by hand rather than globbed: a screen that stops being imported
#: here should be a deliberate deletion, not a file that quietly dropped out of a pattern.
PAGE_MODULES: Final = (
    "ui.pages.setup",
    "ui.pages.preflight",
    "ui.pages.data",
    "ui.pages.content",
    "ui.pages.publish",
    "ui.pages.runs",
    "ui.pages.video_map",
)

#: Routes that are registered but deliberately absent from the rail, with how they are reached.
#: A screen added without either an entry here or a rail entry is unreachable, which is the point
#: of checking both directions.
UNLISTED_ROUTES: Final[dict[str, str]] = {
    "/videos": "the video mapping editor, linked from the Data screen — one input file's editor "
    "rather than a step of the run, and the rail is numbered by step",
}

#: NiceGUI's own machinery, not ours.
_INTERNAL_PREFIXES: Final = ("/_nicegui", "/docs", "/redoc", "/openapi.json")

#: A hardcoded step eyebrow — what every screen used to pass to ``theme.heading``.
_STEP_LITERAL: Final = re.compile(r"Step \d+")

#: Where each screen's source lives, for the AST check below.
_PAGES_DIR: Final = Path(__file__).resolve().parent.parent.parent / "ui" / "pages"
_MODULE_PATHS: Final = {name: _PAGES_DIR / f"{name.split('.')[-1]}.py" for name in PAGE_MODULES}


def _our_routes() -> set[str]:
    """Every path this application registers, minus NiceGUI's internals."""
    importlib.import_module("ui.app")
    paths = {path for route in nicegui_app.routes if (path := getattr(route, "path", None))}
    return {p for p in paths if not p.startswith(_INTERNAL_PREFIXES)}


@pytest.mark.parametrize("module", PAGE_MODULES)
def test_every_screen_imports(module: str) -> None:
    """A screen that cannot be imported cannot be opened, and only the terminal would say so."""
    importlib.import_module(module)


def test_the_application_module_imports() -> None:
    """``ui.app`` wires the routes; importing it is what registers them.

    Safe headless: ``pywebview`` is only reached inside ``main()``, which this never calls.
    """
    importlib.import_module("ui.app")


def test_the_theme_installs() -> None:
    """``theme.install()`` runs once at startup, so a broken call would surface only there."""
    theme.install()


def test_every_rail_entry_has_a_route() -> None:
    """A rail entry with no route is a dead link on the one navigation the shell has."""
    routes = _our_routes()
    for label, path, step in theme.NAV:
        assert path in routes, f"rail entry {step} {label!r} points at {path}, which has no route"


def test_every_route_is_reachable() -> None:
    """The other direction: a screen with no way to reach it is a screen nobody will use."""
    listed = {path for _, path, _ in theme.NAV} | set(UNLISTED_ROUTES)
    orphans = _our_routes() - listed
    assert not orphans, (
        f"registered but unreachable: {sorted(orphans)} — add a rail entry in ui/theme.py NAV, "
        "or record how it is reached in UNLISTED_ROUTES here"
    )


# --- The numbering is an assertion, so it has one source ----------------------


def test_the_rail_is_numbered_in_order_from_one() -> None:
    """The numbers are the workflow. A gap or a repeat makes them decoration."""
    assert [number for _, _, number in theme.NAV] == [str(n) for n in range(1, len(theme.NAV) + 1)]


def test_preflight_comes_after_the_screens_it_depends_on() -> None:
    """Four of the doctor's checks answer "Run `parse_export` first" — which is the Data screen.

    Preflight sat at step 2 for that whole time, so step 2 told an operator to go and do step 3
    and come back, and on a fresh machine most of the list could not answer its own questions
    yet. This is the ordering, asserted rather than remembered.
    """
    order = [label for label, _, _ in theme.NAV]

    assert order.index("Preflight") > order.index("Data")
    assert order.index("Preflight") > order.index("Content")
    assert order.index("Preflight") < order.index("Publish")


def test_no_screen_spells_its_own_step_number() -> None:
    """``theme.step`` reads the rail, so a reorder is one edit rather than seven.

    They *were* seven: the rail carried the numbers and every screen also hardcoded its own into
    ``theme.heading``. Two lists that had to be renumbered together, with nothing to notice when
    only one was.
    """
    for module in PAGE_MODULES:
        path = _MODULE_PATHS[module]
        for node in ast.walk(ast.parse(path.read_text("utf-8"), filename=str(path))):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            assert not _STEP_LITERAL.fullmatch(node.value), (
                f"{path.name}:{node.lineno} spells {node.value!r} — use theme.step('<rail label>') "
                "so the rail stays the only place the order is written"
            )
