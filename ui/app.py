"""Routing and startup for the operator shell.

Bound to ``127.0.0.1`` and opened in a native window rather than a browser tab. The window is not
cosmetic: this process reaches a live WordPress site and the GS1 production resolver, and a
listening socket on a workstation is a thing IT will ask about. One that answers only to loopback
and has no visible URL to share is a much shorter conversation.

``reload=False`` because the reloader re-executes the module, and a shell that restarts itself
mid-run would orphan a subprocess that is writing permanent records.
"""

from __future__ import annotations

from typing import Final

from nicegui import ui

from ui import theme
from ui.pages import content, data, preflight, publish, runs, setup, video_map

#: Loopback only, and a port unlikely to collide with a dev server the operator also runs.
HOST: Final = "127.0.0.1"
PORT: Final = 8477

TITLE: Final = "GS1 Digital Link — operator shell"


@ui.page("/")
def _setup() -> None:
    setup.render()


@ui.page("/preflight")
def _preflight() -> None:
    preflight.render()


@ui.page("/data")
def _data() -> None:
    data.render()


# Not in the rail: this is one input file's editor, reached from the Data screen. The rail is
# numbered, and each screen's heading says "Step N", so an entry here would number a detour
# as a step of the run.
@ui.page("/videos")
def _video_map() -> None:
    video_map.render()


@ui.page("/content")
def _content() -> None:
    content.render()


@ui.page("/publish")
def _publish() -> None:
    publish.render()


@ui.page("/runs")
def _runs() -> None:
    runs.render()


def main(*, native: bool = True) -> None:
    """Start the shell.

    ``native=False`` serves it in a browser instead, for a machine with no webview available —
    still on loopback, and still the same pages.
    """
    theme.install()
    ui.run(
        host=HOST,
        port=PORT,
        title=TITLE,
        native=native,
        reload=False,
        show=not native,
        favicon="🔗",
        dark=None,  # follow the operating system rather than impose a mood
    )
