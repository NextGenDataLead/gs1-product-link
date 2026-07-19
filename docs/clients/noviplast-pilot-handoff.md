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

### Step 0 — Land the current doc changes (do this first)
The working tree has uncommitted edits to `IMPLEMENTATION_SPEC.md`, `ROADMAP.md`, and this new file, and
the session is on the stale merged branch `phase-8-docs-followup`.
```
git fetch
git stash                       # carry the 3 doc edits safely
git switch -c docs/phase-9-pilot-status origin/main
git stash pop
git add docs/IMPLEMENTATION_SPEC.md docs/ROADMAP.md docs/clients/noviplast-pilot-handoff.md
git commit -m "docs(spec): record Phase 9 execute+resolution proof; add Phase 9.5 media and 9.8 Cowork-flow phases"
git push -u origin docs/phase-9-pilot-status     # then open a PR; docs-only, no DoD box ticked
```

### Step 1 — Phase 9.5 Media (code) — branch `feat/phase-9.5-media`
- **Images:** download each product's export `image_url` (public `.jpg` on GDSN blob storage) →
  `wp_client.upload_media` → set the image ACF field(s) (`product_header_image`/`product_regular_image`).
  First confirm the ACF image **write-shape live** on page 1449 (attachment id vs URL — the §3 trap).
  Add the field(s) to `clients.yml` `acf_map`.
- **Videos:** operator supplies two local folders (nl, fr); files named **by product name, not GTIN**.
  Build a **draft name→GTIN mapping** against the feed names and **get operator confirmation before any
  upload** (names carry known brand-prefix typos). Then per language → `upload_media` → ACF
  `product_header_video_file` (caption `product_header_video_text` already = tagline). Revise the
  `{gtin}*.mp4` single-folder assumption in `noviplast-page-adapter.md` §3.
- Replace `run_execute.py` `featured_media=None`; keep re-runs idempotent. TDD; gates; PR; merge.
- Ticks the four §12 Phase 9.5 boxes.

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
