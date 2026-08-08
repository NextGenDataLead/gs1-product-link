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
| 1 | **Setup** | The operator-facing half of `clients.yml` and `.env`, as a form, with live Test buttons. |
| 2 | **Preflight** | `python -m scripts.doctor`, rendered as a list to work down. Offline by default. |
| 3 | **Data** | Upload the export, prune the process list, read the data-quality report. |
| 4 | **Content** | Import `generated_cache.json`, check its coverage, read the copy. |
| 5 | **Publish** | The nine gates, one at a time. |
| 6 | **Runs** | Every row of every run, as it was recorded at the time. |

### Setup

The client, the site, the environment, the credentials, and every configured file with **how long
ago it was modified**. The export path is authoritative and has no command-line override, so a
workbook saved somewhere new is invisible to the tool — the date beside it is the fastest way to
notice.

The two most expensive mistakes in this pipeline are both *config* mistakes that nothing downstream
notices: pointing at the wrong export, and pointing at production. Both were previously made in a
text editor, in a file whose rules are not visible from inside it. Hence the form, and five things
about it:

- **Only changed fields are written.** The screen shows the *resolved* config, with the `defaults`
  block merged in. Saving all of it would freeze every inherited default into this client's own
  block, so an untouched form writes nothing at all.
- **Everything else in `clients.yml` survives byte for byte** — comments, alignment, quoting style,
  and every block the form does not show. The file is a document, and several of its comments are
  the only record of why a value is what it is.
- **The result is validated before it replaces the file**, by the same `check_config` the doctor
  runs, which reports every offending field rather than the first. A candidate that would not load
  is refused and the file is left alone. The previous version is kept as `clients.yml.bak`.
- **Switching to production asks for the client id, typed in full** — the same decision the
  production gate asks about, made once here instead of once per run. Two further inconsistencies
  the schema cannot express are refused too: a default language that is not in the language list,
  and `production` with no production account or credential names.
- **The client id is not editable.** It is the path to `output/{client}/state.json`, which records
  every GTIN already published. Renaming it orphans that file rather than moving it, and every
  published GTIN would classify as new on the next run.

**Credentials are write-only.** The fields set values in `.env` and never show one back; an empty
box means *leave this one alone*. Values are always quoted, because the commonest credential
failure here is an application password that lost its quotes and was truncated at the first space —
which the screen also reports, as a group count, without disclosing anything. There is **no
Anthropic key field**, and there will not be one.

`gdsn_map`, `acf_map`, `brick_category_map` and `generator` stay read-only, each with the reason
beside it. The first three were settled by a field walk against the live site; `generator` is the
E21 switch, not a preference.

The Test buttons run `python -m scripts.doctor` and show the checks that answer for that part of
the form. They are the preflight's own checks rather than a second opinion — and when the run as a
whole fails on a check the button did not ask about, it says so instead of showing green.

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
| Refuses to replace `clients.yml` with a file that would not load | `ui/config_edit.py`, via `lib/preflight.check_config` |
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
  with editor rights and GS1 production OAuth credentials. The shell never *loads* them: it does
  not call `load_env()` or any dotenv reader, so no secret enters this long-lived process's
  environment and the staging-guard variables are never armed inside it. The subprocesses load the
  file themselves. The Setup screen writes to it and re-applies mode 600, and reads it only far
  enough to say whether a name has a value.
- **No auto-update, no telemetry, no network listener beyond the loopback socket.**
