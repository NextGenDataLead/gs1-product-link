"""Tests for ui/runner.py — the one seam between the shell and the pipeline.

Driven against tiny real subprocesses rather than mocks, because what is being tested *is* the
subprocess behaviour: that the child runs in the repo root, that stderr is kept (every script in
this project writes its summary there and reserves stdout for machine output), that output streams
rather than arriving in a block, and that a timeout still returns what was said before it fired.

Mocking ``subprocess`` here would test that the code calls the function it obviously calls.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ui import REPO_ROOT, runner


def _inline(code: str) -> list[str]:
    """An argv that runs ``code`` in a child interpreter."""
    return ["-c", code]


def test_the_child_runs_in_the_repo_root() -> None:
    """Every output path in this project is built as ``Path("output") / …``, relative to cwd.

    A child launched from anywhere else writes the run log, the plan and the QR files into a
    directory nobody will look in — and reports success doing it.
    """
    result = runner.run(_inline("import os; print(os.getcwd())"))
    assert Path(result.stdout.strip()) == REPO_ROOT


def test_stderr_is_kept_because_that_is_where_the_summary_is() -> None:
    result = runner.run(_inline("import sys; sys.stderr.write('the summary line')"))
    assert result.stderr == "the summary line"


def test_the_exit_code_distinguishes_did_not_start_from_had_errors() -> None:
    """Exit 2 means nothing was attempted; exit 1 means the run happened and some rows failed."""
    assert runner.run(_inline("raise SystemExit(0)")).ok
    assert runner.run(_inline("raise SystemExit(1)")).returncode == runner.EXIT_HAD_ERRORS
    assert runner.run(_inline("raise SystemExit(2)")).config_error


def test_the_command_is_shown_the_way_a_person_would_type_it() -> None:
    """Every screen that runs something shows this, so the operator can reproduce it."""
    result = runner.run(_inline("pass"))
    assert result.display_command.startswith("python -c ")


def test_run_json_returns_the_raw_result_when_the_output_does_not_parse() -> None:
    """A crashed command still said something. Hiding it behind a parse error is worse."""
    payload, result = runner.run_json(_inline("print('not json'); raise SystemExit(2)"))
    assert payload is None
    assert "not json" in result.stdout
    assert result.config_error


def test_run_json_parses_a_clean_payload() -> None:
    payload, _ = runner.run_json(_inline("import json; print(json.dumps([{'status': 'ok'}]))"))
    assert payload == [{"status": "ok"}]


def test_stream_delivers_lines_as_they_arrive_not_all_at_the_end() -> None:
    """The only progress channel a run has. A block at the end would be no better than run()."""
    seen: list[str] = []
    code = "import sys\nfor n in range(3):\n    print(n, flush=True)\nsys.stderr.write('done\\n')"
    result = asyncio.run(runner.stream(_inline(code), seen.append))

    assert seen[:3] == ["0", "1", "2"]
    assert "done" in seen[-1]  # both streams merged, in arrival order
    assert result.ok


def test_a_timeout_still_returns_what_the_run_managed_to_say() -> None:
    """A hung run that reports nothing is worse than one that reports how far it got."""
    seen: list[str] = []
    code = "import time\nprint('started', flush=True)\ntime.sleep(30)"
    result = asyncio.run(runner.stream(_inline(code), seen.append, timeout=1.5))

    assert "started" in seen[0]
    assert any("timed out" in line for line in seen)
    assert not result.ok


def test_the_dot_env_rule_is_enforced_elsewhere() -> None:
    """The shell must not load ``.env`` itself — asserted in ``tests/lib/test_env.py``.

    Kept there rather than duplicated here so every rule about ``.env`` lives in the file that
    owns it, and so there is one place to change when the rule changes.
    """
    assert (REPO_ROOT / "tests" / "lib" / "test_env.py").is_file()


@pytest.mark.parametrize(
    ("build", "expected"),
    [
        (lambda: runner.doctor_argv("acme"), ["-m", "scripts.doctor", "--json", "acme"]),
        (
            lambda: runner.doctor_argv("acme", offline=True),
            ["-m", "scripts.doctor", "--json", "acme", "--offline"],
        ),
        (lambda: runner.doctor_argv(None), ["-m", "scripts.doctor", "--json"]),
        (lambda: runner.run_plan_argv("acme"), ["-m", "scripts.run_plan", "acme"]),
        (
            lambda: runner.parse_export_argv("acme", dry_run=True),
            ["-m", "scripts.parse_export", "acme", "--dry-run"],
        ),
        (
            lambda: runner.build_video_map_argv("acme"),
            ["-m", "scripts.build_video_map", "acme", "--check"],
        ),
        (
            lambda: runner.build_video_map_argv(None),
            ["-m", "scripts.build_video_map", "--check"],
        ),
        (
            lambda: runner.reconcile_argv("acme"),
            ["-m", "scripts.reconcile", "acme", "--json"],
        ),
    ],
)
def test_the_named_commands_are_what_a_person_would_type(
    build: object, expected: list[str]
) -> None:
    assert build() == expected  # type: ignore[operator]


def test_an_absent_client_id_is_omitted_rather_than_passed_empty() -> None:
    """The id is optional when clients.yml defines exactly one client — passing "" is not that."""
    assert "" not in runner.run_plan_argv(None)


def test_the_shell_never_offers_to_re_draft_the_video_mapping() -> None:
    """Draft mode prints a fresh skeleton; a button that ran it could discard client sign-off.

    ``build_video_map`` without ``--check`` is safe in a terminal, where redirecting its output
    over the mapping is a deliberate act. Behind a button it would be one click.
    """
    assert "--check" in runner.build_video_map_argv("acme")
