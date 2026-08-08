---
name: flow-orchestrator
description: "Publish a client's products to GS1 Digital Link and WordPress end-to-end — generate content, plan, confirm, then execute pages, GS1 resolver entries and QR, with step-by-step operator gates. Use when the operator says 'publish {client} to GS1', 'run the GS1 pipeline for {client}', 'run for {client}', or 'process {client}', and for any request to create only the pages or only the Digital Links: this skill classifies which of the three publish modes is meant and confirms it. This is the only sanctioned path for publishing: it is what enforces the review gates."
---

# Flow Orchestrator

## When to load

Trigger phrases (§10.5), most to least specific:

- **"publish {client} to GS1"** ← preferred
- **"run the GS1 pipeline for {client}"**
- **"run for {client}"**, **"process {client}"** — short forms, kept for continuity

e.g. "publish democlient to GS1, test env". Load this skill to drive a full client run end-to-end
from chat: parse → plan → present → confirm → execute → summarise.

Also load it for any phrasing that asks for **one leg only** — *"create the pages for democlient but
don't touch GS1"*, *"just set the Digital Links, the pages already exist"*. Those are the same
sequence with a different mode, and step 0 is where the mode gets pinned down.

> **Prefer the GS1-qualified phrasings.** A bare *"run for X"* is generic: in a coding session it
> competes with every other meaning of "run" — including a built-in `run` skill that launches a
> project's app. If this skill is not loaded, the operator gates below **do not happen** and a
> publish can proceed unreviewed. When in doubt, say "publish {client} to GS1".

## Publish modes

One sequence, three modes. The mode decides which leg of `run_execute` runs and how loud the gates
have to be:

| Mode | Slash command | Does | Reversibility |
|---|---|---|---|
| `pages` | `/gs1-pages` | WordPress pages only | Reversible — edit or delete the page |
| `links` | `/gs1-links` | Digital Links only, pointing at pages that already exist | **PERMANENT** |
| `both` | `/gs1-publish` | Pages first, then links pointing at them | **PERMANENT** |

Reached by slash command, the mode is **fixed** and you state it rather than derive it. Reached by
natural language, you classify it at step 0 and the operator confirms. When the phrasing is genuinely
ambiguous — *"publish the webpages and digital links"* names all three vocabularies — ask; do not
guess toward the more destructive mode.

## What this skill does

Orchestrates the generate/plan/confirm/execute pipeline for one client, in whichever of the three
modes above applies. For a client with a
`generator` config it first fills and reviews the generated-content cache (review gate 1), then runs
`scripts/run_plan.py` to classify each `(GTIN, language)` — which merges that cache — and presents
the plan (review gate 2), collects the operator's confirmation in chat, writes a `ConfirmedPlan` to
`output/{client}/plan.confirmed.json`, and invokes `scripts/run_execute.py` on the confirmed subset
— then reports the outcome. Generated content is **never auto-published**: it is reviewed twice and
executed draft-first. Tone is **concise and business-like, not conversational** (§10.6): verbose
text creates fatigue during batch runs.

For the pilot the flow is **create-only**: `run_plan.py` gates products through the
**process list**, so only the GTINs the operator listed are candidates. Every GTIN in
that file is processed — the tool reads no status columns, and the operator prepares the
file by deleting the rows that should not run. Every candidate is therefore NEW, and the
CHANGED/diff path below stays dormant — it is implemented and ready for future product
updates.

## Inputs

- `client_id` (from the trigger phrase; ask if unclear).
- `clients.yml` config for the client (languages, environment, `process_list`, `flow`,
  `generator`).
- Parsed products at `output/{client}/data/products.json` (run `parse_export` if absent).
- For a client with a `generator` config, the generated-content cache at
  `output/{client}/data/generated_cache.json` (filled in step 3; `run_plan` reads it).

## Steps

Numbered from **0**, and the numbering is load-bearing: step 0 was added after the rest and the
other eleven keep their numbers so every cross-reference to "step 8" — here, in
`IMPLEMENTATION_SPEC.md` §8.3, in `docs/setup.md` — still points at the same gate.

0. **Intent confirmation (gate 0).** Before running anything, present what is about to happen and
   require a choice. Five things, in this order:

   - **Mode** — `pages`, `links`, or `both`, and what each does *not* touch.
   - **Export file cross-check.** `parse_export` has **no input-path override**: the path comes from
     `clients.yml` → `export.path`. So when the operator names a file (*"/gs1-publish for
     @products-2026-q3.xlsx"*) that filename **verifies the config** rather than driving the run.
     State the configured path and how long ago it was modified, and ask whether it is the same
     file. This catches the likeliest real error — a fresh export dropped somewhere new while
     config still points at the old one — which nothing downstream would notice.
   - **Product count** from `output/{client}/data/products.json`. Label it as the source catalogue
     size, **not** the number of rows this run will write: that arrives at the plan gate (step 5).
   - **Environment** — `test` or `production`, resolved from `clients.yml`.
   - **Permanence**, for `links` and `both` only.

   For `links` / `both`, present verbatim:
   ```
   About to run the GS1 publish flow for democlient.
     Mode:        both — WordPress pages, then Digital Links pointing at them
     Export:      input/democlient/products.xlsx (modified 12 days ago)
     Products:    127 in the parsed catalogue
     Environment: production

   A GS1 Digital Link record can never be deleted. Retraction only disables it; the
   record stays on the account permanently.

   Proceed?
   [confirm | change-mode | cancel]
   ```
   For `pages`, the same block with the permanence paragraph replaced by:
   ```
   Pages only — no GS1 record is written, so this run is reversible.
   ```
   If the operator named a file that does not match `export.path`, add above the menu:
   ```
   You said products-2026-q3.xlsx. Config points at input/democlient/products.xlsx,
   modified 12 days ago. Same file?
   ```
   `change-mode` → re-present with the chosen mode; `cancel` → abort, run nothing.

   **This gate is asymmetric on purpose.** `pages` is reversible, so it **also stands in for the
   step-8 environment confirmation** — gate 0 has already named the environment and nothing
   irreversible follows. `links` and `both` still take step 8 as well.

1. **Resolve the client.** Determine `client_id` from the request; ask if ambiguous. If
   `output/{client}/data/products.json` is missing or stale, run
   `python -m scripts.parse_export {client}` first (the `gs1-export-parser` skill).

2. **Language selection (§10.6.6).** Present verbatim:
   ```
   Client democlient supports [nl, fr]. Which languages should this run cover?
   [all | nl | fr | nl,fr]
   ```
   Default `all`. Remember the chosen subset for step 6.

3. **Generate copy & review (gate 1 of 2).** Skip this step for a client with no `generator`
   config. Otherwise fill the generated-content cache, then review it before planning — the tagline
   and Eigenschappen are LLM-written, so they are reviewed *before* they can reach a page:
   - **In-session (no API key):** run `python -m scripts.run_generate {client} --emit`, then invoke the
     `content-generator` skill to write the copy and `--ingest` it; that skill presents the review.
   - **Headless:** run `python -m scripts.run_generate {client} --backend api` (needs the API key).
   Then eyeball a sample of `output/{client}/data/generated_cache.json` (nl **and** fr) and the
   `output/{client}/data/generated_issues.json` work list. **This pipeline fails silently — verify
   the copy against the real product, not the "ingested N" count.** Generation never publishes; the
   second gate is `plan.json` (step 5) and execute is draft-first.

   This step runs in **`links` mode too**, even though no page is written. Not for the copy itself —
   for the plan: with a `generator` configured, `run_plan` omits any `(GTIN, language)` that has no
   generated tagline (E21), so an empty cache yields an empty plan and the run publishes nothing
   while reporting success.

4. **Plan.** Run `python -m scripts.run_plan {client}` and read
   `output/{client}/plan.json`. run_plan omits any `(GTIN, language)` with a missing
   `product_name` and logs a `SKIPPED …` warning to stderr; for each such warning, present
   the **missing-field prompt (§10.6.5)** verbatim:
   ```
   GTIN 8712345678905 is missing `product_name_fr` (required for language fr).
   [skip-row | ask-me-later | fail-run]
   ```
   - `skip-row` — accept the omission; other languages proceed.
   - `ask-me-later` — batch the prompts, present at end.
   - `fail-run` — abort before execute.
   Default `flow.on_missing_field: prompt`.

5. **Plan summary (§10.6.1).** Present verbatim (the actionable total is NEW + CHANGED;
   UNCHANGED rows are never executed):
   ```
   Plan for democlient (test env):
     New:       38
     Unchanged:  7
     Changed:    2

   Proceed with all 40 to execute?
   [all | new-only | changed-review | cancel]
   ```
   - `all` — confirm every NEW and CHANGED row; execute.
   - `new-only` — confirm NEW rows only, skip CHANGED.
   - `changed-review` — walk each CHANGED row's diff and confirm individually (step 6).
   - `cancel` — abort, write nothing.
   Off-menu reply → reply verbatim: `Please pick one of the listed options, or specify a
   filter (e.g. 'only GTIN 87123...').`
   When run_plan reported process-list exclusions, add one line beneath the counts, e.g.
   `Excluded: 89 not on the process list.` That number is products in the catalogue the
   operator did not list — it is expected, not a warning.
   When `plan.json` carries a non-empty **`skipped`** array, add a line beneath the counts
   naming each reason and its count, e.g. `Skipped: 6 no generated copy, 2 missing
   product_name (not in the plan at all).` These are units that never became rows — E18 (no
   `product_name` in that language), E21 (generator on, no generated copy yet) or E22
   (`require_hero_image`, blank source image) — so they are **not** in the totals above and
   `all` will not publish them. Never present the counts without this line when the array is
   non-empty: an operator reading `New: 0` alone concludes there is nothing to do, when in
   fact there is copy to generate.
   When run_plan's stderr leads with the **state-reset warning** (E19 — prior state was
   corrupt and has been reset), put it **above** the counts, not below, and say what it
   means before offering the menu:
   ```
   WARNING: prior state was corrupt and has been reset (backup: output/democlient/state.json.corrupt.20260713T031200Z).
   Every row therefore re-plans as NEW. Re-running them is idempotent — pages are matched by
   slug/meta.gtin and updated in place, not duplicated — but it will rewrite live pages and
   resolver targets rather than skip them.
   ```
   Then present the counts as normal. Do not suppress or soften this: the counts alone read
   as a routine first run, and `all` would rewrite the whole catalogue.

6. **Build the confirmed subset.** From the plan rows and the menu choice, build
   `confirmed_gtins_by_lang`, then intersect it with the step-2 language subset:
   - `all` → every row with classification NEW or CHANGED.
   - `new-only` → NEW rows only.
   - `changed-review` → all NEW rows **plus** each CHANGED row walked via the **per-row
     diff (§10.6.2)**, presented verbatim:
     ```
     GTIN 8712345678905 (nl) — Cable Organiser Pro
     Changes:
       title:      "Cable Organiser" → "Cable Organiser Pro"
       target_url: /democlient/cable-organiser/ → /democlient/cable-organiser-pro/

     [apply | skip | show-full-diff]
     ```
     `show-full-diff` prints all fields, then re-prompts `[apply | skip]`. Confirm only the
     rows the operator `apply`s. Show only the fields present in the row's `diff`; never
     invent an "old" value (see Failure modes).
     A CHANGED row's `diff` carries `title` and/or `target_url` — the fields `StateEntry`
     records. When it is empty, the change is in the product body; say so plainly
     (`Changes: product content (no title or URL change)`) rather than printing a bare
     `Changes:` header.
     A `gs1_link` key means something different from a content change: the page is published
     but its resolver link was never written (a previous `pages` run). Present it as
     `Changes: resolver link not written yet` — nothing about the page is changing.

7. **Write the ConfirmedPlan.** Serialise `ConfirmedPlan{plan, confirmed_gtins_by_lang}`
   to `output/{client}/plan.confirmed.json`, with `confirmed_gtins_by_lang` as a list of
   `[gtin, language]` pairs (the shape `run_execute --confirmed` consumes).

8. **Environment confirmation (§10.6.7).** In `links` and `both` mode, if the client's resolved
   GS1 environment is `production`, present verbatim and require a choice before executing:
   ```
   About to execute against PRODUCTION environment (gs1nl-api.gs1.nl).
   This will make live changes to https://www.democlient.nl.
   Continue?
   [confirm | switch-to-test | cancel]
   ```
   Mandatory and non-overridable; enforced here per run (not per session). `confirm` →
   proceed; `switch-to-test` → re-resolve to the test environment; `cancel` → abort.
   **Skipped in `pages` mode** — gate 0 already named the environment and nothing irreversible
   follows, so a second production prompt for a page you can delete only trains the operator to
   click through them.

8.5. **Dry run (mandatory).** Before the real invocation, run the *same* command with `--dry-run`
   added and every other flag identical — same `--confirmed` path, same `--only`. It builds no
   clients, writes nothing, and needs no `--i-understand-production`. Show the operator what it
   says it would mutate, then proceed to step 9.

   Numbered 8.5 rather than 9 on purpose: the numbering is load-bearing (see the note above
   step 0), and renumbering would break every cross-reference to "step 9".

   This is the step that catches a plan pointing at the wrong rows, the wrong leg, or the wrong
   URLs — while it still costs nothing. Two things it cannot catch, so do not read a clean dry run
   as more than it is: in `links` mode it does **not** verify that the targets serve (the real run
   does that, and refuses), and it does not prove the ACF fields will land.

9. **Execute.** Invoke
   `python -m scripts.run_execute {client} --confirmed output/{client}/plan.confirmed.json`.
   - **Append `--only pages` or `--only links`** unless the mode is `both`, which is what omitting
     the flag means. The operator never types this — you supply it, on the strength of gate 0.
   - When the resolved environment is `production`, **append `--i-understand-production`** — the
     confirmation that authorises it is step 8 in `links`/`both` mode and gate 0 in `pages` mode.
     Without the flag, `run_execute` refuses a live production run (exit 2), so a production
     execute that omits it will not proceed.
   In `links` mode, `run_execute` verifies every resolver target serves before writing anything,
   and refuses the GTINs whose targets do not. Those come back as errors in step 11 — read them as
   "the page is not where the plan says it is", not as a GS1 fault.

10. **Progress (§10.6.3).** For runs over 20 rows, surface progress every 10 rows;
   otherwise only at the end. Not per-row (per-row detail goes to the JSONL log):
   ```
   Progress: 10/40 rows processed. 10 ok, 0 error, 0 skipped.
   ```

11. **Post-execute summary (§10.6.4).** Read the run JSONL and present verbatim:
    ```
    Run finished for democlient (test env, 2026-05-27T14:32:11Z).
      Ok:       38
      Error:     2
      Skipped:   0

    Errors:
      GTIN 8712345678912 (fr): WP 422 — invalid taxonomy term "outdoor_dier-fr" not found
      GTIN 8712345678919 (nl): image_url returned 404

    Log: output/democlient/runs/20260527T143211Z.jsonl
    QR files: output/democlient/qr/

    Retry the 2 failures? [yes | no | detail]
    ```
    - `yes` — re-run execute filtered to the failed GTINs.
    - `no` — done.
    - `detail` — read the JSONL entries and explain each.

12. **Record observations.** Review your own run (copy, plan, execution, verification) and write
    any qualitative "worth a glance" flags — the same heads-ups you'd give the operator in chat —
    to `output/{client}/data/observations.json` as `{"notes": ["…", "…"]}`, then regenerate the
    report: `python -m scripts.report_quality {client}`. They render in the report's
    **Observations** section, so they persist beyond the chat. This is deliberately *not*
    deterministic — the pipeline's own checks already run; this captures what only your review
    would notice (e.g. "a French title reads Dutch", "a GTIN 404'd once then resolved — GS1
    propagation lag, not a failure"). Write the observations in addition to your chat summary,
    not instead of it. Omit the file (or an empty `notes`) when there is genuinely nothing to flag.

## How the work is done

Python, invoked as modules: `scripts/run_plan.py` and `scripts/run_execute.py`, plus
`scripts/parse_export.py` when products are missing and `scripts/run_generate.py` for copy. Those
scripts own the production guard, the `state.json` writes, and the run JSONL — so keep the work on
them rather than driving `lib/` directly, which would reimplement all three in prose.

There are MCP servers in `mcps/`, but they are **not** how anything here works and there is no
`.mcp.json`. They expose a strict subset of the Python clients, and OD-2 keeps them private.

## Failure modes

- **Create-only, so no diffs in the pilot.** Every candidate row is NEW, so the
  `changed-review` / per-row diff path (§10.6.2) does not fire. It is implemented for
  future product updates.
- **No fabricated "old" values.** `StateEntry` records the prior `title` and `wp_url`, so a
  CHANGED row's `diff` can show a real before/after for those two — and only those two.
  `content_hash` proves the rest of the product changed but, being a digest, cannot say how.
  Present only the fields actually in `diff`; never invent an old value. State written before
  titles were persisted has `title: null`, and the title row is then omitted, not guessed.
- **run_plan exits 2** (bad client id, unreadable products/state/control file, missing
  `slug_pattern`/`target_url_pattern`): surface the stderr `config error: …` and stop —
  do not attempt to execute against a missing or malformed plan.
- **Corrupt state is not an exit-2** (E19). run_plan moves the bad file aside, starts fresh,
  and exits 0 with the reset warning on stderr. The plan is valid and safe to execute; what
  changes is its *meaning* — an incremental re-run has become a full rewrite. Surface it per
  step 5 and let the operator decide. Never re-plan silently.
- **Nothing to execute.** If the confirmed subset is empty (e.g. everything excluded by the
  control file, or the operator picked `new-only` with zero NEW rows), report it and skip
  the execute step rather than invoking `run_execute` with an empty plan.
- **Missing process list.** If `process_list` is configured but the file is absent,
  run_plan exits 2 — ask the operator to place it at the configured path before retrying.
- **`links` mode refused a GTIN: "refusing to point a permanent GS1 record at it".** Its target URL
  did not serve. Do **not** work around it — that refusal is the whole reason the mode is safe to
  offer. Find out where the page actually is (the slug may not match `slug_pattern`, or the page may
  be drafted or gone), fix `wordpress.target_url_pattern` or publish the page, and re-run. The other
  GTINs in the batch already went through.
- **`pages` mode leaves rows CHANGED.** Expected, not a bug: a page published without its resolver
  link is not finished, and the plan says so until `/gs1-links` completes it. The row's diff carries
  `gs1_link`, not a content change.
