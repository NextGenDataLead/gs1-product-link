# GS1 Digital Link Orchestrator — working notes for Claude Code

This tool publishes real product pages to a live WordPress site and registers **permanent** records
in the GS1 production resolver. Read [`docs/setup.md`](docs/setup.md) before acting on anything here.

## Publishing goes through `flow-orchestrator`. Always.

The operator gates live **only** in [`.claude/skills/flow-orchestrator/SKILL.md`](.claude/skills/flow-orchestrator/SKILL.md).
Calling `scripts/run_execute.py` directly bypasses every one of them.

When the operator asks to publish — *"publish {client} to GS1"*, *"run the GS1 pipeline for
{client}"*, or the older short forms *"run for {client}"* / *"process {client}"* — **load that skill
and follow it step by step.** If for any reason it is not available as a skill, read the file and
follow it anyway. Do not improvise an equivalent flow: the gates *are* the safety mechanism.

**Publishing has three modes, all through that one sequence:** `/gs1-pages` (WordPress pages only —
reversible), `/gs1-links` (Digital Links only, aimed at pages that already exist — **permanent**),
and `/gs1-publish` (both). Those three skills are thin: each pins the mode and delegates to
`flow-orchestrator`, which supplies `run_execute --only`. A request phrased in plain English for one
leg goes to `flow-orchestrator` too — it classifies the mode at gate 0 and confirms it. Never guess
toward the more destructive mode.

The other five skills in `.claude/skills/` cover the individual steps (parse, generate copy, pages,
Digital Link, QR) and have their own trigger phrases.

## Invariants — these were each learned the hard way

- **A GS1 Digital Link record can never be deleted.** The v2 API has no DELETE; retraction only
  clears links and disables the record. Every write against a real GTIN is permanent.
- **A real production run needs `--i-understand-production`.** The skill appends it *after* the
  operator confirms at a gate — never before, and never on the operator's behalf. (In `pages` mode
  that confirmation is gate 0; the separate environment gate is skipped there because nothing
  irreversible follows.)
- **`--only links` refuses a GTIN whose target URL does not serve.** That check is in
  `run_execute`, not in a skill, precisely because prose can be skipped. Never route around it — fix
  where the page actually is.
- **Dry-run first, always.** `--dry-run` writes nothing.
- **The ACF write path fails silently.** A `200` proves the post exists, not that its fields landed.
  Verify by fetching the rendered HTML.
- **Test resolution with `GET`, not `HEAD`.** `id.gs1.org` 404s to HEAD and 307s to GET.
- **Never run `pytest -m staging` casually.** Those tests write to live WordPress and GS1 production.
  A bare `pytest` is safe: `addopts = "-m 'not staging'"` deselects them.
- **Never invent product data.** Blank or wrong source values get fixed in MyGS1, not filled in
  downstream. `python -m scripts.report_quality` is how they surface.

## There is a second surface: the local operator shell

`pip install -e ".[dev,ui]"` then `python -m ui` — a desktop window over the same commands, for an
operator repeating a known loop. It **subprocesses the scripts and must never import their
`main()`** (see the `.env` rule below), and `ui/session.py` **raises** rather than building a run
command while a required gate is unanswered. It holds no LLM credential and never reaches Anthropic.

It is also the only thing that **writes `clients.yml` and `.env`**, on the Setup screen.
`ui/config_edit.py` edits the YAML **as text** — that file is a document whose comments are often
the only record of why a value is what it is, so it is never round-tripped through a YAML dumper —
and validates the candidate with `lib/preflight.check_config` before replacing anything. It still
does not *load* `.env`; it reads it only far enough to say whether a name has a value.

The gates themselves live in **`lib/gates.py`** as data, and `flow-orchestrator/SKILL.md` carries a
**Gate index** table that `tests/lib/test_gates.py` checks in both directions. Adding a gate to one
without the other fails CI — that check exists because two implementations of one safety contract
drift silently. Read `docs/ui-operator-shell.md` before changing either.

## Layout

- `lib/` — the library. `scripts/` — ten CLI entry points. `ui/` — the operator shell (optional
  `[ui]` extra; nothing in `lib/` or `scripts/` imports it, and the suite passes without it).
  `mcps/` — three TypeScript MCP servers (unpublished by choice, see `docs/OPEN_DECISIONS.md` OD-2).
- **Run `python -m scripts.doctor` before a wave.** It is the preflight: config, scope, cache
  coverage, credentials, reachability. Exit 1 on any failure; `--offline` skips everything needing
  a secret or a socket, `--json` is what the shell parses.
- **Credentials come from `.env`**, loaded by `lib/env.py` `load_env()` from each script's
  `if __name__ == "__main__":` block — **never from `main()`**, which the tests call directly, and
  **never from anywhere under `ui/`**, which would put production secrets in a long-lived desktop
  process and arm the staging guards inside it. `tests/lib/test_env.py` enforces both with an AST
  check; it is not boilerplate.
- `clients.yml` (gitignored) holds config and the **names** of env vars, never values. `client_id` is
  optional on every script when exactly one client is defined.
- CI: `ruff check`, `ruff format --check`, `mypy --strict lib`, `pytest`.
- `main` is branch-protected — changes go through a PR.
