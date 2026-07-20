# Roadmap — phases × page-adapter track

One-screen overview tying the two planning axes together. **Not** the source of truth for phase
Definition-of-Done — that stays in [`IMPLEMENTATION_SPEC.md §12`](IMPLEMENTATION_SPEC.md) (the `[x]`
checkboxes). This file gives the big picture and tracks the generator commit-by-commit, which §12
does not. Last updated 2026-07-19.

## Two axes

- **Numbered phases (1–11)** — the horizontal framework build: the reusable tool (GS1 client, WP
  client, parser, state, plan/execute, skills, release). DoD boxes live in §12.
- **Page-adapter track (Noviplast pilot)** — a vertical, client-specific slice that **cross-cuts
  Phases 6–9** and does not fit one numbered gate (§12 says so explicitly). Its last critical-path
  item, the **content generator, is now complete** (all 9 commits). Detail in
  [`clients/noviplast-page-adapter.md`](clients/noviplast-page-adapter.md) and the
  [generator SPEC](clients/noviplast-generator-spec.md).

## Phase status (summary — authoritative boxes in §12)

| Phase | What | Status |
|---|---|---|
| 1 | Repo skeleton | Built — ruff/mypy/pytest/CI green |
| 2 | GS1 Digital Link client + MCP | Built; live DoD **gated** (sandbox has no DL contract — 21011) |
| 3 | Excel/GDSN parser + records | Built — 127 products nl+fr, round-trip |
| 4 | WordPress client + MCP | Built & merged; staging DoD **deferred** (no staging WP provisioned) |
| 5 | QR + templates | Built; physical iOS/Android scan DoD pending |
| 6 | lib, scripts, state | Built; end-to-end-on-staging DoD **gated** |
| 7 | Re-run + change detection | **Done** (§12 [x]) |
| — | **Page-adapter track** | Done — **generator complete** (all 9 commits, see below) |
| 7.5 | GPC brick → category | **Done** (§12 [x], 2026-07-18) |
| 8 | Skills | **Done except execute leg** — all 6 SKILL.md finalised; chat flow (parse→generate→plan→confirm) validated on real files; execute leg deferred to Phase 9 (§12) |
| 9 | Pilot end-to-end (≥10 live, QR scans, no manual fixes) | **In progress — paused.** Execute + resolution **proven live** on the first real GTIN (`08713195007717`, nl+fr; QR resolves via `id.gs1.org`). ≥10 batch paused by operator choice; §12 boxes stay unchecked |
| 9.5 | Media (images + video) | **Code merged (PR #7) + proven live (2026-07-20).** Image+video render on pilot 1449/1450; media idempotent (content-addressed slug). **Open:** the drafted name→GTIN mapping (166 files) needs **client sign-off** (§12 boxes 1/3) |
| 9.8 | Operator flow in Cowork | **New — not started.** Drive `flow-orchestrator` end-to-end from a real Cowork chat session (all gates incl. execute), operator guided step-by-step. Ticks open Phase 8 box #4 |
| 10 | Docs | Not started |
| 11 | Release | Not started |

"Gated"/"deferred" = code is written, the DoD step needs a live environment (staging WP, a real DL
contract, a printed QR) not yet available.

## Page-adapter track — done vs open

Done (§12 page-adapter block): field mapping resolved with the client (title 3301, 1083 unwired as a
generator input); ranked `market_priority`; source-data report; unpublish lifecycle; `net_content`
H87→word decoding; **the content generator (all 9 commits, below).** **Open:** only the deferred
brand-typo report — everything on the page-adapter critical path is done.

## Generator — commit tracker

Branch `noviplast-page-adapter` (unpushed). SPEC: [generator SPEC](clients/noviplast-generator-spec.md).
Suite 414 green, ruff + `mypy --strict` clean.

| # | Commit | State |
|---|---|---|
| 1 | Parser inputs → `gdsn_extras` (variation, dims, material) | ✅ `3b2ffb5` |
| 2 | `generated_tagline`/`generated_description` record fields | ✅ `d5e8b0f` |
| 3 | `lib/generator.py` deterministic core (cache, contract, merge) | ✅ `43b3256` |
| — | Capture all 1067 slots (multivalue) | ✅ `babd01b` |
| — | 1067 routing: verbatim / tighten / generate + adjusted report | ✅ `3fff444` |
| 4 | `scripts/run_generate.py` spine + `LLMClient` seam + `--emit`/`--ingest` | ✅ `a61c1fd` |
| 5 | Cowork-native producer (generation skill + voice) | ✅ `bf31dd9` |
| 6 | API backend (`lib/llm.py`, Sonnet 5, `--backend api`) | ✅ `c6a91e4` |
| 7 | `run_plan` merge (before `diff_against_state`; E18 backstop; `generated_issues.json`) | ✅ `6316ad4` |
| 8 | Wire `acf_map` (title/tagline/description → generated fields) | ✅ `3b44ba7` |
| 9 | Docs + flow-orchestrator gate | ✅ `2999201` |

**Generator COMPLETE (all 9 commits, 2026-07-19).** Copy producer is **both** Cowork-native (no API
key) and a headless API backend, sharing one cache/contract seam. Next milestone: **Phase 9** live
pilot (now unblocked).

### How the generator commits touch the phases
- Commit 5 (Cowork generation skill) ticks a **Phase 8 (Skills)** box.
- Commit 7 sits in the **Phase 6/7** plan + change-classification machinery.
- Commit 8 is the **Phase 6** execute/write path for this client.
- Commit 9 feeds **Phase 10 (Docs)**.
- Finishing the generator **unblocks Phase 9** — the live pilot is the next milestone after it.

## The critical path
~~`generator (commits 4–9)`~~ **done** → ~~verify WPML helper + a real published ACF page live~~ **done**
→ ~~**Phase 9** execute + resolution on the first real GTIN~~ **PROVEN (paused)** → **Phase 9.5 media**
(images from export URLs; videos from the operator's nl/fr folders via a client-confirmed name→GTIN
mapping) → **Phase 9.8 operator flow in Cowork** (drive `flow-orchestrator` end-to-end through its chat
gates, operator guided step-by-step) → **finish Phase 9** (scale to ≥10 live via that validated flow,
decide fr-QR strategy, tick §12) → Phase 10 docs → Phase 11 release. The pilot's execute + QR resolution are validated live on `08713195007717`; the
remaining pilot work is media + the ≥10 batch. The ACF pipeline still fails silently — verify each page
renders against the live site, not just green tests.
