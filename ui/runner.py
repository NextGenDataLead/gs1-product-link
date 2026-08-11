"""Run the project's CLIs as subprocesses and stream what they say.

The single seam between the shell and the pipeline. Everything the UI causes to happen goes
through here, which is what makes the claim "the UI runs exactly the command a human would"
checkable rather than aspirational: there is one place to look.

Why a subprocess rather than an import — see :mod:`ui`. The short version is that ``load_env()``
lives in each script's ``__main__`` block on purpose, so an in-process call would have no
credentials, and calling ``load_env()`` here would arm the staging guards inside a long-lived
process. Subprocessing also inherits the production guard, the ``state.json`` writes, the run
JSONL and the ``--only links`` refusal, all of which live in ``scripts/``.

``sys.executable`` is the interpreter, so the child runs in the same virtualenv. This is also why
PyInstaller is ruled out for packaging: bundling makes ``sys.executable`` the bundled app rather
than an interpreter, and ``-m scripts.…`` stops working.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from ui import REPO_ROOT

#: Exit codes are uniform across all the scripts (docs/troubleshooting.md).
EXIT_OK: Final = 0
EXIT_HAD_ERRORS: Final = 1
EXIT_CONFIG_ERROR: Final = 2

#: How long to let a single CLI run before giving up. Generous: a real publish of a few dozen
#: products makes hundreds of HTTP calls against someone else's WordPress, with retries.
DEFAULT_TIMEOUT_SECONDS: Final = 60 * 30


@dataclass(frozen=True)
class CommandResult:
    """What a finished subprocess reported.

    ``stderr`` matters more than ``stdout`` here: every script in this project writes its human
    summary to stderr and reserves stdout for machine output. A caller that reads only stdout
    gets silence from a run that said plenty.
    """

    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """Whether every row succeeded. Exit 1 means the run happened and some rows failed."""
        return self.returncode == EXIT_OK

    @property
    def config_error(self) -> bool:
        """Whether the run never started — bad config, missing credentials, refused production."""
        return self.returncode == EXIT_CONFIG_ERROR

    @property
    def display_command(self) -> str:
        """The command as a person would type it, for showing beside the output.

        Shown on every screen that runs something. An operator who can see the command can
        reproduce it in a terminal, ask someone about it, or check it against the docs — none of
        which is possible if the UI only reports what it decided to say about the result.
        """
        return " ".join(["python", *self.argv])


def _child_env() -> dict[str, str]:
    """The environment a CLI child gets.

    Passed through unchanged but for ``PYTHONUNBUFFERED``, so streamed output arrives line by line
    instead of in a block at the end — the whole point of streaming it.

    Deliberately **not** loaded from ``.env`` here. The child does that itself, in its own
    ``__main__`` block, which is the arrangement ``tests/lib/test_env.py`` enforces.
    """
    return {**os.environ, "PYTHONUNBUFFERED": "1"}


def run(argv: Sequence[str], *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> CommandResult:
    """Run a CLI to completion and return everything it said. Blocking.

    For the quick, offline commands — ``doctor``, ``run_plan``, ``report_quality`` — where there
    is nothing to watch and the answer is the point.
    """
    completed = subprocess.run(  # noqa: S603 — argv is built in code, never from operator text
        [sys.executable, *argv],
        cwd=REPO_ROOT,
        env=_child_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return CommandResult(
        argv=list(argv),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


async def stream(
    argv: Sequence[str],
    on_line: Callable[[str], None],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> CommandResult:
    """Run a CLI, calling ``on_line`` for each line as it arrives, and return the result.

    For ``run_execute``, which is the only command long enough to need watching. Both streams are
    merged in arrival order rather than kept apart, because the interleaving is the information:
    a warning that lands between two rows belongs between them.

    The child is killed if it outlives ``timeout``, and what it had already said is returned — a
    hung run that reports nothing is worse than a hung run that reports how far it got.
    """
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        *argv,
        cwd=REPO_ROOT,
        env=_child_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    lines: list[str] = []

    async def pump() -> None:
        assert process.stdout is not None
        async for raw in process.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            lines.append(line)
            on_line(line)

    try:
        await asyncio.wait_for(pump(), timeout=timeout)
        returncode = await process.wait()
    except TimeoutError:
        process.kill()
        await process.wait()
        message = f"[shell] timed out after {timeout:.0f}s and was stopped"
        lines.append(message)
        on_line(message)
        returncode = EXIT_HAD_ERRORS

    return CommandResult(argv=list(argv), returncode=returncode, stdout="\n".join(lines), stderr="")


def run_json(argv: Sequence[str]) -> tuple[Any, CommandResult]:
    """Run a CLI that emits JSON on stdout and parse it.

    Returns ``(None, result)`` rather than raising when the output does not parse, so a caller can
    show the raw text. A crashed command is still a command that said something, and hiding what
    it said behind a parse error is how a UI becomes less useful than a terminal.
    """
    result = run(argv)
    try:
        return json.loads(result.stdout), result
    except json.JSONDecodeError:
        return None, result


# --- The commands, named ------------------------------------------------------
#
# One function per pipeline step, so no screen builds an argv inline. The publish argv is the
# exception: it comes from lib.gates.run_execute_argv, because it is part of the gate contract
# rather than a convenience.


def doctor_argv(client_id: str | None, *, offline: bool = False) -> list[str]:
    """The preflight, as JSON."""
    argv = ["-m", "scripts.doctor", "--json"]
    if client_id:
        argv.append(client_id)
    if offline:
        argv.append("--offline")
    return argv


def parse_export_argv(client_id: str | None, *, dry_run: bool = False) -> list[str]:
    """Workbook → ``products.json``."""
    argv = ["-m", "scripts.parse_export"]
    if client_id:
        argv.append(client_id)
    if dry_run:
        argv.append("--dry-run")
    return argv


def run_plan_argv(client_id: str | None) -> list[str]:
    """Classify every unit against prior state."""
    return ["-m", "scripts.run_plan", *([client_id] if client_id else [])]


def report_quality_argv(client_id: str | None) -> list[str]:
    """Render every issue file into one worklist."""
    return ["-m", "scripts.report_quality", *([client_id] if client_id else [])]


def build_video_map_argv(client_id: str | None, *, check: bool = True) -> list[str]:
    """The video-mapping coverage gate.

    Only the ``--check`` half is offered here. Draft mode prints a fresh skeleton to stdout, and
    a screen that could re-draft the mapping would be a screen that can discard client sign-off —
    so drafting stays a terminal job, where redirecting the output is a deliberate act.
    """
    argv = ["-m", "scripts.build_video_map", *([client_id] if client_id else [])]
    if check:
        argv.append("--check")
    return argv
