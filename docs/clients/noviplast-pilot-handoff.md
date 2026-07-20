# Noviplast pilot — handoff & steps-to-completion

**Read this first after a context clear.** It carries the live state of the Phase 9 pilot and the exact
path (incl. git workflow) from here to release. DoD checkboxes stay authoritative in
[`../IMPLEMENTATION_SPEC.md` §12](../IMPLEMENTATION_SPEC.md); the phase map is in
[`../ROADMAP.md`](../ROADMAP.md). Last updated 2026-07-19.

## Where we are

- **Phase 9 execute + resolution is PROVEN live, then PAUSED by operator choice.** One real GTIN,
  `08713195007717` (Hogedrukreiniger / Nettoyeur haute pression), is published: WP pages **1449 (nl) /
  1450 (fr)**, both render their ACF text, GS1 **production** record enabled (`gs1:pip` nl+fr), QR
  resolves (`GET https://id.gs1.org/01/08713195007717` → 307 → nl page → 200). Leave it live.
- **Only 1 of ≥10 products is live**, so Phase 9's three §12 boxes stay **unchecked**. Pages are
  **text-only** (media deferred). One QR resolves only to the **nl** default.
- `08713195000527` is a **dirty prior test artifact** (pages 1447/1448 draft, GS1 disabled) — never reuse.
- Generation used the **Cowork-native producer** (no `ANTHROPIC_API_KEY`); the API backend is untested
  but optional. The pilot was driven by **calling scripts directly**, which bypassed the operator UX —
  that gap is Phase 9.8.
- **Git: on `main`, clean, in sync with `origin/main`.** The Phase 9 status note + the 9.5/9.8 phases +
  this runbook landed via **PR #5** (commit `b850176`, merge `a8463e6`); all session branches are deleted.
  **A fresh session starts at Step 1** below — Step 0 is already done.

## Load-bearing invariants (do not relearn the hard way)

- **The ACF pipeline fails silently.** Oxygen renders from ACF; a blank page still returns 200 and passes
  `verify_url` (unauth HEAD). Always fetch the public page and confirm the copy is *in the HTML*.
- **Resolution: test with GET, never HEAD.** `id.gs1.org` returns **404 to HEAD** but **307 to GET** for a
  good record. Use `curl -sSL` / `-o /dev/null -w`.
- **GS1 v2 has no DELETE.** `retract` only disables (`isEnabled:false`); the record persists forever.
  Register only GTINs you're committed to. WP pages are fully reversible (draft/delete).
- **Production, live site.** `clients.yml` GS1 `environment: production` (account `8719965024137`); WP
  `post_status: publish` on `www.noviplast.nl` (no separate staging). Safety = GTIN choice + dry-run +
  review, not environment isolation.
- **Env:** `set -a; source .env; set +a`. `NOVIPLAST_WP_APP_PASS` must stay **single-quoted** (spaces).
- **Single QR → nl default; no single QR robustly routes by language** (resolver 404s on unsupported
  `Accept-Language` with `?linkType=`). Decide fr-QR strategy in Phase 9 finish (see page-adapter doc).

## Git workflow (applies to every step)

Rule of thumb per unit of work: **fetch → branch off `origin/main` → work → gates → commit → push → PR →
review → merge**, then the next unit branches off the freshly-updated `main`. Never commit on an
already-merged branch. Gates before every commit that touches code:
`.venv/bin/python -m pytest -q` · `.venv/bin/ruff check` · `.venv/bin/ruff format --check` ·
`.venv/bin/mypy --strict lib`. Runtime artifacts under `output/` are gitignored — never commit them.
Commit/push only when the operator asks.

## Steps to completion

### Step 0 — Land the doc changes — ✅ DONE (2026-07-19, PR #5)
The Phase 9 status note, the new Phase 9.5/9.8 DoD blocks, and this runbook are merged to `main`
(commit `b850176`, merge `a8463e6`); session branches deleted; workspace clean. **Start at Step 1.**
(Retained only as the template for the per-step git flow: fetch → branch off `origin/main` → work →
gates → commit → push → PR → merge → sync `main` → delete the branch.)

### Step 1 — Phase 9.5 Media — code DONE + proven live (2026-07-20); **open: client sign-off on the video mapping**
- **DONE (merged PR #7):** `lib/media.py` (convert-all TIFF/PNG→web JPEG), `lib/media_video.py`
  (operator-authored name→GTIN mapping + ffmpeg MP4 transcode), `scripts/build_video_map.py`
  (draft/`--check`), `MediaConfig`, and `run_execute._row_media` (hero → `product_header_image`/
  `_regular_image` + `featured_media`; video → `product_header_video_file`). Image write-shape confirmed
  live = **attachment id** (`media.image_write_shape: id`). Media dedup made robust via a
  **content-addressed slug** (`{base}-{sha12}`) after a live idempotency bug (see §7 / §12).
- **PROVEN LIVE** on `08713195007717` (nl 1449 / fr 1450): image + correct video (Hydro Jet) render on
  both; re-runs reuse the same 4 attachments. §12 boxes **2 and 4 checked**.
- **OPEN (boxes 1, 3):** the name→GTIN mapping is drafted at `input/noviplast/videos/mapping.yml` (166
  files; 26 strong pre-fills + `…7717` confirmed) but needs **client sign-off** on the rest; then
  `build_video_map noviplast --check` must exit 0. Watch: `Seal Strip.mpg` is 0 bytes (re-copy);
  filenames are **English marketing names**, mostly not in the feed, so most rows are a human call.
- **WP-side (operator):** added `register_post_meta('attachment','content_sha256', …)` to the
  "expose CPT to REST" snippet (now optional — dedup no longer needs it; see §7).

### Step 2 — Phase 9.8 Operator flow in Cowork (validation) — branch only if code gaps found
- Drive the `flow-orchestrator` skill **end-to-end from a real Cowork chat session** on ≥1 GTIN,
  draft-first, **guiding the operator step-by-step**: present each gate, wait for the choice, then proceed
  — language select → review gate #1 → plan review gate #2 → **production env-confirmation gate** →
  execute → progress → post-execute summary → retry. Never batch or auto-confirm.
- If gaps surface, fix on a branch (TDD/gates/PR). Otherwise tick §12 Phase 9.8 + the open Phase 8 box #4
  with a docs commit.

### Step 3 — Finish Phase 9: scale to ≥10 — branch `feat/phase-9-batch` if code, else docs
- Decide the **fr-QR strategy** (recommended: keep bare QR→nl + WPML switcher; alt: separate fr QR direct
  to `/fr/` URL). Code only if a separate fr QR is chosen.
- For ~9 more GTINs from the shortlist (raamwisser `08713195000862`, onkruidborstel `...000961`,
  zuignaphaak `...003139`, vliegenverjager `...003474`, grondpen `...004358`, bureaulamp `...004488`,
  afvoerzeef `...004778`, papiermes `...004839`, ledstrip `...005829`, Tafellamp `...005898`): generate
  copy (Cowork-native; these are `mode: generate` so bullets are LLM-written — **review gate #1 matters**),
  `run_generate --ingest`, re-run `run_plan`, then publish **through the validated flow-orchestrator**
  (Phase 9.8), not raw scripts.
- Verify each: public page renders the copy; `GET id.gs1.org/01/{gtin}` resolves (307→page→200). Client
  does a **physical phone scan** on printed QR samples (the DoD's literal requirement).
- Tick the three §12 Phase 9 boxes + update `ROADMAP.md`. Docs commit.

### Step 4 — Phase 10 Docs — branch `docs/phase-10`
Setup steps run by an unfamiliar person; docstring coverage on every skill/script; `troubleshooting.md`
covers each §4.1 error type. Tick §12 Phase 10. PR; merge.

### Step 5 — Phase 11 Release — branch `release/v0.1.0`
Version bump (`pyproject.toml`, `package.json`); populate `CHANGELOG.md`; push git tag `v0.1.0`; submit
MCP registry entry; draft announcement. Tick §12 Phase 11.

## One-GTIN / batch run mechanics (reusable)
1. Ensure generated copy exists: for `tighten`-mode GTINs (attr 1067 present) faithfully shorten
   `candidates`; for `generate` mode write from 1083 + context. Write `generation_results.json` (echo each
   `input_fingerprint`), `run_generate noviplast --ingest`, then re-run `run_plan noviplast` so
   `generated_tagline`/`generated_description` merge into `plan.json`.
2. Slice the wanted rows from `output/noviplast/plan.json` into a minimal Plan
   (`{client_id, generated_at, total, counts, rows}`) and run
   `run_execute noviplast --plan <file>` (treats every row as confirmed) — or drive it via
   flow-orchestrator once Phase 9.8 is validated. `--dry-run` previews with no writes.
3. Verify render + resolution (see invariants). Rollback if wrong: `set_page_status(draft)`/`delete_page`
   + `gs1.retract`.

## Pointers
- Page model / WPML / write traps / QR-language: [`noviplast-page-adapter.md`](noviplast-page-adapter.md).
- Generator contract / voice: [`noviplast-generator-spec.md`](noviplast-generator-spec.md),
  `prompts/noviplast/generation.v1.md`, `skills/content-generator/SKILL.md`.
- Operator flow: `skills/flow-orchestrator/SKILL.md`.
- Auto-memory: `phase9-resolution-proven.md` (this pilot's live state + gotchas).
