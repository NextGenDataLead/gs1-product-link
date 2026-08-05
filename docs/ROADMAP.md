# Roadmap — phases × page-adapter track

One-screen overview tying the two planning axes together. **Not** the source of truth for phase
Definition-of-Done — that stays in [`IMPLEMENTATION_SPEC.md §12`](IMPLEMENTATION_SPEC.md) (the `[x]`
checkboxes). This file gives the big picture and tracks the generator commit-by-commit, which §12
does not. Last updated 2026-07-30.

**New here?** Read [`../README.md`](../README.md) for what the tool does, then
[`setup.md`](setup.md) to run it. This file is for tracking build status.

## Two axes

- **Numbered phases (1–11)** — the horizontal framework build: the reusable tool (GS1 client, WP
  client, parser, state, plan/execute, skills, release). DoD boxes live in §12.
- **Page-adapter track (Democlient pilot)** — a vertical, client-specific slice that **cross-cuts
  Phases 6–9** and does not fit one numbered gate (§12 says so explicitly). Its last critical-path
  item, the **content generator, is now complete** (all 9 commits). Detail in
  [`clients/democlient-page-adapter.md`](clients/democlient-page-adapter.md) and the
  [generator SPEC](clients/democlient-generator-spec.md).

## Phase status (summary — authoritative boxes in §12)

| Phase | What | Status |
|---|---|---|
| 1 | Repo skeleton | Built — ruff/mypy/pytest/CI green |
| 2 | GS1 Digital Link client + MCP | Built; live DoD **gated** (sandbox has no DL contract — 21011) |
| 3 | Excel/GDSN parser + records | Built — 127 products nl+fr, round-trip |
| 4 | WordPress client + MCP | Built & merged; staging DoD **deferred** (no staging WP provisioned) |
| 5 | QR + templates | Built; physical phone scan of a printed sample **confirmed** 2026-07-28 (via Phase 9) |
| 6 | lib, scripts, state | Built; end-to-end-on-staging DoD **gated** (no staging WP — proven on live instead, Phase 9) |
| 7 | Re-run + change detection | **Done** (§12 [x]) |
| — | **Page-adapter track** | Done — **generator complete** (all 9 commits, see below) |
| 7.5 | GPC brick → category | **Done** (§12 [x], 2026-07-18) |
| 8 | Skills | **Done** (§12 all 4 [x]) — all 6 SKILL.md finalised; chat flow validated on real files; the execute leg proven in Phase 9 and the full plan → diff → confirm → execute loop walked in Phase 9.8, which ticked the last box |
| 9 | Pilot end-to-end (≥10 live, QR scans, no manual fixes) | **Done** (§12 all 3 [x], 2026-07-28). 10 GTINs live nl+fr; all resolve via GET → 307 → 200; printed-QR phone scan confirmed; both waves ran 0-error. fr-QR strategy decided: keep as-is (bare QR → nl default, fr via the site switcher). Audit trail in [`clients/democlient-live-log.md`](clients/democlient-live-log.md) |
| 9.5 | Media (images + video) | **Code merged (PR #7) + proven live (2026-07-20).** Image+video render on pilot 1449/1450; media idempotent (content-addressed slug). **Open:** the drafted name→GTIN mapping (166 files) needs **client sign-off** (§12 boxes 1/3) |
| 9.8 | Operator flow (Claude Code) | **Done** (§12 all 4 [x], PR #29 `071f8fe`, 2026-07-30). `flow-orchestrator` driven end-to-end in a fresh Claude Code session with the operator answering every gate, via a reversible dry-run harness (nothing written; `state.json` verified byte-identical after teardown). Ticked the open **Phase 8 box #4** |
| 10 | Docs | **Done** (§12 all 3 [x], 2026-07-30). Seven `docs/*.md` written **from the code at HEAD**; README status corrected; drift fixed (§4.1, §4.5, §8, PREPARATION §3.18). `setup.md` proven by **executing it verbatim from a fresh clone** — which surfaced and got a real `inspect_export --help` crash fixed |
| 11 | Release | **Not started — the last phase.** Version bump (`pyproject.toml` is still `0.0.1`, `package.json`), `CHANGELOG.md`, `v0.1.0` tag, MCP registry entry, announcement |

"Gated"/"deferred" = code is written, the DoD step needs a live environment (staging WP, a real DL
contract, a printed QR) not yet available.

## Page-adapter track — done vs open

Done (§12 page-adapter block): field mapping resolved with the client (title 3301, 1083 unwired as a
generator input); ranked `market_priority`; source-data report; unpublish lifecycle; `net_content`
H87→word decoding; **the content generator (all 9 commits, below).** **Open:** only the deferred
brand-typo report — everything on the page-adapter critical path is done.

## Generator — commit tracker

Merged to `main` (the `democlient-page-adapter` branch is history).
SPEC: [generator SPEC](clients/democlient-generator-spec.md).
Suite green at HEAD — 550 passed, 2 skipped, 5 deselected (staging); ruff + `mypy --strict` clean.

| # | Commit | State |
|---|---|---|
| 1 | Parser inputs → `gdsn_extras` (variation, dims, material) | ✅ `3b2ffb5` |
| 2 | `generated_tagline`/`generated_description` record fields | ✅ `d5e8b0f` |
| 3 | `lib/generator.py` deterministic core (cache, contract, merge) | ✅ `43b3256` |
| — | Capture all 1067 slots (multivalue) | ✅ `babd01b` |
| — | 1067 routing: verbatim / tighten / generate + adjusted report | ✅ `3fff444` |
| 4 | `scripts/run_generate.py` spine + `LLMClient` seam + `--emit`/`--ingest` | ✅ `a61c1fd` |
| 5 | In-session producer (generation skill + voice) | ✅ `bf31dd9` |
| 6 | API backend (`lib/llm.py`, Sonnet 5, `--backend api`) | ✅ `c6a91e4` |
| 7 | `run_plan` merge (before `diff_against_state`; E18 backstop; `generated_issues.json`) | ✅ `6316ad4` |
| 8 | Wire `acf_map` (title/tagline/description → generated fields) | ✅ `3b44ba7` |
| 9 | Docs + flow-orchestrator gate | ✅ `2999201` |

**Generator COMPLETE (all 9 commits, 2026-07-19).** Copy producer is **both** in-session (no API
key) and a headless API backend, sharing one cache/contract seam. It went on to feed the Phase 9 live
pilot, which is now also complete.

### How the generator commits touch the phases
- Commit 5 (in-session generation skill) ticks a **Phase 8 (Skills)** box.
- Commit 7 sits in the **Phase 6/7** plan + change-classification machinery.
- Commit 8 is the **Phase 6** execute/write path for this client.
- Commit 9 fed **Phase 10 (Docs)** — now superseded by the full `docs/` set (see
  [`setup.md`](setup.md) and [`costs.md`](costs.md) for the operator-facing generator docs).
- Finishing the generator unblocked **Phase 9**, which has since completed.

## The critical path

Everything through Phase 9.8 is **done**:

~~`generator (commits 4–9)`~~ → ~~verify WPML helper + a real published ACF page live~~ →
~~**Phase 9** execute + resolution on the first real GTIN~~ → ~~**Phase 9.5 media**~~ (code merged and
proven live; only the client sign-off on the video mapping is open) → ~~**finish Phase 9**~~ (10 live,
QR scans confirmed, fr-QR strategy decided) → ~~**Phase 9.8 operator flow**~~ (`flow-orchestrator`
driven end-to-end under Claude Code through every gate).

~~**Phase 10 docs**~~ **done** (2026-07-30) → ~~**Phase 11 release**~~ **done (2026-07-30) — every
numbered phase is now complete.**

Phase 11 shipped `v0.1.0`: version bumped to `0.1.0` across `pyproject.toml`, `package.json`, the
three `mcps/*/package.json` and the lockfile; `CHANGELOG.md` reconstructed from 92 unrecorded
commits and promoted to `[0.1.0]`; the `v0.1.0` tag and GitHub release published; the announcement
drafted at `docs/announcement-v0.1.0.md` (**not** published). The **MCP registry entry will not be
submitted** — decided 2026-07-31 ([OD-2](OPEN_DECISIONS.md#resolved)): the three servers stay
private, so that DoD box is unticked by choice rather than left as outstanding work. The
`server.json` files are committed and schema-valid, so it is cheap to reverse.

Also resolved alongside the release: [OD-1](OPEN_DECISIONS.md) — `.env` is now the single source of
truth for credentials, and the ambient `~/.claude/settings.json` `env` block is gone.

**Still open and not ours:** the client's sign-off on the video mapping (~140 unmapped rows). The
exposed WordPress application password was **rotated on 2026-07-30** and the old one revoked.

Two standing invariants, both learned the hard way and neither closed by a phase:

- **The ACF pipeline fails silently.** Verify each page by fetching its rendered HTML against the live
  site — a 200 proves the post exists, not that its fields landed. Green tests prove nothing here.
- **Test resolution with GET, never HEAD.** `id.gs1.org` 404s to HEAD and 307s to GET.
