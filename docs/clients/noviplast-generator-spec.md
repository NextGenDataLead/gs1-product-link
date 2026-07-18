# Content Generator — implementation SPEC

**Status:** scoped, not started. Supersedes the scoping questions in
`docs/clients/noviplast-generator-handoff.md`. Grounded in a full read of the codebase and the
real 127-product export. Written 2026-07-18.

The Noviplast GS1→WordPress page-adapter pipeline is complete except for its last critical-path
item: the **content generator**. Every other page slot is deterministic assembly from parsed GDSN
data; the generator owns the handful of slots that require *writing* copy.

## Decisions locked
- **Model:** Claude **Sonnet 5** (`claude-sonnet-5`; pin the exact snapshot id via the `claude-api`
  skill at build time). ~$2 for the whole catalogue, once, then cached. (Sonnet 5 is cheaper than
  Opus 4.8: $2/$10 vs $5/$25 per MTok; cost is negligible either way here.)
- **Scope:** the **full three-part `product_description` block** — AI tagline + AI Eigenschappen
  bullets + French gap-fill, PLUS the deterministic Technische-details specs line. Publishable
  end-to-end. NOT the full ACF page-assembly step (that stays a follow-up).
- **LLM spend placement:** a **separate `scripts/run_generate.py` command** — the only step that
  spends money / needs network. `run_plan` only *reads* the cache: free, offline, CI-safe.
- **Brand voice:** **few-shot from existing feed copy** — seed prompts with real taglines/1083
  messages that already read well (113 nl / 112 fr), frozen by `prompt_version`.

## Ground-truth data facts (verified against the real export)

| Input | Attr | Sheet | Coverage /127 | Role |
|---|---|---|---|---|
| Functional name | 3301 | TradeItemDescription | 127 nl / 126 fr | title base (already `product_name`; raw in `extras.functional_name`) |
| Product variation | 3332 | TradeItemDescription | **4** nl | title suffix — near-empty, deterministic rule only |
| Marketing message | 1083 | MarketingInformation | 113 nl / 112 fr | tagline source + Eigenschappen input (`description_short`) |
| Feature/benefit | 1067 | — | 6 nl / 5 fr | Eigenschappen seed where present (`description_long`) |
| Net content | 3510 | TradeItemMeasurements | 125 | Technische details (`decode_net_content`) |
| Height / Width / Depth | 3498 / 3520 / 3492 | TradeItemMeasurements | **127 / 127 / 127** (mm, `MMT`) | Technische details + Eigenschappen — needs parsing |
| Material | 4.012 | BrickGPCCommercialData | 75 | Technische details + Eigenschappen — needs parsing |

Only `nl` and `fr` exist in the data. The "combine 3301+3332" work is effectively moot (3332 is
4/127) — a trivial deterministic dedup rule covers it, no LLM. The real generation surface is
**Eigenschappen** (≈121 gaps/lang) and the **~14 tagline gaps/lang**.

## Architecture (one line)

Generation is a **cache-backed merge step that runs in `run_plan` before `diff_against_state`**,
mirroring `_assign_categories` (`scripts/run_plan.py:98-134,164`): it materialises generated copy
onto the `ProductRecord` so it enters the content hash, flows to ACF, and reclassifies rows — with
all LLM spend isolated in the opt-in `run_generate` step that only writes the cache.

### Representation on the record
Add two fields to `ProductRecord` (`lib/records.py:91-117`):
```python
generated_tagline: LocalisedText | None = None       # -> product_title, product_header_video_text
generated_description: LocalisedText | None = None    # -> product_description (three-part HTML blob)
```
- **Enters the hash** automatically — `compute_content_hash` dumps the whole record
  (`lib/state.py:176-186`); generated changes reclassify CHANGED, same reason `_assign_categories`
  sets `category` before classification.
- **Reaches ACF** — `acf._resolve` (`lib/acf.py:33-47`) does `getattr(product, field)`, sees a
  `LocalisedText`, returns `.values.get(language)`. No `acf.py` change; `acf_map` is free `getattr`,
  not bound by `LOCALISED_TARGETS`.
- **Distinguishable from feed** — net-new fields the feed never writes; `description_short` (1083) /
  `description_long` (1067) stay untouched as inputs, so a later feed value always wins at merge.
- Rejected: flat `extras.<name>` (no per-language dimension in `acf._resolve`); separate artifact
  merged at ACF-time (bypasses the hash).

**Title (3301+3332)** overwrites `product_name` via `model_copy(update=...)` in the merge step (not a
third field), so slug/title/`diff_against_state` keep working. Raw 3301 stays in
`extras.functional_name` for distinguishability + supersession.

## Cache design (`lib/generator.py`)
- **File:** `output/{client_id}/data/generated_cache.json`.
- **Model:** pydantic `GeneratedCache`; atomic write reusing the tmp-file + `os.replace` pattern from
  `lib/state.py save_state`.
- **Key:** `(gtin, language, field)`, each entry carrying an **`input_fingerprint`** = sha256 over the
  source inputs (canonicalised like `compute_content_hash`): relevant subset of `{1083, 1067,
  net_content, height, width, depth, material, lang, prompt_version, model}`.
- **Provenance:** stores `provenance:"generated"`, `model`, `prompt_version`, `input_fingerprint`,
  `generated_at`, raw model output, and the **source-language input** it was derived from. Feed values
  are never cached — that absence *is* the provenance line.
- **Supersession:** merge always prefers the feed field; reads cache only for genuine gaps. A feed edit
  changes the fingerprint → cache miss → stale value ignored + re-flagged. `prompt_version`/`model`
  bump invalidates the same way.

## Pipeline placement
- **`lib/generator.py`** — `generate(products, cfg, llm) -> GeneratedCache` (LLM path, used by
  `run_generate`) and `merge_generated(products, cache, cfg) -> tuple[list[ProductRecord],
  list[SourceIssue]]` (pure, no network: title combiner, tagline resolution, three-part HTML assembly,
  French fill, one `SourceIssue` per generated value; used by `run_plan`).
- **`lib/llm.py`** — sync `AnthropicClient` over the Messages API (sync `httpx`, matching
  `gs1_dl_client`/`wp_client`) + an `LLMClient` Protocol for test fakes. Credential via a config-named
  env var, lazily read, raising `MissingCredentialError`.
- **`scripts/run_generate.py CLIENT_ID`** — fills the cache, prints a spend/coverage summary.
- **`run_plan._build_plan`** (`scripts/run_plan.py:150-176`) — gains `_generate_content(cfg, products)`
  after `_assign_categories`, before `diff_against_state`, cache-only. Gaps with no valid cache entry
  become "needs generation" `SourceIssue`s and fall to the E18 backstop.

Operator flow: `parse_export` → **`run_generate`** (spend + first copy review) → `run_plan` (merges
cache, classifies, second review in `plan.json`) → confirm → `run_execute` (draft-first).

## LLM call shape & prompt
- **One call per `(gtin, language)`** returning structured JSON via tool-use / strict schema:
  `{"usps": [...], "eigenschappen": [...]}`. One call per unit keeps prompts small, cache keys clean,
  failures local. Batch API (50% off) is an optional optimisation.
- **Determinism:** `temperature=0`, pinned model id, versioned prompt template, inputs sorted
  deterministically.
- **Tagline "first USP" ordering** solved structurally — the one call returns both `usps` and
  `eigenschappen`, so `merge_generated` sets tagline = 1083 if present else `usps[0]`, and assembles:
  ```html
  <p><strong>{tagline}</strong></p>
  <p><strong>Eigenschappen</strong><br />• …</p>          <!-- from eigenschappen -->
  <p><strong>Technische details</strong><br />• …</p>     <!-- deterministic: net_content + dims + material -->
  ```
- **Brand voice:** few-shot block of real feed taglines/1083 that read well, per language, in the
  versioned prompt.

## Parser extensions (`clients.yml` + `clients.example.yml`, `gdsn_extras`)
```yaml
gdsn_extras:
  product_variation: { sheet: TradeItemDescription,  attribute: "3332", localised: true }
  dim_height:        { sheet: TradeItemMeasurements,  attribute: "3498", with_unit: true }
  dim_width:         { sheet: TradeItemMeasurements,  attribute: "3520", with_unit: true }
  dim_depth:         { sheet: TradeItemMeasurements,  attribute: "3492", with_unit: true }
  material:          { sheet: BrickGPCCommercialData, attribute: "Material" }
```
- Dimensions carry `MeasurementUnitCode` (`MMT`) → decode via existing `lib/units` (reuse).
- **Material** is `Information[0]/Material[0]/Value` with a non-numeric `(4.012)` label, so it is a
  **language-agnostic scalar** matched by the path segment `"Material"` (confirmed at commit 1 via
  `inspect_export`; `matches_attribute`, `lib/gdsn.py:151-155`). Multi-value in the feed
  (`Material[0..2]`); the parser takes the first. The value `"zzzanders"` appears in the column — the
  generator treats obvious junk as absent.
- `product_variation` (3332) resolves at default language only (`_resolve_extra`, `lib/gdsn.py:780`) —
  fine for the base variation token.

## Behaviour changes elsewhere
- **E18** (`lib/state.py:274-280`): filling `product_name.fr` from cache before `diff_against_state`
  stops the skip firing — no new branch. Keep the skip as the backstop (LLM disabled / no inputs / no
  cache).
- **Reporting:** every generated value → a `SourceIssue` in a **separate**
  `output/{client_id}/data/generated_issues.json` (mirroring `_write_category_issues`), each carrying
  the source-language input. Written always, even empty.
- **`acf_map`** (`clients.yml`, currently `{}`): `product_title` → `generated_tagline`,
  `product_header_video_text` → `generated_tagline`, `product_description` → `generated_description`.
- **Approval gate:** reviewed twice — `run_generate` cache output + `plan.json` — never auto-published
  (flow-orchestrator confirm + draft-first). Update `skills/flow-orchestrator/SKILL.md`.

## Config additions (`lib/config.py` + `schema/clients.schema.json`)
A `GeneratorConfig` block: `enabled`, `model` (`claude-sonnet-5`), `prompt_version`, api-key env-var
name, `max_tokens`. Typed, validated at load.

## Commit breakdown (dependency order)
1. **Parser inputs** — `product_variation`/`dim_*`/`material` in both configs; confirm material
   segment; test they land in `extras`.
2. **Record fields** — add `generated_tagline`, `generated_description`; round-trip + hash tests.
3. **`lib/generator.py` deterministic core** — `GeneratedCache` + atomic IO, title combiner, tagline
   resolver, three-part HTML assembler, `merge_generated` + `SourceIssue`; full unit tests, no LLM.
4. **`lib/llm.py`** + `GeneratorConfig` — sync `AnthropicClient`, `LLMClient` Protocol,
   `MissingCredentialError`; `pytest-httpx` tests; schema update.
5. **`generator.generate` (LLM path)** — prompt templates + few-shot voice, cache population, fr-fill;
   fake-`LLMClient` tests.
6. **`scripts/run_generate.py`** — CLI + summary; smoke test.
7. **`run_plan` integration** — merge before `diff_against_state`; E18 backstop; `generated_issues.json`;
   summary line; tests.
8. **Wire `acf_map`** in `clients.yml`; acf test.
9. **Docs + gate** — update `docs/clients/noviplast-page-adapter.md` §4.1/§4.2/§8 and
   `skills/flow-orchestrator/SKILL.md`.

## Reuse (do not rebuild)
`lib/acf.build_acf_payload`, `lib/units.decode_net_content`,
`lib/state.compute_content_hash`/`save_state`, the `lib/gdsn` extras mechanism, `lib/records.SourceIssue`.

New files: `lib/generator.py`, `lib/llm.py`, `scripts/run_generate.py`.

## Conventions (IMPLEMENTATION_SPEC §1)
Python 3.11+, PEP604 unions, `mypy --strict`, Google docstrings, `ruff check` (E,F,I,N,UP,B,SIM,PL),
line 100, typed exceptions from `lib.errors`, `logging` not `print`, **sync `httpx` only**, pydantic
for schemas, absolute imports. Tests: `.venv/bin/python -m pytest -q`.

## Verification (end-to-end)
1. **Unit** — `test_generator.py`, `test_llm.py`: cache hit reused; fingerprint change → miss; feed
   present → cache ignored (supersession); title dedup cases; per-language materialisation; one
   `SourceIssue` per generated value with its source input.
2. **LLM surface** — `pytest-httpx` asserts request shape (model, `temperature=0`, auth header),
   parses a canned tool-result; HTTP error → typed error.
3. **Integration** — `test_run_plan.py`: generated content reclassifies CHANGED; E18 row with cached
   fr plans; E18 row with no cache still SKIPs.
4. **Real run (staged)** — `run_generate noviplast` on the real `products.json`, eyeball
   `generated_cache.json` (spot-check NL + FR blocks), then `run_plan` and confirm generated fields
   appear on `plan.json` rows and reclassify. Draft-first execute protects the live site. **This
   pipeline fails silently — verify against the real parsed data, not just green tests.**

## Progress
- **Commit 1 done** (`3b2ffb5`): parser inputs wired into `gdsn_extras`; the material segment (open
  item) is resolved to the scalar `"Material"`. Coverage verified against the real export
  (variation 4/127, dimensions 127/127, material 75/127). Next: commit 2 (record fields).
