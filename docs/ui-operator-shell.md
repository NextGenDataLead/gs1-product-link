# The operator shell

> **Looking for how to publish a batch?** That is
> [`operator-guide.md`](operator-guide.md) — a walkthrough of the four screens with screenshots,
> written for the person running the tool. **This page is not that.** It explains *why each screen
> is built the way it is*, usually by naming the defect that produced the current design, and it
> names Python modules and test files throughout. It is for whoever maintains the shell.

A local desktop window over the same commands you would otherwise type. It exists so that the
recurring loop — drop a new export and a new scope list, choose the batch, import the copy, run the
flow, read
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

**It has no LLM credential and no connection to Anthropic — unless you give it one.** Leave the
client's `generator.api_key_env` unset and nothing here reaches Anthropic: content generation
happens on the maintainer's machine, in a Claude Code session with the `content-generator` skill,
and `generation_results.json` is handed over as a file and uploaded on the Content screen.

That split removes an entire class of IT objection and a per-token cost from the operator's
workstation, at the price of one file changing hands per batch. It is the default and it stays
supported. But it is also the reason the shell was not, on its own, a route from dataset to pages:
a brand-new product needs copy, and copy could not be written here.

So the key is now an **optional** field on the Setup screen. Set it and the Content screen writes
this run's copy itself; leave it blank and the screen says so and offers the import instead. The
credential is read by the `run_generate` subprocess from `.env`, never by this application — the
same arrangement that keeps every other secret out of the desktop process.

**It does not reimplement the pipeline.** Every action is a subprocess running exactly the command
a person would run, from the repository root, and every screen shows that command. So the terminal,
the skills, and [`verifying-live.md`](verifying-live.md) all stay valid as a fallback when something
here is wrong.

**It does not replace the Claude Code flow.** `publish {client} to GS1` still works and still goes
through `flow-orchestrator`. The shell is a second surface over the same gates, not a fork of them.

---

## The screens

Seven screens, in two groups. **The rail is the argument**: the numbered four are the loop an
operator repeats per batch, and everything else is deliberately not numbered.

| # | Screen | What it is for |
|---|---|---|
| 1 | **Data** | Upload the export and the scope list, choose the batch, read the data-quality report. |
| 2 | **Content** | Generate or import `generation_results.json`, check its coverage, read the copy. |
| 3 | **Preflight** | `python -m scripts.doctor`, rendered as a list to work down. Offline by default. |
| 4 | **Publish** | The nine gates, one at a time. |
| — | **Setup** | The operator-facing half of `clients.yml` and `.env`, as a form, with live Test buttons. |
| — | **Runs** | Every row of every run, as it was recorded at the time, and whether the site agrees. |
| — | **Video mapping** | Which video file belongs to which product, per language. |

Load this batch's inputs · review its copy · check *this batch* · publish it.

**All six used to be numbered 1-6, and that was wrong in the same way twice.** Setup is configured
once and then left alone; Runs is read afterwards. Numbering them alongside the four said they were
one sequence, so the work an operator actually repeats was buried between machine configuration at
one end and history at the other. They keep a permanent place in the rail — below a rule, under
*This machine* — because a tool nobody can find is a tool nobody uses; only below 55rem, where the
rail used to stack as a full-width block with no way past it, does anything fold behind a `☰`.

**And Preflight used to sit at 2.** Four of the doctor's checks have the remedy "Run `parse_export`
first" — which is the Data screen — so it told an operator to go and do a later step and come back,
and on a machine being set up from scratch most of the list could not answer its own questions yet.
Its headline is "N of M in scope", a statement *about the export just loaded*, so it belongs after
the loading. Nothing is lost by moving it: the credential checks it also carries are on the Setup
screen's Test buttons, at the moment the field is edited.

The order lives in `ui/theme.py`'s `WAVE` and nowhere else — each screen reads its own eyebrow from
it via `theme.eyebrow`, and `tests/ui/test_pages_contract.py` holds both the numbering and the
membership of the two groups, in both directions against the registered routes.

Each rail entry also carries one **fact** from `ui.context.rail_facts` — the export's age, the
plan's row count — so "have I done Data yet?" is answerable from any screen. Facts and not ticks:
a tick on Data because *an* export exists cannot say it is the *right* export, and a tick that lies
is worse than no tick. Everything in there must stay stat-cheap, because it runs on every render of
every screen; `tests/ui/test_shell_chrome_contract.py` fails if it grows a subprocess.

### Every button says that it is working

A button that runs something disables itself for the duration and shows a spinner and the seconds
elapsed. That is not decoration. `run_execute` prints one line when it starts and one when it
finishes, so a twenty-row publish leaves the console silent for about ninety seconds — and with
nothing disabled and nothing moving, the operator reasonably concluded it had not worked and
clicked again. Twenty live pages were rewritten twice. No damage that time, because pages are
matched by slug and `meta.gtin` and updated in place; the same second click in `links` or `both`
mode is aimed at records that can never be deleted.

The guard is in `ui/theme.py`, on `action` and `quiet_action`, rather than at the two dozen call
sites — a guard on the execute button alone would have left the defect on the other twenty-three,
and a screen written next month inherits this one without an edit. It is not a progress bar:
`run_execute` reports every ten rows by design, so a bar would be fake precision on a twenty-row
run. It disables the button that was clicked and not the screen, because that is what actually
happened.

**The other half is that the command runs off the event loop.** A blocking `subprocess.run` inside
a click handler holds the loop until the command has already finished, so every UI change queued
before it — including the one saying the command is running — reaches the browser with nothing
left to report. `runner.run_json_off_the_loop` was written for exactly this and had been adopted
on one screen; six buttons across five others still blocked, so on those the spinner would never
have animated. Both halves are checked by `tests/ui/test_run_feedback_contract.py`, which derives
the handler list from the code rather than keeping one: a list of known offenders goes stale in
both directions, and the button added next month is the case it would miss.

### Data

**Two files, two sections, two uploads.** The export is product *data*; the scope list is *which
products*. They come from different places and confusing them is the most expensive mistake this
screen affords, so each has its own name, its own section and its own upload. The config key stays
`process_list` — it is in `clients.yml`, `schema/clients.schema.json`, `ProcessListConfig`, the
doctor payload and five call sites, and renaming it would break every install. Only the words the
operator reads changed.

Both uploads **replace the configured path in place**. Writing anywhere else would produce a file
the tool cannot see, and neither `parse_export` nor the scope-list reader takes an input-path
override.

The two uploads differ in order of operations, and the difference matters. The export is backed up
and overwritten. The scope list is **validated first** — written to a temporary directory, read
with `lib.process_list.read_process_list`, and only then archived and installed — because it is a
file the shell now also archives, and a blind overwrite would leave a window where the control file
and its archive disagree. It is `ui.video_map_edit.write_validated`'s pattern.

**`process-list.source.xlsx` is the upload, kept byte for byte.** `.bak.xlsx` holds only *the
previous save*, so after two saves the operator's original is gone; the archive is what lets
**Restore the uploaded list** mean "the list I sent" rather than "whatever it looked like last
time". It is read for Restore and for display. **It never decides what gets written** — a design
that derived the control file from it would put a wrong join between the operator and their own
list, silently.

**The grid is the scope list joined against the export**, and that join is the reason this screen
was rebuilt. A barcode on the list that the export carries no row for produces no error, no plan
row and no count anywhere in the tool; the operator's only evidence is a total one smaller than
they expected. On the pilot that is exactly one SKU. It gets its own table, above the rest, with
its own checkboxes — not a protected class, because a row there can be dropped on purpose.

The join is `lib.process_list.rows_in_export`, in `lib` rather than on the screen, on
`product.gtin14` against the sheet's own normalisation — `lib.preflight.in_scope`'s exact pair.
`check_scope` deliberately emits `ProductRecord.gtin` and not `gtin14` "because a normalised
variant here would silently fail to match for any client whose feed carries 13-digit codes", and a
third normalisation invented on a screen would report every good product as missing.

**The checkbox inverted, and that is a hazard, not a detail.** It used to mean *remove this row*;
it means *keep this row*. An operator with the old habit ticks what they want gone and publishes
exactly those. Four mitigations, all cheap and all required: the *Remove selected rows* button is
deleted outright so no control carries the old wording; Save reports the **delta** ("Saved 36
row(s). 2 dropped") rather than the end state, which is the sentence that contradicts them;
`theme.action(danger=True)` keeps it red; and Restore makes the worst case one click.

`pagination=0` on the table is **mandatory, not cosmetic**. With pagination on, Quasar's header
checkbox selects *this page*, and a save would quietly drop every row the operator never scrolled
to. Selection is independent of the filter in Quasar 2.18 — verified in a browser: deselect two,
type in the box, clear it, and the count holds — but the header checkbox's tri-state describes the
rows the *filter* is showing, not the file. That is what the count label beside the table is for,
and why it names the file's numbers first and the filtered view second.

Saving keeps the previous version, freezes and filters the header row so the file is still workable
in Excel, and refuses to write a list with no GTINs at all, because that would produce an empty
plan and a run that reports success having published nothing.

Two staleness facts the screen was not stating. The product count comes from `products.json` and
the "export modified" date from the workbook: **two files, two mtimes, shown as one fact**. Upload
without pressing Parse and last quarter's count sits under today's date, so the screen compares
them and warns. The data-quality report is dated for the same reason — a rebuild that wrote nothing
new leaves last week's worklist on screen looking exactly like this week's.

Every in-scope SKU held for want of a confirmed video carries a per-row mark, from
`lib.preflight.held_for_video`. Data is the only per-SKU grid in the shell, so it is the only place
that fact can live per row; on the pilot, 19 of the 37 are held and the screen used to show none of
it.

### Content

Import the copy, then look at the **coverage** figures before reading it. The copy is written fresh
for each run and never stored, so the question is not how much has piled up but whether *this* file
answers every unit the run will publish. Not every in-scope unit: copy is written for the rows a run
creates or changes, so an already-live, unchanged unit needs none, and the figures say how many were
set aside for that reason. Its fingerprint covers `{inputs, language, prompt_version}`, so editing
one product in the feed leaves that unit uncovered — and an uncovered unit with no producer on this
machine is dropped from the plan (E21). The screen lists those units by GTIN and language, so
"request fresh copy" is an instruction rather than a hunch.

**The copy review shows this run's batch, not the whole file.** It reads `in_scope_gtins` from the
doctor's `scope` check and splits: this run's entries, then the in-scope GTINs with **no** copy (the
work still to do), then anything outside this run's scope. That last group used to be ordinary —
the cache accumulated every unit ever generated on this machine — and it is not any more. A per-run
file holding GTINs this run will not touch means it was written against a *different scope list*,
so the screen says so as a warning and keeps the list behind a fold rather than presenting it as
background.

Scope is not recomputed here. `lib.preflight.in_scope` stays the single implementation and the
doctor carries the answer across. If it cannot be read the screen shows the whole file and *says*
so, because filtering to nothing would read as "there is no copy" — wrong in the direction that
stops an operator looking.

Coverage and the review come from **one** preflight run, and the import button refreshes both: they
describe the file it just replaced.

**There are two producers, and which one this screen offers depends on one config field.** With
the client's `generator.api_key_env` naming a variable that has a value, the screen leads with
**Generate the copy** and a button that runs `python -m scripts.run_generate {client} --backend
api` as a subprocess — the key is read by the child from `.env`, never by this process. With it
unset there is no button and no Anthropic egress at all: the copy is written on the maintainer's
machine, in a Claude Code session with the `content-generator` skill, and arrives here as a file
to import. Both write the same `generation_results.json` against the same contract.

There is still no **export** button for `generation_requests.json`, and that is deliberate: the
skill reads the pending units itself from the same export, so what the operator sends is the list
this screen already shows, and a file written here for someone else to run `--emit` against would
add a hand-off without removing one.

> This paragraph said the opposite for a while — "asking for that copy is a conversation, not a
> button… this machine never runs `run_generate`" — which stopped being true when the key became
> optional, and stayed on the page for a release. The intro of this document was updated in the
> same change and this section was not, so the doc contradicted itself in two places about the
> most consequential thing on the screen.

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

The first line to read is **"What a run would touch"** — how many products survive the scope list
and the video allowlist. Every check below it reports on that scope rather than the whole
catalogue.

### Publish

The gates come from `lib/gates.py`, which `flow-orchestrator/SKILL.md` is checked against by a test
in both directions. Each is shown with **why it exists**, not only what it asks — a form that asks
without saying why teaches you to answer without reading, and this flow's whole cost is
concentrated in one unreviewed click.

**Some options exist only in the chat flow, and the data says which.** `post_run`'s *Explain each
error* needs a model to read the run log, so it is marked `chat_only` in `lib/gates.py` rather
than deleted: the shell not being able to do something is no reason for the surface that can to
lose it. The screen renders `shell_options`, so such an option cannot become a button that does
not do what it says, and the contract test derives what must be rendered from the gates instead of
from a hand-maintained list of exceptions.

`row_diff`'s per-row *apply*/*skip* used to be on that list and are not any more — the screen
walks the rows now, so the flag would have been describing the surface rather than the option. The
flag moves when the surface does; that is what keeps it meaningful.

**And the data says what each answer *does*, in three states rather than two.** `GateOutcome` is
`ADVANCES`, `STOPS`, or `REDISPLAYS`, because one boolean was answering two questions — *does this
carry the flow on* and *does this stop the run* — which coincide everywhere except on a detour.
Gate 6's *Show full diff* is the detour: in the chat flow it prints the rest and re-prompts, so it
does not advance; on a form it is the terminal answer to its gate. It was once the only option that
gate could render here, and read as a refusal that one button ended the run with nothing on the
screen to undo it — reached by answering *Review changed*, the most careful answer on offer. It now
lifts the row cap and means nothing else. *Change mode* and *Regenerate* are detours too, at gates
that are required, so the run is still held — but held as **unanswered**, which is what the screen
says, instead of reporting a cancellation nobody made.

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
- **Gate 6 walks every CHANGED row, and confirms only the ones you applied.** It used to do
  neither. It listed `[row for row in plan.rows if row.diff]` — and `state.json` records the prior
  `title` and `wp_url` and nothing else, so a row changed in the product body carries no diff at
  all. On the live 24-row plan that displayed **one** row of the twenty it was confirming.
  Meanwhile *Review changed* returned exactly what *All* returned, because the selection switched
  on classification alone. So the most careful answer on the menu was the same click as the most
  sweeping one, and fixing one product's French title meant rewriting twenty live rows.

  Each row now carries *Apply* and *Skip*, and a row left undecided is **not** published — the
  safe default, and the one that makes narrowing possible at all. NEW rows are confirmed
  regardless; the rows are narrowed to the languages chosen at gate 2, since a decision about a
  row the language subset drops is a decision with no effect. Rebuilding the plan forgets every
  decision: these are not the rows those answers were about.

  The 50-row display cap stays, and now says what it costs. With a control on every row a capped
  list drops rows out of the *decision*, not merely out of the display, so the rows past it are
  named as undecided and therefore unpublished, with *Show full diff* offered to bring them on
  screen. Which rows a run confirms is `PublishSession.confirmed_pairs` — in the module that is
  tested without a browser, rather than half on the screen as it was.
- **Gate 0 leads with what this run could touch, not the size of the catalogue.** It used to
  render the length of `products.json` under the label "products in the catalogue" — honest, and
  the wrong number: **127** on a run scoped to one product, at the gate where the operator forms
  their picture of what they are about to do. It now shows the doctor's `scope` check — *15 in
  scope*, *127 in the catalogue* one size down, and the doctor's own sentence naming what removed
  the rest. The shell does **not** compute scope itself: `lib.preflight.in_scope` already composes
  the scope list and the video allowlist, and a second implementation of "what will this run
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

### Runs

Reads `output/{client}/runs/*.jsonl`, newest first, and distinguishes a **partial** log — a run
that stopped mid-way — from a finished one. That is the case that matters most: live pages and
permanent GS1 records may already exist for the rows that landed.

**Build the result sheet** sits on each run's card, not on Data. The artefact is per-run and
lands beside `{ts}.jsonl` as `{ts}-scope.xlsx`; a screen showing no run would have to guess which
run it was about, and `--run` is passed explicitly for the same reason — two runs a second apart
are `{ts}.jsonl` and `{ts}-1.jsonl`, and the wrong one of the pair is indistinguishable from the
right one until somebody opens a page URL that was never visited.

The sheet is the operator's own scope list handed back with what the run did appended: one row per
SKU, their columns verbatim, then `in_scope`, `result`, and status/page/detail per language. A
`units` tab carries one row per `(gtin, language)` uninterpreted, which is where "nl published, fr
failed" survives the worst-of reduction; a `legend` tab gives every value a sentence so the file
can be forwarded without a covering email.

**It is a report, written after — nothing reads it back.** That is the whole difference from the
design where the scope list grows a run-status column and a later run filters on it. `lib/
process_list.py` records what that cost the last time: a status column silently meant its opposite
for a client whose file said `no`, in both directions, and neither direction raised anything. Two
status columns rather than one, for the same reason: `in_scope` is the decision and `status_{lang}`
is what happened, and one cell answering both has a meaning that depends on when you read it.

`plan.json` is overwritten by every `run_plan`, so for anything but the newest run it is somebody
else's document. A plan generated *after* the run is refused with a line on stderr rather than
quietly contributing its holds to a run that never saw them.

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

### Video mapping

Under *This machine* rather than in the numbered four: it is one input file's editor, not a step
of a batch. It sat outside the rail entirely while the rail was a single numbered list, reachable
only from a link on Data; splitting the rail gave it somewhere honest to sit. It exists because
that file decides whether a product can be published at all —
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

## Regenerating the screenshots

[`operator-guide.md`](operator-guide.md) embeds one PNG per screen from `docs/images/`. They go
stale whenever the chrome changes, and they are **captured against a throwaway client, never
against a real one** — `clients.yml` is gitignored because it is client configuration, and a
screenshot bakes the client name, real GTINs, product names and the site URL into a committed
binary that no `.gitignore` protects.

The recipe, all of it outside the repository:

1. Copy the repo to a scratch directory. Use `clients.example.yml` as its `clients.yml` —
   `democlient` is already defined in it.
2. Write synthetic `output/{client}/data/products.json`, `generation_results.json`, `plan.json`,
   `plan.summary.json` and one `runs/*.jsonl` through the models in `lib.records` and
   `lib.generator`, so the shapes are right by construction rather than by hand. Products need
   `image_url` and the `dim_*` extras or the plan holds all of them (E22/E23) and every figure
   reads zero. Leave `input_fingerprint` **null** on each result item — it is optional, and any
   other value fails the doctor's staleness check.
3. Give it `input/{client}/process-list.xlsx` (a `Barcode` column), a stand-in `products.xlsx`,
   and `videos/mapping.yml` in `{language: [{file, gtin}]}` shape with matching files on disk. For
   the Data screen specifically: put **one barcode on the scope list that is not in the export**,
   or the table that exists to show them is not in the picture; leave some GTINs unmapped so the
   per-row "no video yet" mark appears; and **backdate `products.xlsx` behind `products.json`**, or
   the staleness band fires on files written seconds apart and the screenshot tells the operator to
   re-parse for no reason.
4. Run `python -m scripts.run_plan {client}` there so the plan and the rail facts agree, then
   serve it with `ui.run(..., native=False, show=False)` on a spare port and drive Playwright
   at 1280x860.

The scratch directory's absolute path shows up in the Preflight screenshot's first check, so put
it somewhere that is not a private path.

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
