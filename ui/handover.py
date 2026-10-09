"""Give the operator a report file: in their Downloads folder, opened in their default app."""

from __future__ import annotations

from pathlib import Path

from ui import runner, theme


def give(path: Path) -> None:
    """Copy ``path`` to Downloads, open it, and say where it is — or why it is not there."""
    try:
        copy = runner.hand_over(path)
    except OSError as exc:
        theme.notify_problem(f"Could not save {path.name}: {exc}")
        return
    theme.notify_ok(f"Saved to {copy} — opening it")
