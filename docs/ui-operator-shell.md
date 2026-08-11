# The operator shell

A local desktop window over the same commands you would otherwise type. It exists so that the
recurring loop — drop a new export, prune the process list, import the copy, run the flow, read
the result — does not require a terminal, a virtualenv, or knowing which of eleven scripts to call.

On the operator's machine there is nothing to install first and nothing to type: double-click
**`install.command`** (macOS) or **`install.bat`** (Windows), then **`start.command`** /
**`start.bat`**. See [`operator-install.md`](operator-install.md), which also covers the two ways a
managed machine refuses to open an unsigned file.

From a development clone it is the same program, started the usual way:

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
| 2 | **Data** | Upload the export, prune the process list, edit the video mapping, read the data-quality report. |
| 3 | **Content** | Import `generated_cache.json`, check its coverage, read the copy. |
| 4 | **Preflight** | `python -m scripts.doctor`, rendered as a list to work down. Offline by default. |
| 5 | **Publish** | The nine gates, one at a time. |
| 6 | **Runs** | Every row of every run, as it was recorded at the time, and whether the site agrees. |

Configure the machine · load this wave's inputs · check *this wave* · publish it · read what ran.

**Preflight used to sit at 2, and that was wrong.** Four of the doctor's checks have the remedy
"Run `parse_export` first" — which is the Data screen — so step 2 told an operator to go and do
step 3 and come back, and on a machine being set up from scratch most of the list could not answer
its own questions yet. Its headline is "N of M in scope", a statement *about the export just
loaded*, so it belongs after the loading. Nothing is lost by moving it: the credential checks it
also carries are on the Setup screen's Test buttons, at the moment the field is edited. The order
lives in `ui/theme.py`'s `NAV` and nowhere else — each screen reads its own number from it.

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

It runs the offline checks on arrival, so the screen is never blank — which is also why the
buttons need to *look* like they did something. The subprocess runs off the event loop and the
buttons disable while it does; without that, a blocking `subprocess.run` in a click handler held
the loop until it had already finished, so "running…" never reached the browser and the screen
looked identical from click to result. Each result carries the time it finished, because on a
healthy machine an identical list is exactly what a working re-run produces.

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

### Video mapping

Linked from Data rather than sitting in the rail: it is one input file's editor, and the rail is
numbered by step. It exists because that file decides whether a product can be published at all —
with `media.restrict_to_mapped_gtins` on, a product without a confirmed video in **every** language
never reaches the plan, so an operator could complete every screen and still produce an empty plan
with the fix available only in a text editor.

It lists every file per language with its state (unset · confirmed · `skip` · not on disk), offers
`build_video_map`'s ranked fuzzy hints as *suggestions that fill the box*, and stages edits until
one Save. Three things it will not do:

- **Re-draft the file.** Confirmed rows are client sign-off. Drafting stays a terminal job, where
  redirecting the output over the mapping is a deliberate act rather than a click.
- **Round-trip the YAML.** Each row's trailing comment records which fuzzy hint its GTIN came from
  — the evidence behind the sign-off — so `ui/video_map_edit.py` rewrites one line at a time, in the
  spirit of `ui/config_edit.py` on `clients.yml`.
- **Write a file that lost a row.** Nothing here deletes one, so a row that has disappeared is a
  fault in the tool, and the file is left alone.

### Content

Import the cache, then look at the **coverage** figures before the copy. A cache entry's
fingerprint covers `{inputs, language, prompt_version}`, so editing one product in the feed makes
that unit *pending* again — and a pending unit with no producer on this machine is dropped from the
plan (E21). The screen lists the pending units by GTIN and language, so "request a fresh cache" is
an instruction rather than a hunch.

**The copy review shows this run's batch, not the cache.** `generated_cache.json` is never pruned,
so it holds every unit ever generated for this client on this machine. The review used to list all
of it — captioned "N GTIN(s) in the cache" — directly beneath coverage figures that *were* scoped,
with nothing to tell the two apart, and the gap widens with the age of the machine. It now reads
`in_scope_gtins` from the doctor's `scope` check and splits the file: this run's entries, then the
in-scope GTINs that have **no** entry (the copy still to be made), then everything else folded away
under a count. Folded rather than dropped — it is real copy, and a reader who wants it should reach
it; what it must not do is pad this run's list.

Scope is not recomputed here. `lib.preflight.in_scope` stays the single implementation and the
doctor carries the answer across. If it cannot be read the screen shows the whole cache and *says*
so, because filtering to nothing would read as "there is no copy" — wrong in the direction that
stops an operator looking.

Coverage and the review come from **one** preflight run, and the import button refreshes both: they
describe the file it just replaced.

**Asking for that cache is a conversation, not a button, and deliberately so.** This machine never
runs `run_generate` — no API key, no Anthropic egress — so it cannot produce
`generation_requests.json` either; that command runs on the maintainer's machine, in a Claude Code
session with the `content-generator` skill, which reads the pending units itself from the same
export. What the operator sends is the list this screen already shows. A file written here for
someone else to run `--emit` against would add a hand-off without removing one, which is why there
is no export button.

### Publish

The gates come from `lib/gates.py`, which `flow-orchestrator/SKILL.md` is checked against by a test
in both directions. Each is shown with **why it exists**, not only what it asks — a form that asks
without saying why teaches you to answer without reading, and this flow's whole cost is
concentrated in one unreviewed click.

**Some options exist only in the chat flow, and the data says which.** `post_run`'s *Explain each
error* needs a model to read the run log; `row_diff`'s per-row *apply*/*skip* need the
row-by-row walk the chat flow does and this screen does not — it shows every changed row at once
and confirms the subset at step 5. Those are marked `chat_only` in `lib/gates.py` rather than
deleted: the shell not being able to do something is no reason for the surface that can to lose
it. The screen renders `shell_options`, so such an option cannot become a button that does not do
what it says, and the contract test derives what must be rendered from the gates instead of from a
hand-maintained list of exceptions.

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
- **Gate 0 leads with what this run could touch, not the size of the catalogue.** It used to
  render the length of `products.json` under the label "products in the catalogue" — honest, and
  the wrong number: **127** on a run scoped to one product, at the gate where the operator forms
  their picture of what they are about to do. It now shows the doctor's `scope` check — *15 in
  scope*, *127 in the catalogue* one size down, and the doctor's own sentence naming what removed
  the rest. The shell does **not** compute scope itself: `lib.preflight.in_scope` already composes
  the process list and the video allowlist, and a second implementation of "what will this run
  touch" is the same class of mistake as a second implementation of the gates.

  Neither figure is the row count. Scope deliberately cannot subtract the units already published
  — that needs `state.json`, and an idle read of a corrupt one quarantines it (E19) — so it is a
  ceiling, and the real number arrives at step 5. On the live pilot the two read 15 and 5.

  If the payload cannot be read the gate shows a dash and says so; it never falls back to the
  catalogue total, because a wrong number under the right label is worse than no number. An empty
  scope gets a danger band: that run would write nothing and report success.

  **One `doctor --json --offline` per redraw**, in `_redraw` and shared by gates 0 and 3. Gate 3
  already ran one; a second would have been ~500 ms of blocking subprocess on every answer, and
  two gates could have disagreed about the same run. A contract test fails if any gate renderer
  runs its own.
- **The missing-field gate (step 4) appears only when the plan actually dropped a unit for a
  missing `product_name` (E18), and it names each one.** It used to render on every run, offering
  *Skip this unit* beside no unit — and of its three answers only *Stop the run* had any effect,
  so the one live control on a question about nothing was the destructive one. Gate applicability
  now consults the plan (`needs_missing_product_name`), refreshed on **every redraw** rather than
  once per run, because the plan is built at step 5 — in the middle of the walk — and a fact read
  before there is a plan decides a gate that then never appears. Building a plan that drops units
  says so in a toast as well as by the gate appearing above.

The Gate index's **Modes column is checked against the code** in both directions, not only the
ids and step numbers. It is prose, it said `all` for a gate that was never meant to fire
unconditionally, and nothing compared the two — which is how that defect shipped.

### Runs

Reads `output/{client}/runs/*.jsonl`, newest first, and distinguishes a **partial** log — a run
that stopped mid-way — from a finished one. That is the case that matters most: live pages and
permanent GS1 records may already exist for the rows that landed.

Above the logs, **"Does the site match the ledger?"** asks the site instead. Everything else on
this screen is what *this machine* recorded, which cannot show a page created by anything else —
another machine whose `state.json` has not come back, a hand edit in wp-admin, or a run that
failed part-way.

That last one is why it exists. The first real publish through this shell published a product in
Dutch and failed on French; sibling-blocking correctly held the product, so the row was logged as
an **error** and nothing was written to state — while the Dutch page was live, correct and
publicly reachable. Ten entries in the ledger, eleven pages on the site, and nothing in the tool
could say so. A later run classifies that product NEW, and only the slug lookup inside the
WordPress client stops it creating a duplicate.

It lists every page carrying a `meta.gtin`, **per language explicitly** (an unscoped query on a
WPML site answers with the default language only, so skipping that would report every translated
page as missing), and diffs both directions. It reports and never repairs: each divergence has
more than one correct resolution, and choosing needs someone who knows which machine published
last. `python -m scripts.reconcile` is the same check in a terminal.

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
- **The install is user-scope and version-pinned** — `uv` at a pinned version, CPython 3.11, and
  86 packages resolved in the committed `uv.lock` with hashes. No administrator rights, no service.
  [`operator-install.md`](operator-install.md#for-it) has the detail.
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
