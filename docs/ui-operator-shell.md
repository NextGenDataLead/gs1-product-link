# The operator shell

A local desktop window over the same commands you would otherwise type. It exists so that the
recurring loop — drop a new export, prune the process list, import the copy, run the flow, read
the result — does not require a terminal, a virtualenv, or knowing which of nine scripts to call.

```bash
pip install -e ".[ui]"
python -m ui
```

It binds to `127.0.0.1` only and opens in a native window rather than a browser tab. `python -m ui
--browser` serves the same pages in a browser on the same loopback address, for a machine with no
webview available.

---

## What it is not

**It has no LLM, no `ANTHROPIC_API_KEY`, and no connection to Anthropic.** Content generation
happens on the maintainer's machine, in a Claude Code session with the `content-generator` skill;
`generated_cache.json` is handed over as a file and uploaded on the Content screen. This machine
never runs `run_generate`.

That is a deliberate split, not an omission. It removes an entire class of IT objection and a
per-token cost from the operator's workstation, at the price of one file changing hands per batch.

**It does not reimplement the pipeline.** Every action is a subprocess running exactly the command
a person would run, from the repository root, and every screen shows that command. So the terminal,
the skills, and [`verifying-live.md`](verifying-live.md) all stay valid as a fallback when something
here is wrong.

**It does not replace the Claude Code flow.** `publish {client} to GS1` still works and still goes
through `flow-orchestrator`. The shell is a second surface over the same gates, not a fork of them.

---

## The screens

| # | Screen | What it is for |
|---|---|---|
| 1 | **Setup** | What this machine is configured to publish, where, and with which credential *names*. Read-only. |
| 2 | **Preflight** | `python -m scripts.doctor`, rendered as a list to work down. Offline by default. |
| 3 | **Data** | Upload the export, prune the process list, read the data-quality report. |
| 4 | **Content** | Import `generated_cache.json`, check its coverage, read the copy. |
| 5 | **Publish** | The nine gates, one at a time. |
| 6 | **Runs** | Every row of every run, as it was recorded at the time. |

### Setup

Shows the client, the site, the environment, and every configured file with **how long ago it was
modified**. The export path is authoritative and has no command-line override, so a workbook saved
somewhere new is invisible to the tool — the date beside it is the fastest way to notice.

Credential *names* only, never values. Whether a name resolves is the preflight's question.

### Preflight

Runs the doctor in a subprocess and renders each check with its remedy. Two buttons: offline (no
credentials, no sockets) and everything. The full run authenticates against WordPress and mints a
GS1 token; both are read-only.

The first line to read is **"What a run would touch"** — how many products survive the process list
and the video allowlist. Every check below it reports on that scope rather than the whole
catalogue.

### Data

Uploading the export **replaces the configured path in place**, keeping the previous file as
`.bak.xlsx`. Writing anywhere else would produce a file the tool cannot see.

The process-list grid is for the one thing the operator does with that file: **deleting rows**.
Every other column is preserved verbatim — they are your working notes. Saving keeps the previous
version, and refuses to write a list with no GTINs at all, because that would produce an empty plan
and a run that reports success having published nothing.

### Content

Import the cache, then look at the **coverage** figures before the copy. A cache entry's
fingerprint covers `{inputs, language, prompt_version}`, so editing one product in the feed makes
that unit *pending* again — and a pending unit with no producer on this machine is dropped from the
plan (E21). The screen lists the pending units by GTIN and language, so "request a fresh cache" is
an instruction rather than a hunch.

### Publish

The gates come from `lib/gates.py`, which `flow-orchestrator/SKILL.md` is checked against by a test
in both directions. Each is shown with **why it exists**, not only what it asks — a form that asks
without saying why teaches you to answer without reading, and this flow's whole cost is
concentrated in one unreviewed click.

`ui/session.py` **refuses to build the command** while any required gate is outstanding. Not a
warning: a function that raises. That is the improvement over prose, which can be paraphrased,
compressed, or skipped.

- Choosing `links` or `both` turns the banner red and inserts the production gate, which needs the
  client id typed in full.
- The dry run is mandatory and runs the same command with `--dry-run` and every other flag
  identical.
- `--i-understand-production` is appended **only** after the production gate is answered, and never
  on a dry run.
- An empty plan is refused rather than run. Publishing nothing successfully is the one outcome
  indistinguishable from success.

### Runs

Reads `output/{client}/runs/*.jsonl`, newest first, and distinguishes a **partial** log — a run
that stopped mid-way — from a finished one. That is the case that matters most: live pages and
permanent GS1 records may already exist for the rows that landed.

---

## Where the safety actually lives

| Guard | Where |
|---|---|
| Refuses a real production run without `--i-understand-production` | `scripts/run_execute.py` |
| Refuses a `--only links` GTIN whose target does not serve | `scripts/run_execute.py` |
| Refuses to build a command past an unanswered required gate | `ui/session.py` |
| Which gates exist, and which are non-negotiable | `lib/gates.py`, checked against `SKILL.md` |
| The prompt text a model reads | `.claude/skills/flow-orchestrator/SKILL.md` |

The first two are inherited unchanged, because the shell subprocesses the scripts rather than
importing them. That is also why it cannot import `main()`: `load_env()` lives in each script's
`__main__` block on purpose, so an in-process call would have **no credentials** — and calling
`load_env()` in the shell would put production secrets into a long-lived desktop process and arm
the staging-guard variables inside it. A test asserts no module under `ui/` does either.

---

## For IT

- **Loopback only.** Port 8477, `127.0.0.1`, native window — no shareable URL.
- **No Anthropic egress and no LLM credential** on this machine.
- **Outbound**: the client's WordPress site, `gs1nl-api.gs1.nl` (or its acceptance host), and the
  image hosts named in the product feed. That last one is currently unconstrained by an allowlist,
  which is a fair question to ask about.
- **Credentials** are the pre-existing `.env` at `chmod 600` — a WordPress application password
  with editor rights and GS1 production OAuth credentials. The shell does not read them; the
  subprocesses do.
- **No auto-update, no telemetry, no network listener beyond the loopback socket.**
