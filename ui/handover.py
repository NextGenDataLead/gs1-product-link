"""Give the operator a report file — the way their screen can actually receive it.

In the native window (the default) there is no browser for a download to land in, so the file
is copied to their Downloads folder and opened in their default app (operator, 2026-10-09).

Served to a browser instead — ``--browser``, or the container image — the browser *is* the
operator's, and a copy into Downloads would land on the serving side: inside a container, a
folder the operator cannot reach. There the browser downloads it, as any web page would.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from ui import runner, theme

#: Set once at startup by :func:`ui.app.main`, from how the shell is being served.
_through_browser = False


def deliver_through_browser(enabled: bool) -> None:
    """Choose how :func:`give` hands files over; called once, before the first page renders."""
    global _through_browser  # noqa: PLW0603 — one process, one serving mode, fixed at startup
    _through_browser = enabled


def give(path: Path) -> None:
    """Hand ``path`` to the operator, and say where it went — or why it did not."""
    if _through_browser:
        ui.download.file(path)
        theme.notify_ok(f"Downloading {path.name}")
        return
    try:
        copy = runner.hand_over(path)
    except OSError as exc:
        theme.notify_problem(f"Could not save {path.name}: {exc}")
        return
    theme.notify_ok(f"Saved to {copy} — opening it")
