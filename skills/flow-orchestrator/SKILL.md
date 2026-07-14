# Flow Orchestrator

## When to load

Trigger phrases: **"run for {client}"**, **"process {client}"** (§10.5) — e.g. "run for
noviplast in test env". Load this skill to drive a full client run end-to-end from chat:
parse → plan → present → confirm → execute → summarise.

## What this skill does

Orchestrates the plan/confirm/execute pipeline for one client. It runs
`scripts/run_plan.py` to classify each `(GTIN, language)`, presents the plan and collects
the operator's confirmation in chat, writes a `ConfirmedPlan` to
`output/{client}/plan.confirmed.json`, and invokes `scripts/run_execute.py` on the
confirmed subset — then reports the outcome. Tone is **concise and business-like, not
conversational** (§10.6): verbose text creates fatigue during batch runs.

For the pilot the flow is **create-only**: `run_plan.py` gates products through the
website-status control file, so only GTINs that are already in GS1 and not yet on the
website become candidates. Every candidate is therefore NEW, and the CHANGED/diff path
below stays dormant — it is implemented and ready for future product updates.

## Inputs

- `client_id` (from the trigger phrase; ask if unclear).
- `clients.yml` config for the client (languages, environment, `website_status`, `flow`).
- Parsed products at `output/{client}/data/products.json` (run `parse_export` if absent).

## Steps

1. **Resolve the client.** Determine `client_id` from the request; ask if ambiguous. If
   `output/{client}/data/products.json` is missing or stale, run
   `python -m scripts.parse_export {client}` first (the `gs1-export-parser` skill).

2. **Language selection (§10.6.6).** Present verbatim:
   ```
   Client noviplast supports [nl, fr]. Which languages should this run cover?
   [all | nl | fr | nl,fr]
   ```
   Default `all`. Remember the chosen subset for step 5.

3. **Plan.** Run `python -m scripts.run_plan {client}` and read
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

4. **Plan summary (§10.6.1).** Present verbatim (the actionable total is NEW + CHANGED;
   UNCHANGED rows are never executed):
   ```
   Plan for noviplast (test env):
     New:       38
     Unchanged:  7
     Changed:    2

   Proceed with all 40 to execute?
   [all | new-only | changed-review | cancel]
   ```
   - `all` — confirm every NEW and CHANGED row; execute.
   - `new-only` — confirm NEW rows only, skip CHANGED.
   - `changed-review` — walk each CHANGED row's diff and confirm individually (step 5).
   - `cancel` — abort, write nothing.
   Off-menu reply → reply verbatim: `Please pick one of the listed options, or specify a
   filter (e.g. 'only GTIN 87123...').`
   When run_plan reported control-file exclusions, add one line beneath the counts, e.g.
   `Excluded (control file): 12 already on website, 3 not yet in GS1, 1 not in control file.`

5. **Build the confirmed subset.** From the plan rows and the menu choice, build
   `confirmed_gtins_by_lang`, then intersect it with the step-2 language subset:
   - `all` → every row with classification NEW or CHANGED.
   - `new-only` → NEW rows only.
   - `changed-review` → all NEW rows **plus** each CHANGED row walked via the **per-row
     diff (§10.6.2)**, presented verbatim:
     ```
     GTIN 8712345678905 (nl) — Cable Organiser Pro
     Changes:
       title:      "Cable Organiser" → "Cable Organiser Pro"
       target_url: /noviplast/cable-organiser/ → /noviplast/cable-organiser-pro/

     [apply | skip | show-full-diff]
     ```
     `show-full-diff` prints all fields, then re-prompts `[apply | skip]`. Confirm only the
     rows the operator `apply`s. Show only the fields present in the row's `diff`; never
     invent an "old" value (see Failure modes).
     A CHANGED row's `diff` carries `title` and/or `target_url` — the fields `StateEntry`
     records. When it is empty, the change is in the product body; say so plainly
     (`Changes: product content (no title or URL change)`) rather than printing a bare
     `Changes:` header.

6. **Write the ConfirmedPlan.** Serialise `ConfirmedPlan{plan, confirmed_gtins_by_lang}`
   to `output/{client}/plan.confirmed.json`, with `confirmed_gtins_by_lang` as a list of
   `[gtin, language]` pairs (the shape `run_execute --confirmed` consumes).

7. **Environment confirmation (§10.6.7).** If the client's resolved GS1 environment is
   `production`, present verbatim and require a choice before executing:
   ```
   About to execute against PRODUCTION environment (gs1nl-api.gs1.nl).
   This will make live changes to https://www.noviplast.nl.
   Continue?
   [confirm | switch-to-test | cancel]
   ```
   Mandatory and non-overridable; enforced here per run (not per session). `confirm` →
   proceed; `switch-to-test` → re-resolve to the test environment; `cancel` → abort.

8. **Execute.** Invoke
   `python -m scripts.run_execute {client} --confirmed output/{client}/plan.confirmed.json`.

9. **Progress (§10.6.3).** For runs over 20 rows, surface progress every 10 rows;
   otherwise only at the end. Not per-row (per-row detail goes to the JSONL log):
   ```
   Progress: 10/40 rows processed. 10 ok, 0 error, 0 skipped.
   ```

10. **Post-execute summary (§10.6.4).** Read the run JSONL and present verbatim:
    ```
    Run finished for noviplast (test env, 2026-05-27T14:32:11Z).
      Ok:       38
      Error:     2
      Skipped:   0

    Errors:
      GTIN 8712345678912 (fr): WP 422 — invalid taxonomy term "outdoor_dier-fr" not found
      GTIN 8712345678919 (nl): image_url returned 404

    Log: output/noviplast/runs/20260527T143211Z.jsonl
    QR files: output/noviplast/qr/

    Retry the 2 failures? [yes | no | detail]
    ```
    - `yes` — re-run execute filtered to the failed GTINs.
    - `no` — done.
    - `detail` — read the JSONL entries and explain each.

## MCP tools used

None directly. This skill drives `scripts/run_plan.py` and `scripts/run_execute.py` (and
`scripts/parse_export.py` when products are missing). The GS1/WordPress/QR MCP tool
wrappers are wired in Phase 8.

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
- **Nothing to execute.** If the confirmed subset is empty (e.g. everything excluded by the
  control file, or the operator picked `new-only` with zero NEW rows), report it and skip
  the execute step rather than invoking `run_execute` with an empty plan.
- **Missing control file.** If `website_status` is configured but the file is absent,
  run_plan exits 2 — ask the operator to place it at the configured path before retrying.
