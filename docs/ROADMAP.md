# Roadmap — phases × page-adapter track

One-screen overview tying the two planning axes together. **Not** the source of truth for phase
Definition-of-Done — that stays in [`IMPLEMENTATION_SPEC.md §12`](IMPLEMENTATION_SPEC.md) (the `[x]`
checkboxes). This file gives the big picture and tracks the generator commit-by-commit, which §12
does not. Last updated 2026-07-18.

## Two axes

- **Numbered phases (1–11)** — the horizontal framework build: the reusable tool (GS1 client, WP
  client, parser, state, plan/execute, skills, release). DoD boxes live in §12.
- **Page-adapter track (Noviplast pilot)** — a vertical, client-specific slice that **cross-cuts
  Phases 6–9** and does not fit one numbered gate (§12 says so explicitly). The **content generator**
  is its last open item. Detail in [`clients/noviplast-page-adapter.md`](clients/noviplast-page-adapter.md)
  and the [generator SPEC](clients/noviplast-generator-spec.md).

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
| — | **Page-adapter track** | Mostly done; **generator in progress** (see below) |
| 7.5 | GPC brick → category | **Done** (§12 [x], 2026-07-18) |
| 8 | Skills | Partial — `flow-orchestrator` + `content-generator` (generator c5) have SKILL.md; 4 stubs empty |
| 9 | Pilot end-to-end (≥10 live, QR scans, no manual fixes) | Not started — **gated by the generator** |
| 10 | Docs | Not started |
| 11 | Release | Not started |

"Gated"/"deferred" = code is written, the DoD step needs a live environment (staging WP, a real DL
contract, a printed QR) not yet available.

## Page-adapter track — done vs open

Done (§12 page-adapter block): field mapping resolved with the client (title 3301, 1083 unwired as a
generator input); ranked `market_priority`; source-data report; unpublish lifecycle; `net_content`
H87→word decoding. **Open:** the generator (below) and the deferred brand-typo report.

## Generator — commit tracker

Branch `noviplast-page-adapter` (unpushed). SPEC: [generator SPEC](clients/noviplast-generator-spec.md).
Suite 407 green, ruff + `mypy --strict` clean.

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
| 7 | `run_plan` merge (before `diff_against_state`; E18 backstop; `generated_issues.json`) | ▶ next |
| 8 | Wire `acf_map` (title/tagline/description → generated fields) | — |
| 9 | Docs + flow-orchestrator gate | — |

**3 commits left.** Copy producer is **both** Cowork-native (no API key) and a headless API backend,
sharing one cache/contract seam.

### How the generator commits touch the phases
- Commit 5 (Cowork generation skill) ticks a **Phase 8 (Skills)** box.
- Commit 7 sits in the **Phase 6/7** plan + change-classification machinery.
- Commit 8 is the **Phase 6** execute/write path for this client.
- Commit 9 feeds **Phase 10 (Docs)**.
- Finishing the generator **unblocks Phase 9** — the live pilot is the next milestone after it.

## The critical path
`generator (commits 4–9)` → **Phase 9** live pilot (≥10 products, QR scans, zero manual corrections)
→ Phase 10 docs → Phase 11 release. Everything before the generator on the page-adapter track is done.
