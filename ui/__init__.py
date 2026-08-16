"""The local operator shell: a desktop window over the same CLIs a person would type.

Run it with ``python -m ui`` after ``pip install -e ".[ui]"``.

Three design decisions shape everything here, and each is load-bearing.

**The UI subprocesses the scripts; it never imports their ``main()``.** ``load_env()`` lives in
each script's ``if __name__ == "__main__":`` block by design — nine test modules call ``main()``
directly, and ``.env`` carries the four staging-guard variables, so loading it in the test path
would arm tests that write to live WordPress and the GS1 production resolver. A shell that
imported ``main()`` would therefore have **no credentials**; one that called ``load_env()`` itself
would re-arm that hazard inside a long-lived process. Subprocessing sidesteps both, and inherits
the production guard, the ``state.json`` writes, the run JSONL and the ``--only links``
target-serves refusal unchanged — all of which live in ``scripts/``, not ``lib/``.

It also means the UI runs exactly the command a human would, so ``docs/verifying-live.md``, the
skills and the terminal all stay valid as a fallback when something here is wrong.

**No LLM, no ``ANTHROPIC_API_KEY``, no Anthropic egress.** Content generation happens on the
maintainer's machine, in a Claude Code session with the ``content-generator`` skill;
``generation_results.json`` is handed over as a file and uploaded here. This machine never runs
``run_generate``. See ``docs/ui-operator-shell.md``.

**The gates come from** :mod:`lib.gates`, **not from this package.** They are the safety
mechanism, they are also written as prose in ``flow-orchestrator/SKILL.md``, and two
implementations of one safety contract drift silently. A test asserts the two agree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

#: The repository root. Every output path in this project is built as ``Path("output") / …``,
#: relative to the working directory, so every subprocess is launched from here.
REPO_ROOT: Final = Path(__file__).resolve().parent.parent

__all__ = ["REPO_ROOT"]
