# Generator — scoping handoff

**Purpose.** Resume brief for scoping the **content generator** (the last big page-adapter
piece) in a fresh session. Self-contained: read this, then the linked doc sections, and you have
enough to scope without re-deriving. Written 2026-07-18.

> After `/clear`: point me at this file (`docs/clients/noviplast-generator-handoff.md`). The
> auto-loaded `MEMORY.md` also links it. Next intended step is **scoping** (a SPEC), not coding.

> **UPDATE 2026-07-18 — scoping DONE.** The SPEC now lives at
> `docs/clients/noviplast-generator-spec.md` (all §4/§6 open decisions settled: Sonnet 5, full
> three-part description, separate `run_generate`, few-shot voice; 3332 confirmed 4/127 so title
> combination is a trivial rule; dims 127/127 and material 75/127 need small `gdsn_extras` adds).
> **Next step is coding commit 1** of that SPEC's list. This handoff remains useful background;
> the SPEC is now the authority.

---

## 0. Where the project is

- **Branch `noviplast-page-adapter`** (pushed). Full suite green (**352 passed**, 2 skipped, 5
  staging-deselected); `ruff check` + `mypy --strict lib scripts` clean. `.venv/bin/python` runs
  everything; tests: `.venv/bin/python -m pytest -q`.
- **Just completed (this session):**
  - **Phase 7.5 — GPC brick → category mapping: DONE**, all 5 DoD met. Operator supplied the GS1
    DIY datamodel (`input/noviplast/GS1 Data Source Datamodel 3.1.36.xlsx`); client signed off the
    73-brick map + 1 override in `clients.yml`. See `IMPLEMENTATION_SPEC.md` §12 Phase 7.5 and
    `noviplast-page-adapter.md` §5.7.
  - **`net_content` H87 decoding: DONE** (Phase 7 page-adapter item). `reference/
    measurement_units.json` + `lib/units.py` `decode_net_content` → "5 H87" renders "5 Stuk"/"5
    Pièce" per language, at render time in `templates._build_context`.
- **The generator is the remaining critical-path item.** It is **not a numbered phase** — it is a
  page-adapter track item (`noviplast-page-adapter.md` §4.1, §4.2, §5, §6, §8). Not started.

---

## 1. What the generator actually owns (grounded in the field walk)

Its real scope is **smaller than "write the description"** (`noviplast-page-adapter.md` §4.1):
everything else is assembly from parsed data. Four things:

1. **Title combination** — the WP **post title** comes from **attr 3301** (Functional Name) today.
   When **attr 3332** (Product variation) is present, combine 3301+3332 *intelligently*
   (prefix/suffix/omit). Blind concatenation gives duplicates ("Snoeischaar snoeischaar"). 3301 is
   already parsed to `product_name`; 3332 is **not currently mapped** — confirm/extend the parser.
2. **Tagline** — the ACF slot fed to **three places, one value**: `product_title`,
   `product_header_video_text`, and the opening `<p>` of `product_description`. Rule: **1083 when
   present, else the first generated USP** (a non-deterministic choice, hence generator-owned).
   Coverage of 1083: **113/127 nl, 112/127 fr** — so ~14 need generating.
3. **Eigenschappen bullets** (the real bulk of the work, ~121 products) — LLM-**generated** per
   language from **marketing message (1083) + net content + dimensions + material** (+ attr **1067**
   `TradeItemFeatureBenefit` as a seed where it exists: only **6/127 nl, 5/127 fr**). Rendered as
   the `<p><strong>Eigenschappen</strong>…•…</p>` block inside `product_description`.
4. **Fill missing French** — the feed carries fr for most but not all; generate only into the gaps
   (§6 resolved: the tool writes both languages).

**NOT the generator (deterministic assembly):**
- **Technische details** bullets — from `net_content` (now decodable via `lib/units.decode_net_content`,
  "5 H87" → "5 Stuk"/"5 Pièce") + dimensions/material. 125/127 have net_content.
- Title base (3301), images, video, category (7.5), GS1 links, QR.

### `product_description` structure (one HTML blob, `noviplast-page-adapter.md` §4.1)
```html
<p><strong>{tagline}</strong></p>                        <!-- = product_title, one value 3× -->
<p><strong>Eigenschappen</strong><br />• … • …</p>        <!-- generated -->
<p><strong>Technische details</strong><br />• …</p>       <!-- deterministic (net_content etc.) -->
```

### ACF field targets (`noviplast-page-adapter.md` §3) — `wordpress.acf_map` is currently `{}`
- `product_title` ← tagline
- `product_header_video_text` ← tagline (same value)
- `product_description` ← the three-part HTML blob above
- (title base, images, video handled elsewhere; `product_header_video`/`is_new_product` left untouched)

---

## 2. Existing primitives to REUSE (do not rebuild)

- **`lib/units.py` `decode_net_content(value, language, *, fallback_language=)`** — Technische
  details unit words. Reuse for the net_content bullet.
- **`lib/gdsn.py`** — the parser. 1083 → `description_short`, 1067 → `description_long`, both
  `report_issues: false` **generator inputs** (`clients.example.yml` gdsn_map). 3301 → `product_name`.
  `extras` carries pass-throughs (functional_name 3301, logistics_name 3297, marketing_name 3318).
  Dimensions/material sources are **not confirmed mapped** — scope this.
- **`lib/records.py`** — `ProductRecord` (frozen). `SourceIssue` (records.py:194) is the documented
  home for **generated-content reporting** (records.py:202-204): when the LLM fills a gap the feed
  should have carried, emit a SourceIssue with the source-language input. Report to
  `output/{client}/data/source_issues.json`.
- **`lib/acf.py` `build_acf_payload(product, language, acf_map)`** — assembles the ACF write from
  `wordpress.acf_map`. The generator's outputs must reach it (populate acf_map + feed generated values).
- **`lib/wp_client.py`** — the verified **3-call per-(GTIN,language) write** (see §3.1 traps: `?lang=`
  and acf are incompatible in one call; language-scoped lookups; read ACF back by page id).
- **`lib/state.py` `compute_content_hash`** dumps the whole product — generated content must be part
  of the hashed input (or a deterministic cache) so re-runs don't drift.
- **`lib/templates.py` `_build_context(product, language, client_meta)`** — the per-language render
  seam (where net_content decoding lives). Note: the pilot renders via **ACF/Oxygen**, not these
  templates — the templates are the Phase-5 body path.
- **`skills/flow-orchestrator/SKILL.md`** — the human-approval/confirmation step the generator's
  gate slots into. Draft-first publishing (pages created as **draft**) backstops it.

---

## 3. Hard requirements / constraints (from §5, §6, §8, records.py)

- **Deterministic cache keyed on the source inputs.** Generate **once**, cache, so re-runs don't
  flip the content hash / re-bill the LLM. Cache location + format = open decision (§6).
- **Human-approval gate.** It is marketing copy on a live site — every generated block is reviewed
  before publish (flow-orchestrator confirmation + draft-first). Never auto-publish generated copy.
- **Report every generated value** to `source_issues.json` with its source-language input — a
  generated value is a datapool gap with a suggested fill (records.py:202-204). Success = that file
  shrinking as the feed improves.
- **E18 must change meaning** (`noviplast-page-adapter.md` §8): a missing `product_name.{lang}` today
  is **skipped**; with the generator it should be **planned and flagged for generation**, not dropped.
  (Currently `run_plan` logs `SKIPPED … missing product_name.fr` — 1 fr row in the pilot.)
- **Bilingual:** generate both nl and fr; fr only where the feed lacks it. WPML write path already
  handles the pair (no settings changes needed — §6).
- **Model:** use a current Claude model via the Anthropic API — load the `claude-api` skill when
  implementing (project rule: default to the latest/most capable Claude models). Codebase is sync
  (`httpx`), typed, `mypy --strict`.

---

## 4. Open decisions to settle during scoping (§6 "settle when that phase is planned")

- **LLM provider/prompt + cache location/format** — the one §6 explicitly defers to this phase.
- **Title combination rule** — how to merge 3332 into 3301 (prefix? suffix? when to omit?) without
  duplicates. Is 3332 even populated in the export? (Parser doesn't map it yet — check.)
- **Eigenschappen inputs** — confirm what's parsed: net_content ✓, 1083 ✓, 1067 ✓; **dimensions**
  and **material** sources need locating in the GDSN export (likely new gdsn_map/extras entries).
- **Tagline generation** — when 1083 absent, "first generated USP" means the Eigenschappen must be
  generated first; define the ordering/dependency.
- **How generated content is represented on the record** — new fields on `ProductRecord`? a separate
  generated-content artifact keyed by (gtin, language)? This drives the cache + hash design.
- **Where the approval gate lives** — inline in `run_execute`, or a distinct review step producing an
  approved artifact the execute step consumes.

---

## 5. Key files / entry points

- Design: `docs/clients/noviplast-page-adapter.md` §3 (ACF), §3.1 (write traps), §4.1 (three-part
  description), §4.2 (1083-is-not-the-tagline), §5 (items 3 Title, 4 Tagline, 5 Eigenschappen), §6
  (open decisions), §8 (tool-side work list). `IMPLEMENTATION_SPEC.md` §1 (conventions), §12.
- Code: `lib/gdsn.py`, `lib/records.py`, `lib/acf.py`, `lib/wp_client.py`, `lib/units.py`,
  `lib/templates.py`, `lib/state.py`, `scripts/run_plan.py`, `scripts/run_execute.py`,
  `scripts/parse_export.py`. Config: `clients.example.yml` (durable) / `clients.yml` (gitignored real).
- Data: `input/noviplast/products.xlsx` (127-product GDSN export),
  `output/noviplast/data/products.json` (parsed), the DIY datamodel xlsx.

---

## 6. Suggested first move when resuming

Scope, don't code. Produce a generator SPEC that pins the §4 open decisions — start from the
Eigenschappen bullets (the bulk) and the tagline choice, since those gate the ACF slots. Confirm
the parser gaps first (3332, dimensions, material) because they change the input contract. Then
plan in small commits per the working principles (`IMPLEMENTATION_SPEC.md` §1).
