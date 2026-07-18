# Content Generator

## When to load

Trigger phrases: **"generate copy for {client}"**, **"write product copy for {client}"** — e.g.
"generate copy for noviplast". Load this skill to act as the Cowork-native copy producer: read the
pending generation requests, write the tagline + Eigenschappen copy in the client's brand voice, and
ingest the results into the generated-copy cache. No API key — generation happens in this session.

## What this skill does

Fills the handful of product slots that need *writing* — the tagline (`usps[0]`) and the
Eigenschappen bullets (`usps[1:]`) — for the `(GTIN, language)` units the generator flagged as gaps.
It reads `output/{client}/data/generation_requests.json` (written by `run_generate --emit`), produces
per-language copy following the versioned voice template, writes
`output/{client}/data/generation_results.json`, and hands it back via `run_generate --ingest`, which
validates each result into `output/{client}/data/generated_cache.json`. Determinism lives in the
cache, not here: a unit is only generated once per input fingerprint, and this producer is
interchangeable with the headless API backend. Tone is **concise and business-like, not
conversational** — the operator is reviewing copy, not reading prose. Generated copy is **never
auto-published**: it is reviewed here (cache) and again in `plan.json`, then executed draft-first.

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
   the split by `mode` (`tighten` vs `generate`) and `needs_name`. Present verbatim:
   ```
   noviplast: 246 units to generate (3 tighten, 243 generate; 1 needs a French name).
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
     "client_id": "noviplast",
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
   verbatim, e.g. `ingested 244 result(s), skipped 2; 252/254 units cached; 2 pending (…)`. A
   non-zero exit is a config error — stop and show it (step: Failure modes).

7. **Review (gate #1 of 2).** Present a representative sample — a few NL and FR blocks, including any
   `tighten` and `needs_name` units — and the coverage counts. Point to
   `output/{client}/data/generated_cache.json` for the full copy and
   `output/{client}/data/generated_issues.json` for the reported values. Then:
   ```
   Generated copy is in the cache (reviewed once here). run_plan is the second review before publish.
   [looks good — continue to run_plan | regenerate GTIN … | cancel]
   ```
   - `looks good` — done; the operator proceeds to the `flow-orchestrator` skill / `run_plan`.
   - `regenerate GTIN …` — redo those units (edit their results, re-run `--ingest`; a fresh
     fingerprint supersedes the old entry).
   - `cancel` — stop; the cache keeps whatever ingested so far (nothing is published).
   Off-menu reply → the same canned reply as step 2. Never offer to publish from here.

## MCP tools used

None directly. This skill drives `scripts/run_generate.py` (`--emit` / `--ingest`) and reads/writes
the `output/{client}/data/` JSON artifacts. The copy itself is written by Claude in-session, so no
API key or MCP call is involved (the headless API backend in `lib/llm.py` is the alternative
producer, wired in a later commit).

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
- **Blank marketing message.** A `generate` unit whose 1083 is empty still gets copy written from
  `functional_name` + context, and the gap is reported as `missing_generation_input` in
  `generated_issues.json` — surface it so the operator fixes 1083 in MyGS1.
- **This pipeline fails silently.** A green `--ingest` only means the JSON validated. Eyeball the
  actual NL and FR blocks in `generated_cache.json` against the real product before continuing — never
  trust the "ingested N" count alone. Never put specs into `usps`; never publish from this skill.
