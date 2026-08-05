# Democlient pilot — handoff & steps-to-completion

**Read this first after a context clear.** It carries the live state of the Phase 9 pilot and the exact
path (incl. git workflow) from here to release. DoD checkboxes stay authoritative in
[`../IMPLEMENTATION_SPEC.md` §12](../IMPLEMENTATION_SPEC.md); the phase map is in
[`../ROADMAP.md`](../ROADMAP.md). Last updated 2026-07-26.

## Where we are (2026-07-26)

- **Phase 9.5 media is DONE and proven live.** Images (convert-all TIFF/PNG→web JPEG) and videos
  (operator name→GTIN mapping → ffmpeg H.264 MP4) publish through `run_execute._row_media`; media
  idempotency is robust via a **content-addressed media slug** (`{base}-{sha12}`). Proven on
  `08713195007717` (nl **1449** / fr **1450**): image + the correct Hydro Jet video render on both,
  GS1 resolves. §12 Phase 9.5 boxes **2 & 4 checked**.
- **The pilot is frozen to the fully-mapped GTINs.** `media.restrict_to_mapped_gtins: true` —
  `run_execute` **hard-blocks** any GTIN without a client-confirmed video in *every* language (even via
  `--plan`); `run_plan` additionally drops already-present GTINs. The allowlist is derived live from
  `input/democlient/videos/mapping.yml` (`lib.media_video.fully_mapped_gtins`).
- **The numbers:** **20** GTINs are video-mapped in both nl+fr = the allowlist. Of those, **5 are
  already live** on the site (`…0473, …1739, …3948, …5676, …7359`, per `website_status`), **2 are
  already present** (`…7717` published, `…0527` dirty draft) → **13 are the runnable batch**. 13 +
  `…7717` = 14 live, past the ≥10 DoD.
- **The immediate blocker is COPY.** All 13 have title + image + video (both languages) but **no
  generated tagline/description yet** — they are `generate`-mode. Next = generate copy → review →
  publish. **This is Step 3 below; media Step 1 is done.**
- `08713195000527` — **RESOLVED 2026-07-28: republished cleanly** (`run_execute --revive`, copy
  generated in-session first) and now live nl+fr as the 10th GTIN. Pages 1447/1448 are `publish`,
  GS1 re-enabled. It was held for *missing generated copy*, not a blank feed.
- Client is **done mapping**; the ~140 unmapped video rows are left to the client. A **GTIN-format bug**
  (the mapping is 13-digit, the pipeline 14-digit) is fixed via `canon_gtin` (zfill-14).
- **Git:** media landed via **PR #7** + a content-slug/docs PR; the pilot-allowlist + this handoff
  update land in **PR `feat/pilot-gtin-allowlist`**. Once merged, `main` is current and a fresh session
  starts at **Step 3**.

## Load-bearing invariants (do not relearn the hard way)

- **The ACF pipeline fails silently.** Oxygen renders from ACF; a blank page still returns 200 and passes
  `verify_url` (unauth HEAD). Always fetch the public page and confirm the copy is *in the HTML*.
- **Resolution: test with GET, never HEAD.** `id.gs1.org` returns **404 to HEAD** but **307 to GET** for a
  good record. Use `curl -sSL` / `-o /dev/null -w`.
- **GS1 v2 has no DELETE.** `retract` only disables (`isEnabled:false`); the record persists forever.
  Register only GTINs you're committed to. WP pages are fully reversible (draft/delete).
- **Production, live site.** `clients.yml` GS1 `environment: production` (account `8719965024137`); WP
  `post_status: publish` on `www.democlient.nl` (no separate staging). Safety = GTIN choice + dry-run +
  review, not environment isolation.
- **Env:** `set -a; source .env; set +a`. `DEMOCLIENT_WP_APP_PASS` must stay **single-quoted** (spaces).
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
- **OPEN (boxes 1, 3):** the name→GTIN mapping is drafted at `input/democlient/videos/mapping.yml` (166
  files; 26 strong pre-fills + `…7717` confirmed) but needs **client sign-off** on the rest; then
  `build_video_map democlient --check` must exit 0. Watch: `Seal Strip.mpg` is 0 bytes (re-copy);
  filenames are **English marketing names**, mostly not in the feed, so most rows are a human call.
- **WP-side (operator):** added `register_post_meta('attachment','content_sha256', …)` to the
  "expose CPT to REST" snippet (now optional — dedup no longer needs it; see §7).
- **Pilot-gate DONE** (PR `feat/pilot-gtin-allowlist`): `media.restrict_to_mapped_gtins` blocks every
  non-fully-mapped GTIN from runs, plus the 13/14-digit `canon_gtin` fix. See "Where we are".

### Step 2 — Phase 9.8 Operator flow under Claude Code (validation) — ✅ DONE (2026-07-30)
- Drove `flow-orchestrator` **end-to-end in a Claude Code chat**, operator answering each gate: language
  select (`all`) → review gate #1 (`approve`) → plan review gate #2 (`changed-review`) → per-row diff gate
  §10.6.2 (`apply`) → production env-confirmation (`confirm`) → execute (`--dry-run`) → progress →
  post-execute summary (`no`). No code gaps — all gates rendered verbatim and behaved correctly.
- The pilot is exhausted (0 actionable rows), so a **reversible dry-run harness** supplied the rows
  (gitignored `clients.yml`: `post_status: draft` + `restrict_to_mapped_gtins: false`; one live GTIN's
  state staled to force a CHANGED diff). `--dry-run` writes nothing (no WP/GS1/state); harness torn down,
  `state.json` verified byte-identical, `run_plan` back to 0 rows. Details: `../IMPLEMENTATION_SPEC.md` §12
  Phase 9.8 status.
- §12 Phase 9.8 (×4) + the open Phase 8 box #4 are ticked. **Not live-fired** (documented + code-covered):
  off-menu-reply branch, retry `yes` path, missing-field prompt §10.6.5.

### Step 3 — Finish Phase 9: publish the 13-GTIN batch — **THE ACTIVE STEP**
The pilot-gate has already scoped the runnable batch. `run_plan democlient` writes the 13 GTINs (26
rows) to `output/democlient/plan.json`. Readiness (checked 2026-07-26): all 13 have title + image +
video (both langs); **none have generated copy yet** — that is the blocker.
1. **Generate copy for the 13** — in-session `content-generator` (no API key). They are
   `generate`-mode, so the LLM writes tagline + **Eigenschappen** bullets from the marketing message +
   net content + dims/material (Technische details stay deterministic). Write `generation_results.json`.
2. **Review Gate #1** — a human/client approves each product's tagline + bullets (live marketing copy).
3. `run_generate democlient --ingest` → `run_plan democlient` (merges copy). Re-check readiness — all green.
4. **Dry-run:** `run_execute democlient --plan <13-batch> --dry-run` (26 rows, nothing blocked).
5. **Publish staged (LIVE, per-wave operator go-ahead):** a first wave of 2–3, fully verified, then the
   rest. Each sets page (title + copy + image + video), links nl/fr, GS1 record, QR. The pilot-gate
   guarantees no non-mapped GTIN is written.
6. **Verify each:** public page renders copy + image + video (fetch the HTML, not just 200);
   `GET id.gs1.org/01/{gtin}` → 307 → page → 200 (GET, never HEAD). Client does a **physical phone scan**
   on printed QR samples (the DoD's literal requirement).
7. **DONE 2026-07-28:** `…0527` republished as the 10th GTIN; **fr-QR strategy decided — keep
   as-is** (bare QR → nl default page; fr reached via the site language switcher). §12 Phase 9
   boxes: all three **ticked** — ≥10-live, no-manual-corrections, and the physical printed-QR
   phone-scan (confirmed working 2026-07-28). **Phase 9 is complete.**
- Phase 9.8 (Claude Code operator-flow validation, Step 2) can run alongside — the batch mechanics are proven
  via scripts, so it is not a hard blocker for going live.

### Step 4 — Phase 10 Docs — branch `docs/phase-10` — **DONE 2026-07-30**
All three §12 boxes ticked. The seven docs named in `PROJECT_HANDOVER.md` §8.2 are written
(`setup.md`, `troubleshooting.md`, `gs1-nl-onboarding.md`, `wordpress-onboarding.md`,
`data-source-export-schema.md`, `template-variables.md`, `costs.md`), `README.md` rewritten, and
doc-vs-code drift corrected in §4.1 / §4.5 / §8 and `PREPARATION.md` §3.18. Everything derived from
the code at HEAD rather than the planning docs.

Box 1 proven by **executing `setup.md` verbatim from a fresh clone** (clean venv → install → ruff →
format → mypy → pytest → config load → `--help` on all nine scripts → `inspect_export` /
`parse_export --dry-run` on the real export → production-guard refusal at exit 2). That run exposed a
real defect — `inspect_export --help` crashed with an unhandled `InvalidFileException` — which was
fixed with tests rather than documented around.

**Start here when onboarding anyone new, or a second client:** [`../setup.md`](../setup.md), then
[`../troubleshooting.md`](../troubleshooting.md).

### Step 5 — Phase 11 Release — branch `release/v0.1.0`
Version bump (`pyproject.toml`, `package.json`); populate `CHANGELOG.md`; push git tag `v0.1.0`; submit
MCP registry entry; draft announcement. Tick §12 Phase 11.

## One-GTIN / batch run mechanics (reusable)
1. Ensure generated copy exists: for `tighten`-mode GTINs (attr 1067 present) faithfully shorten
   `candidates`; for `generate` mode write from 1083 + context. Write `generation_results.json` (echo each
   `input_fingerprint`), `run_generate democlient --ingest`, then re-run `run_plan democlient` so
   `generated_tagline`/`generated_description` merge into `plan.json`.
2. Slice the wanted rows from `output/democlient/plan.json` into a minimal Plan
   (`{client_id, generated_at, total, counts, rows}`) and run
   `run_execute democlient --plan <file>` (treats every row as confirmed; add
   `--i-understand-production` for a live prod run — it is refused without it) — or drive it via
   flow-orchestrator once Phase 9.8 is validated. `--dry-run` previews with no writes.
3. Verify render + resolution (see invariants). Rollback if wrong: `set_page_status(draft)`/`delete_page`
   + `gs1.retract`.

## Pointers
- Page model / WPML / write traps / QR-language: [`democlient-page-adapter.md`](democlient-page-adapter.md).
- Generator contract / voice: [`democlient-generator-spec.md`](democlient-generator-spec.md),
  `prompts/democlient/generation.v1.md`, `.claude/skills/content-generator/SKILL.md`.
- Operator flow: `.claude/skills/flow-orchestrator/SKILL.md`.
- **What's live:** [`democlient-live-log.md`](democlient-live-log.md) — committed audit trail of every
  page/GS1 record published to the live site (machine source: gitignored `output/democlient/state.json`).
- Auto-memory: `phase9-resolution-proven.md` (this pilot's live state + gotchas).
