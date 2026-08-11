---
name: content-generator
description: "Write the product tagline and Eigenschappen text as the in-session producer (no API key) for the (GTIN, language) units the generator flagged, then ingest them into the cache. Use when the operator says 'generate content for {client}', 'generate copy for {client}', or 'write product copy for {client}' — the older 'copy' phrasings are kept as triggers so existing habits keep working."
---

# Content Generator

## When to load

Trigger phrases: **"generate content for {client}"** ← preferred, plus **"generate copy for
{client}"** and **"write product copy for {client}"**, kept so existing habits keep working — e.g.
"generate content for democlient". Load this skill to act as the in-session producer: read the
pending generation requests, write the tagline + Eigenschappen copy in the client's brand voice, and
ingest the results into the generated-content cache. No API key — generation happens in this session.

## What this skill does

Fills the handful of product slots that need *writing* — the tagline (`usps[0]`) and the
Eigenschappen bullets (`usps[1:]`) — for the `(GTIN, language)` units the generator flagged as gaps.
It reads `output/{client}/data/generation_requests.json` (written by `run_generate --emit`), produces
per-language copy following the versioned voice template, writes
`output/{client}/data/generation_results.json`, and hands it back via `run_generate --ingest`, which
validates each result into `output/{client}/data/generated_cache.json`. Determinism lives in the
cache, not here: a unit is only generated once per input fingerprint, and this producer is
interchangeable with the headless API backend. Tone is **concise and business-like, not
conversational** — the operator is reviewing copy, not reading prose. Generated content is reviewed
**twice before it can reach a page** — here (the cache) and again in `plan.json`. There is no third
look: execute writes each page at `wordpress.post_status`, which ships as `publish`, so the page is
**live the moment it is written**. `post_status: draft` is the opt-in staged variant, and it is a
config change the operator makes deliberately — see `docs/wordpress-onboarding.md`.

## Inputs

- `client_id` (from the trigger phrase; ask if unclear).
- Pending requests at `output/{client}/data/generation_requests.json` (run
  `python -m scripts.run_generate {client} --emit` first if absent). It carries `prompt_version` and,
  per unit, `gtin`, `language`, `mode`, `needs_name`, `input_fingerprint`, `candidates`, and `inputs`.
- The voice template `prompts/{client}/generation.{prompt_version}.md`.

## Steps

1. **Resolve the client and ensure requests exist.** Determine `client_id` from the request; ask if
   ambiguous. If `output/{client}/data/generation_requests.json` is missing, run
   `python -m scripts.run_generate {client} --emit` first (parse the export via the
   `gs1-export-parser` skill if `output/{client}/data/products.json` is missing too).

2. **Read the requests.** Load `generation_requests.json`. Note `prompt_version`, the unit count, and
   the split by `mode` (`tighten` vs `generate`) and `needs_name`.

   **That file holds only the units in scope** — `run_generate` narrows through the process list and
   the confirmed-video allowlist before computing the gaps, so its count matches the doctor's
   `cache_coverage` pending figure. It used to be the whole catalogue: 224 units where 10 were in
   scope, which is copy nobody publishes and a review gate too long to read. If the count looks like
   the size of the export rather than the size of the batch, stop — the process list is probably not
   configured, and generating against it wastes real tokens.

   Present verbatim:
   ```
   democlient: 10 units to generate (3 tighten, 7 generate; 1 needs a French name).
   Generate all, or a subset?
   [all | only-tighten | only GTIN … | cancel]
   ```
   Default `all`. Off-menu reply → reply verbatim: `Please pick one of the listed options, or specify
   a filter (e.g. 'only GTIN 87123...').`

3. **Load the voice.** Read `prompts/{client}/generation.{prompt_version}.md` — its few-shot examples
   and rules *are* the voice for this `prompt_version`. If the file for the requested version is
   absent, stop and say so (a version bump needs its voice file); do not fall back to another version.

4. **Generate, per unit.** For each request, produce a ranked `usps` list in the voice:
   - `usps[0]` = the tagline (~30–60 chars); `usps[1:]` = Eigenschappen bullets (each ≤ ~80 chars).
   - **`mode = tighten`:** shorten and rank the request's `candidates`; keep their meaning, invent no
     new claims. **`mode = generate`:** write from `inputs.marketing_message` (1083) using
     `functional_name`/`net_content`/dims/`material` as context; if 1083 is blank, write minimally
     from `functional_name`.
   - **`needs_name` true:** also supply `product_name` — the name translated into this language.
   - Never emit net content, dimensions, or material as USPs (those are added deterministically).
   Work in batch; do not narrate each unit.

5. **Write the results.** Write `output/{client}/data/generation_results.json`:
   ```json
   {
     "client_id": "democlient",
     "results": [
       { "gtin": "08713195000473", "language": "nl",
         "usps": ["Verwijder makkelijk beschadigde schroeven", "Werkt op hout, plastic en glas"],
         "input_fingerprint": "<echo from the matching request>" }
     ]
   }
   ```
   Echo each unit's `input_fingerprint` from its request (so a feed edit since emit is caught), and
   include `product_name` only for `needs_name` units. `client_id` must equal the run's client.

6. **Ingest.** Run `python -m scripts.run_generate {client} --ingest`. Surface its stderr line
   verbatim, e.g. `ingested 8 result(s), skipped 2; 28/30 units cached; 2 pending (…)`. Those
   totals are **in-scope** units, not the catalogue. A
   non-zero exit is a config error — stop and show it (step: Failure modes).

7. **Review (gate #1 of 2).** Present a representative sample — a few NL and FR blocks, including any
   `tighten` and `needs_name` units — and the coverage counts. Point to
   `output/{client}/data/generated_cache.json` for the full copy and
   `output/{client}/data/generated_issues.json` for the reported values. Then:
   ```
   Generated content is in the cache (reviewed once here). run_plan is the second review before publish.
   [looks good — continue to run_plan | regenerate GTIN … | cancel]
   ```
   - `looks good` — done; the operator proceeds to the `flow-orchestrator` skill / `run_plan`.
   - `regenerate GTIN …` — redo those units (edit their results, re-run `--ingest`; a fresh
     fingerprint supersedes the old entry).
   - `cancel` — stop; the cache keeps whatever ingested so far (nothing is published).
   Off-menu reply → the same canned reply as step 2. Never offer to publish from here.

## How the work is done

Python. This skill drives `scripts/run_generate.py` (`--emit` / `--ingest`) and reads/writes
the `output/{client}/data/` JSON artifacts. The copy itself is written by Claude in-session, so no
API key is involved; `lib/llm.py` is the alternative headless producer. No MCP server is involved
and there is no `.mcp.json`.

## Failure modes

- **Requests file missing.** `generation_requests.json` is absent — run
  `python -m scripts.run_generate {client} --emit` first; do not hand-write requests.
- **`--ingest` exits 2.** A config error (unknown client, unreadable products, a results file whose
  `client_id` differs from the run, or missing results): surface the stderr `config error: …` and
  stop. Do not proceed to `run_plan` against a cache the ingest did not update.
- **Fingerprint mismatch → stale skip.** `--ingest` warns and skips a result whose
  `input_fingerprint` no longer matches the pending request (the feed changed since `--emit`). Re-emit
  and regenerate those units rather than forcing the old copy.
- **No pending request → skip.** A result for a `(gtin, language)` that is already fresh, verbatim
  (short 1067, `origin=feed`), or not pending is skipped with a warning — expected, not an error.
  The warning names which of three causes it was; the third is **not in scope for this run**, which
  happens when the process list was pruned between `--emit` and `--ingest`. That is also expected:
  the operator narrowed the batch, and the copy is kept in the results file rather than cached.
- **Blank marketing message.** A `generate` unit whose 1083 is empty still gets copy written from
  `functional_name` + context, and the gap is reported as `missing_generation_input` in
  `generated_issues.json` — surface it so the operator fixes 1083 in MyGS1.
- **This pipeline fails silently.** A green `--ingest` only means the JSON validated. Eyeball the
  actual NL and FR blocks in `generated_cache.json` against the real product before continuing — never
  trust the "ingested N" count alone. Never put specs into `usps`; never publish from this skill.
