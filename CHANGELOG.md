# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-07-30

First working release. The tool turns a GS1 Data Source (GDSN) export into GS1 Digital Link QR
codes and multilingual WordPress product pages, and it has been proven end to end on a live
pilot: 10 GTINs published to a production site in Dutch and French, each with an enabled GS1
production record whose QR resolves to the right page, and one confirmed scanning from a printed
label. `0.0.1` was a repository skeleton; everything below is what filled it in.

### Added
- `lib/errors.py` — typed exception hierarchy (`OrchestratorError` base plus
  `GS1APIError`, `ConfigError`, `MissingCredentialError`, and others) per
  `docs/IMPLEMENTATION_SPEC.md` §4.1.
- `lib/logging_setup.py` — `scrub_response_body` and `scrub_headers` that redact
  secrets and `meta.*` from log output (§5.2).
- `lib/gs1_dl_client.py` — synchronous GS1 NL Digital Link API v2 client
  (`upsert`, `upsert_bulk`, `get`, `set_enabled`, `validate_draft`) with the §5.1
  retry policy, structured 400 `ErrorResult[]` parsing, and token-scrubbed
  logging. Path-case anomalies (capital-L `digitalLink` for GET/PATCH; no `/v2/`
  in ValidateDraft) preserved.
- `mcps/gs1-nl/` — TypeScript MCP server exposing three tools
  (`gs1_digital_link_upsert`, `gs1_digital_link_upsert_bulk`,
  `gs1_digital_link_get`) over stdio, resolving client config from `clients.yml`
  (§9.1); mirrors the Python client's hosts, paths, auth, and retry policy.
- Tests: `pytest`/`pytest-httpx` for the Python client (idempotency, retry,
  error parsing, token scrubbing) and `vitest` for the MCP client, config, and
  tools (including end-to-end tool calls over an in-memory transport). A skipped
  fixture-backed test slot awaits captured GS1 responses (§13.2).
- CI: a Node job builds and tests the `mcps/gs1-nl` workspace.

- `gs1_dl_client.safe_upsert()` + `OverwriteError` — a GET-before-write guard that
  refuses to overwrite an existing Digital Link unless `overwrite=True` and returns
  the prior snapshot for rollback (§5.4). Prevents silently clobbering a live
  resolver target on production runs.

- **Phase 3 — Excel parser + records schema.**
  - `lib/records.py` — canonical `ProductRecord`/`LocalisedText` plus `Plan`,
    `PlanRow`, `ConfirmedPlan`, `RunOutcome`, `StateEntry`, `State` (§2), and the
    flat-export `parse_excel_row` (§4.9).
  - `lib/gdsn.py` — reader for GS1 Data Source / GDSN datapool exports (multi-sheet,
    7 header rows, `Gtin` + `TargetMarketCountryCode` composite key, `LanguageCode`/
    `Value` pairs). Joins sheets by GTIN into `ProductRecord`s via a per-client
    attribute map. A spec extension over §2/§3's flat single-sheet assumption.
  - `lib/config.py` — `clients.yml` loader (`load_clients`/`get_client`) with
    jsonschema validation, `defaults` inheritance, lazy secrets, and the
    `GS1Config.resolve()` bridge to the Phase-2 client shape (§2.4, §4.2). Extended
    `ExportConfig` with `format`, `market_language`, `gdsn_map`, `gdsn_extras`.
  - `scripts/parse_export.py` — GDSN- and flat-aware CLI producing
    `output/{client_id}/data/products.json` (§8.1).
  - `scripts/inspect_export.py` — onboarding utility that lists worksheet attributes
    and suggests a `gdsn_map` (§8.5).
  - `schema/clients.schema.json` — `export` block extended for the GDSN format.
  - Pilot: Noviplast's real GDSN export parses to 127 products (nl + fr) with zero
    warnings.

- **Phase 4 — WordPress client + MCP.**
  - `lib/wp_client.py` — synchronous WordPress REST API v2 client (§4.4): HTTP Basic
    auth with a lazily-resolved application password, the §5.1 retry policy (429/5xx
    with independent budgets; a `401` is terminal), idempotent `upsert_page`
    (3-step lookup id → slug → `meta.gtin`, §6.1), SHA-256-deduped `upload_media`
    (§6.2), `find_by_slug`, `verify_url`, `download_image`, `detect_multilingual_plugin`,
    and token-scrubbed logging. Edge cases E7 (image 404 → featured media skipped),
    E8 (mismatched `meta.gtin` → `GtinMismatchError`, skip row), E11 (non-GTIN slug
    collision → `WordPressAPIError`, human intervention).
  - `lib/multilingual.py` — `MultilingualAdapter` strategy with `PolylangAdapter`
    (translation linking via `/wp-json/pll/v1/`), `NoOpAdapter`, and a `WPMLAdapter`
    stub that raises `NotImplementedError` (WPML lands in v0.2) (§4.5).
  - `lib/errors.py` — added `GtinMismatchError` (the WordPress sibling of
    `OverwriteError`) so E8 is distinguishable from E11.
  - `mcps/wordpress/` — TypeScript MCP server exposing five tools (`wp_upsert_page`,
    `wp_upload_media`, `wp_find_by_slug`, `wp_verify_url`, `wp_detect_multilingual`)
    over stdio, resolving client config from `clients.yml` (§9.2); mirrors the Python
    client's auth, retry, idempotency, and E8/E11 semantics. README documents the
    adopt-vs-fork survey (§8.2): no off-the-shelf WordPress MCP provides per-client
    credentials, GTIN-keyed idempotency, or Polylang linking, so the client forks the
    in-repo `gs1-nl` pattern.
  - Tests: `pytest`/`pytest-httpx` for the Python client and adapters (detection,
    §6.1/§6.2 idempotency, E7/E8/E11, retry, secret scrubbing) and `vitest` for the
    MCP client, config, and tool wiring. A `staging`-marked
    `tests/integration/test_wp_staging.py` holds the three live-staging DoD checks
    (Polylang detection, §6.1/§6.2 idempotency, published-page exit gate), skipped
    unless the staging env is configured.
  - CI: a Node job builds and tests the `mcps/wordpress` workspace.

- **Phase 5 — QR + templates.**
  - `lib/templates.py` — `TemplateEngine(client_id, template_config)` rendering a
    `ProductRecord` into a localised HTML fragment via Mustache/`pystache` (§4.6, §3.4).
    Client-override-first, `_default`-fallback resolution (missing template →
    `TemplateError`); the §3.4 variable vocabulary with per-language text resolution;
    edge E12 (unknown `{{extras.*}}` key → empty render + one WARNING) and E13 (data
    containing `{{`/`}}` or HTML is escaped and never re-parsed).
  - `templates/_default/product.{nl,en,fr}.html` — default product templates; and
    `templates/noviplast/product.{nl,fr}.html` — the pilot's first templates, surfacing
    the Noviplast `functional_name` extra (§6.5, §5.5).
  - `lib/qr.py` — `render_qr(uri, output_dir, gtin, formats, size_mm, ecc, dpi=300)`
    writing SVG/PNG/EPS Digital Link QR files (§4.7). Applies the uppercase-domain
    optimisation (scheme + host uppercased, path preserved) for alphanumeric-mode symbols;
    the SVG is emitted from the QR module matrix for exact millimetre sizing and
    byte-identical determinism (§6.4); PNG/EPS via Pillow.
  - `mcps/qr-render/` — self-contained TypeScript MCP exposing one tool (`qr_render`)
    over stdio (§9.3). Uses npm `qrcode` for PNG and emits SVG/EPS from the module matrix
    (npm `qrcode` has no EPS writer), mirroring `lib/qr.py`'s uppercase-domain transform
    and output shape.
  - Tests: `pytest` for the template engine (resolution order, variables, E12/E13,
    `TemplateError`) and QR renderer (§6.4 byte-determinism, formats/ordering, uppercase
    transform, ECC mapping, physical sizing); `vitest` for the MCP renderer and
    end-to-end tool wiring.
  - CI: a Node job builds and tests the `mcps/qr-render` workspace.
  - Manual print+scan gate (§8.2 exit gate): 20 mm QR scanned successfully on
    Android (2026-07-12). iOS scan still pending before the gate is fully met.

- **Phase 6 — lib, scripts, state.**
  - `lib/state.py` — per-client run state over the `State`/`StateEntry` models (§4.8):
    `load_state` (empty when absent), `save_state` (atomic write-to-temp-then-`os.replace`,
    so a crash mid-write leaves the prior `state.json` intact, never a partial one), and
    `compute_content_hash` (deterministic SHA-256 over canonical JSON of product +
    language + target URL). `diff_against_state` is deferred to Phase 7, where `run_plan`
    supplies the slug/title inputs a `PlanRow` needs.
  - `scripts/run_execute.py` — deterministic, resumable execution of a confirmed plan
    (§8.3): per `(GTIN, language)` row it renders the template → upserts the WordPress page
    → verifies the URL returns 200 → sets the GS1 resolver target via `safe_upsert`
    (GET-before-write, `overwrite=True`; §5.4) → renders the QR. One `RunOutcome` per row is
    appended to `output/{client_id}/runs/{ts}.jsonl` regardless of success; successful rows
    update `output/{client_id}/state.json`. Exit codes `0`/`1`/`2`. `--dry-run` (§5.4 Level
    B) previews intended mutations without performing them (no HTTP writes, no QR, no state).
  - Tests: `pytest` for `lib/state.py` (round-trip, content-hash determinism, `StateError`,
    and the §12 kill-mid-write atomicity check — including a SIGKILL-during-write subprocess
    test) and `scripts/run_execute.py` (happy path, §6.5 double-run idempotency, verify-failure
    error path, `--dry-run` no-mutation, `--confirmed` subset, config-error exit code) with
    the WP/GS1 clients faked. A `staging`-marked integration test drives `run_execute`
    end-to-end for one GTIN against real WordPress staging + the GS1 **production**
    environment, then re-runs to assert §6.5; skipped until that infrastructure is configured.
  - DoD note: the live end-to-end exit gate and live §6.5 check run via the staging test. The
    GS1 sandbox account has no Digital Link contract, so the run targets GS1 production (a
    disposable/pilot GTIN, protected by the `safe_upsert` guard and `--dry-run`). The other two
    Phase 6 DoD items (§6.5 idempotency, state-file kill-mid-write atomicity) are met and
    covered by passing tests.
  - **WordPress access unblocked (supersedes the earlier deferral).** This item was previously
    recorded as blocked: Application Passwords were disabled by Wordfence on production
    `www.noviplast.nl` and no staging site existed. That has since been fixed and verified —
    the `automation-bot` user authenticates against the live REST API with the **editor** role
    (`edit_posts`, `publish_posts`, `upload_files`, `edit_others_posts`, `unfiltered_html`), and
    the `noviplast` custom post type is registered and REST-exposed (`rest_base: noviplast`).
    The live `run_execute` end-to-end run for one GTIN is therefore **runnable, not blocked** —
    it simply has not been run yet, and it writes to a live WooCommerce store and the GS1
    production resolver, so it needs a deliberate go-ahead and a disposable GTIN.

- **Phase 7 — Re-run & change detection.**
  - `lib/state.py` `diff_against_state(products, state, languages, wordpress)` (§4.8, §8.2):
    per `(GTIN, language)` it builds the slug, resolver target URL, and title from the
    WordPress patterns and classifies against prior state by content hash — NEW (no entry),
    UNCHANGED (equal hash), or CHANGED. A CHANGED row carries a field-level diff of `title`
    and/or `target_url` — the two fields `StateEntry` records — in the order §10.6.2 presents
    them. Fields state does not retain are never fabricated. A language with no `product_name`
    for a product is omitted with a warning (edge E18). Takes the whole `WordPressConfig`
    rather than §4.8's bare `target_url_pattern`, which alone cannot build a `PlanRow`.
  - **`StateEntry.title`** (§2.3, new field): the page title as last written, persisted by
    `run_execute` on every successful row. Without it a CHANGED row had nothing to show —
    `slug_pattern` is GTIN-derived, so renaming a product changes the content hash without
    moving the URL, and §10.6.2's `Changes:` list rendered empty in exactly the scenario the
    phase exit gate names (*"change one product name, re-run, confirm prompt appears"*).
    `content_hash` proves *that* a product changed but, being a digest, can never say *what*.
    Optional (`str | None = None`) so state files predating the field still load; `None` means
    "not recorded" and the title row is omitted rather than guessed.
  - `scripts/run_plan.py` (§8.2): loads config/state/products, classifies with
    `diff_against_state`, writes `output/{client_id}/plan.json`, and prints
    `N new, M unchanged, K changed` to stderr. Exit `0`/`2` (no per-row error class).
  - **E19 (corrupt state file) now recovers instead of aborting**, as §7 always specified.
    `load_state` quarantines the bad file to `state.json.corrupt.{ts}` — preserved, never
    deleted, since it is the only evidence of what went wrong — logs an ERROR, and returns an
    empty state. This is safe because every write path is idempotent (§6.1–§6.5): without a
    known page id `upsert_page` still matches the live page by slug then `meta.gtin` and
    updates it in place, `safe_upsert` reads before it writes, and QR renders are
    byte-deterministic. A reset costs redundant work, not corruption. An *unreadable* file
    (permissions, I/O fault) is an environmental fault and still raises `StateError` → exit 2.
  - **The reset is surfaced, not just logged** (an addition to §7's E19, which stopped at
    "log ERROR"). A reset reclassifies every row as NEW, silently turning an incremental
    re-run into a full rewrite of live pages and resolver targets — and the operator is
    reading the chat, not stderr. So `State.reset_from_corrupt` (load-scoped,
    `Field(exclude=True)`, never persisted) carries the fact to `run_plan`, which leads its
    summary with a warning, and to the flow-orchestrator, which surfaces it **above** the
    §10.6.1 counts. The existing confirmation gate is what makes the reset safe in practice;
    it only works if the operator is told.
  - `skills/flow-orchestrator/SKILL.md` (§10.5, §10.6): presents the plan, collects
    confirmation, writes `plan.confirmed.json`, enforces the mandatory production-env gate,
    and invokes `run_execute` — with the §10.6 chat blocks embedded verbatim.
  - **Website-status control-file gate (extension beyond the spec).** A deliberate,
    user-approved addition for the pilot's *create-only* workflow: an operator-maintained
    file (`input/{client_id}/website_status.xlsx`), separate from the datasource export,
    gates which products are candidates — eligible only when already registered in GS1 and
    not yet on the website. `lib/website_status.py` loads it; `WebsiteStatusConfig` +
    `WebsiteStatusError` + a `websiteStatus` schema block wire it into `clients.yml`;
    `run_plan.py` applies the gate and reports exclusions. Consequence: in the pilot every
    planned row is NEW, so the change-detection/diff path is exercised only by tests, dormant
    at runtime until product updates occur.
  - Tests: `diff_against_state` edge cases (NEW/UNCHANGED/CHANGED; title-only, URL-only, and
    combined diffs; body-only change with no showable diff; a pre-`title` state entry omitting
    the title row; E18; multi-language; missing patterns) plus the legacy state-file load in
    `tests/lib/test_state.py`; control-file parsing and eligibility in
    `tests/lib/test_website_status.py`; E19 recovery (quarantine + reset flag, schema-violation
    files, the raise an unreadable file still gets, and the reset flag never being persisted)
    in `tests/lib/test_state.py`; `run_plan` counts, gate filtering, default path, exit-2
    paths, and the corrupt-state warning reaching stderr in `tests/scripts/test_run_plan.py`;
    title persistence in `tests/scripts/test_run_execute.py`.
  - DoD note: change classification and the §10.6 chat format are met and test-covered. The
    third item — the full re-run flow in a fresh Cowork session — **has moved to Phase 8**
    (§12), whose exit gate is the same test done properly: only `flow-orchestrator` has a
    SKILL.md today, and step 1 of the flow delegates parsing to the not-yet-written
    `gs1-export-parser`, so a Cowork test now would exercise one-fifth of the surface it is
    meant to validate.
  - The plan half now runs on **real operator data**, not a fixture: both `products.xlsx` and
    `website_status.xlsx` are in `input/noviplast/`. `parse_export` reads the 127-product GDSN
    export with zero warnings and `run_plan` gates it to 73 rows (37 nl + 36 fr; one fr row
    skipped by E18 for a missing `product_name.fr`), excluding 90 products — 61 already on the
    website, 12 not yet in GS1, 17 absent from the control file.
- **Phase 7.5 — GPC brick → category mapping.**
  - `lib/config.py` `CategoryConfig` + `schema/clients.schema.json` `$defs.categories`: a client
    `categories:` block with `terms` (the closed allowed set), `brick_category_map` (brick → term),
    and `overrides` (GTIN → term). The loader rejects any map/override value outside `terms`. This is
    client-owned, signed-off data, so it lives in config, not code.
  - `lib/categories.py`: `resolve_category` (precedence override > brick map > none; an unmapped
    brick, out-of-set term, or missing brick reports a `SourceIssue` and never guesses),
    `distinct_bricks`, `coverage_report`/`CoverageReport`, plus the operator-input half —
    `load_diy_datamodel` (columns as parameters, since the GS1 DIY sector datamodel's format is the
    operator's) and `draft_brick_map`/`BrickMapDraft`.
  - `scripts/build_brick_map.py`: read-only. Default mode prints a `categories:` review skeleton
    (every export brick present, term UNSET, annotated from the datamodel); `--check` is the coverage
    gate, exiting non-zero while any export brick is unmapped.
  - `scripts/run_plan.py`: assigns `product.category` after the website-status gate and **before**
    hashing, so a category change reclassifies the row as CHANGED. Unmapped bricks warn and leave the
    category unset; findings go to `output/{client}/data/category_issues.json`; the summary reports the
    count. No-op when the client has no `categories` config.
  - Tests: `tests/lib/test_categories.py`, `tests/lib/test_config.py`,
    `tests/scripts/test_build_brick_map.py`, `tests/scripts/test_run_plan.py`.
  - DoD note (§12): **all five items met (2026-07-18).** The operator supplied the GS1 DIY datamodel
    (`GS1 Data Source Datamodel 3.1.36.xlsx`, sheet `Bricks` / `NL Brick Title`) covering all 73
    export bricks; the client signed off the 73-brick map + one override (`10003865` Tuin
    Handgereedschap → `tuin`, with the Notenkraker `08713195003948` → `keuken`). `build_brick_map
    noviplast --check` is green and `run_plan` assigns a category to all 73 planned rows
    (`category_issues.json` empty). The signed-off map lives in the gitignored `clients.yml`; the
    reviewed source is `output/noviplast/data/categories.proposed.yml`. Open decision resolved:
    missing WordPress terms must **pre-exist** (`require_terms_exist`), enforced later at the
    not-yet-built term-assignment step.
- **Page adapter — `net_content` unit decoding.** `reference/measurement_units.json` (the GS1 DIY
  datamodel's `MeasurementUnitCode_GDSN` picklist, 129 codes → nl/en/fr) + `lib/units.py`
  (`decode_net_content`) turn the raw code the feed carries (`"5 H87"`) into words per language
  (`H87` → *Stuk* / *Piece* / *Pièce*), decoded at render time in `templates._build_context`.
  net_content stays language-agnostic on the record; the decoder is reusable by the future
  Technische-details generator. All 125 pilot products (all H87) now render words. Tests in
  `tests/lib/test_units.py` and `tests/lib/test_templates.py`.
- **Content generator.** `lib/generator.py` turns feed attributes into page copy through a
  deterministic core (cache, contract, merge) with two interchangeable producers behind one
  seam: an **in-session** producer (the `content-generator` skill writes copy in the chat,
  no API key) and a **headless API** backend (`run_generate --backend api` via `lib/llm.py`).
  Routing is per product — `verbatim` / `tighten` / `generate` — driven by attribute 1067
  (the ranked USP source, captured multivalue by `lib/gdsn.py`); `ProductRecord` gains
  `generated_tagline` and `generated_description`, `run_plan` merges the cache before
  classification, and `acf_map` is wired to the generated fields. Producers may declare a
  `generation_inference` — a claim written beyond the literal feed text — which flows to
  `generated_issues.json` for a human to verify before publishing. Spec:
  `docs/clients/noviplast-generator-spec.md`; voice: `prompts/noviplast/generation.v1.md`.
- **Phase 8 — Skills.** All six `skills/*/SKILL.md` finalised: `flow-orchestrator`,
  `content-generator`, `gs1-export-parser`, `gs1-digital-link`, `qr-render`, and
  `wordpress-product-page`. Each carries Agent-Skill YAML frontmatter (name + description with
  trigger phrases) so a Skills-aware surface can discover it. `flow-orchestrator` gained a
  two-gate review flow: generate copy → **review gate 1** → plan → **review gate 2** → execute,
  draft-first.
- **Phase 9 — Live pilot.** The first real GTIN (`08713195007717`) published live in nl+fr and
  validated end to end: WordPress pages render ACF, the GS1 production record is enabled, and
  `GET id.gs1.org/01/{gtin}` → 307 → page → 200. Scaled to **10 live GTINs**, every QR
  resolving, no manual corrections, and a printed QR confirmed scanning from a phone.
  `docs/clients/noviplast-live-log.md` is the committed audit trail of what is live (the
  machine source, `output/{client}/state.json`, is gitignored).
- **Phase 9.5 — Media.** Images and video now publish with the page. `lib/media.py` decodes
  TIFF/PNG/JPEG, flattens alpha, downscales and writes a deterministic baseline JPEG — 93 of
  127 pilot products ship multi-MB TIFF print masters that WordPress rejects outright.
  `lib/media_video.py` resolves videos from per-language folders through a client-confirmed
  name→GTIN mapping and transcodes to H.264/MP4 with ffmpeg, because the source `.mpg`/`.mpeg`
  files are MPEG-1/2 and will not play in an HTML5 `<video>`. `MediaConfig` carries the block;
  `scripts/build_video_map.py` drafts the mapping and `--check` gates coverage. Uploads are
  **content-addressed** (`{base}-{sha12}` slug), so re-runs dedupe on identical bytes without a
  meta read-back. Every media failure still degrades to a published page (E7).
- **Phase 9.8 — Operator flow validated.** `flow-orchestrator` driven end to end in a Claude
  Code session with the operator answering every gate: language select → review gate 1 →
  plan-review gate 2 → per-row diff gate → production environment confirmation → execute →
  post-execute summary. Since the pilot was exhausted (0 actionable rows), a reversible dry-run
  harness supplied one CHANGED and one NEW row; `--dry-run` wrote nothing and the harness was
  torn down with `state.json` verified byte-identical.
- **Phase 10 — Operator documentation.** Seven documents in `docs/`, each derived from the code
  at HEAD rather than from the planning documents: `setup.md` (the entry point),
  `troubleshooting.md` (all 13 `lib/errors.py` classes, the E1–E22 inventory, and the traps
  already paid for live), `gs1-nl-onboarding.md`, `wordpress-onboarding.md`,
  `data-source-export-schema.md`, `template-variables.md`, and `costs.md`. Plus
  `OPEN_DECISIONS.md`, which records identified-but-unmade decisions with evidence and a
  recommendation. `setup.md` was proven by executing it verbatim from a fresh clone in a clean
  venv — which is how the `inspect_export --help` crash below was found.
- **Data-quality report.** `python -m scripts.report_quality {client}` folds four issue files
  into one actionable worklist at `output/{client}/data-quality-report.md`, grouped by owner and
  action (blocks-publish / review / fix-in-MyGS1). `lib/quality_report.py` is a pure,
  deterministic renderer. Sections cover severity-ranked blank fields (§1b separates a blank
  title or hero image, which block publishing, from a blank `net_content`, which only degrades a
  detail line), per-market value comparison (§3b, which surfaced real content disagreements
  rather than cosmetic ones), possible wrong-language values (§3c — flags exactly one real slip
  across all 127 pilot products with no false positives), generation inferences, and free-text
  Observations captured during in-session review.
- **`scripts/run_unpublish.py`** — retract a published product: draft the WordPress pages and
  disable the GS1 record, with HELD classification so it cannot silently republish.
- **`scripts/build_brick_map.py`** — draft and check the GPC brick → category mapping.
- **`.env` is the single source of truth for credentials** (OD-1). `lib/env.py` `load_env()`
  wraps `load_dotenv(override=False)`, called from each script's `if __name__ == "__main__":`
  block — deliberately not from `main()`, which the tests call directly. See *Security* below.

### Fixed
- **`inspect_export` crashed on `--help` and on any non-workbook path.** The script takes a bare
  positional path with no argparse, so `--help` was read as a filename and reached openpyxl,
  which raises `InvalidFileException` — not an `OSError`, so the existing handler missed it and
  the script died with an unhandled traceback. Now prints usage and exits 0 for `-h`/`--help`,
  and reports "cannot read export" (exit 1) for anything unreadable.
- **Video mapping never matched.** `mapping.yml` is keyed with 13-digit GTINs while the pipeline
  carries 14-digit ones, so `VideoMap.resolve` silently returned `None` for every GTIN and no
  video was ever attached. Added `canon_gtin` (strip non-digits, `zfill(14)`) and normalise both
  sides.
- **Media idempotency, found live.** Two distinct failures made every run create duplicate
  attachments: the `content_sha256` meta was silently dropped unless registered in REST, so the
  hash was never readable; and a stale attachment sharing only the base slug squatted it and
  shadowed the match. Fixed by the content-addressed slug above.
- **`ruff format --check` drift** and **PLR0917** (new in ruff 0.16, which CI installs): ignored
  project-wide, matching the existing deliberate `# noqa: PLR0913`.

### Removed
- **Claude Cowork support.** Publishing from its cloud sandbox would have put live production
  WordPress and GS1 credentials on a remote host every wave, and its egress to the live services
  was never proven. **Claude Code is the operating surface**; Claude.ai and Claude Desktop are
  out of scope. `docs/COWORK_SETUP.md` deleted and every "Cowork-native" label re-pointed to
  "in-session (Claude Code)". The pipeline itself was already tool-agnostic.
- **§2b generated-copy dump** from the data-quality report — it reprinted the source text of
  every generated row, never shrank, and named no defect or action.

### Security
- **Production write guard.** A real `run_execute` (not `--dry-run`) against a client whose
  `gs1.environment` is `production` is **refused with exit 2** unless `--i-understand-production`
  is passed. Before this, a bare `run_execute --plan …` published live pages and registered
  permanent GS1 records with no confirmation — the review gates existed only in the
  `flow-orchestrator` skill, so any invocation outside it bypassed them entirely.
- **Credentials consolidated into `.env`** (OD-1, `docs/OPEN_DECISIONS.md`). They had been
  reaching the code from an `env` block in `~/.claude/settings.json` — residue from the removed
  Cowork experiment — which is injected into *every* command in *every* project on the machine.
  That block is now deleted and `.env` is `chmod 600`. The blast radius is the point: while the
  variable was ambient, a diagnostic command echoed a live WordPress application password in
  clear text into a chat transcript.
  **`load_env()` is called from each script's `__main__` block, never from `main()`.** Nine test
  modules call `main()` directly, so the other placement would load production credentials — and
  all four variables the staging guards gate on — into the pytest process on every plain
  `pytest` run, arming tests that write to live WordPress and the GS1 production resolver.
  `tests/lib/test_env.py` enforces both halves of the invariant with an AST check.
- **Pilot allowlist.** `media.restrict_to_mapped_gtins` hard-blocks `run_execute` from writing
  any GTIN without a client-confirmed video in every language, even when passed explicitly via
  `--plan`.
- **E21 / E22 publish holds.** `run_plan` skips any (GTIN, language) with no generated tagline,
  so a product held for blank source copy can never publish as a silently-blank page; the opt-in
  `media.require_hero_image` does the same for a blank source `image_url`. Scoped to the source
  field only — a runtime image fetch failure still degrades gracefully and publishes (E7).

### Changed
- **GS1 GET/PATCH path corrected** (confirmed against the live API): the path segment
  is the GTIN application identifier `01`, not the string `Gtin`
  (`/digitalLink/01/{gtin14}`). Using `Gtin` returned `404` for every GTIN, so `get()`
  and `set_enabled()` never worked before. Not-found is a `400` with body
  `"No valid contract found for Gtin with id: …"` (not 404) → mapped to `None`.
  `DigitalLinkRecord` gains `useGs1Elabel` / `isElabelSupported`; docs (§4.2/§4.3/§5.1/
  §9.1/§13.2) updated. MyGS1-UI Digital Link activations are visible via the API v2.
- **GS1 auth model corrected to OAuth2 client-credentials** (empirically confirmed
  in Phase 2, replacing the spec's assumed static token / `auth_scheme` switch).
  Both clients now mint a short-lived JWT from `client_id`/`client_secret` via
  `POST /authorization/token`, cache it until near expiry, refresh on `401`, and
  send it as a Bearer token. `clients.yml`, its schema, and `.env.example` now
  carry per-environment `client_id_env_*`/`client_secret_env_*` and
  `account_number_*` (the account differs per environment). Docs updated
  (PROJECT_HANDOVER §4.1–4.2, IMPLEMENTATION_SPEC §4.3, §13.2).
- **Specification corrected to match what was built.** Phase 10 audited the documents against
  the modules and found three divergences, each verified in the code first:
  - **`scripts/verify_run.py` does not exist and is not planned.** §8.4 specified it;
    post-run verification lives inside `run_execute` via `wp_client.verify_url`. §8.4 is marked
    superseded and a new **§8.4a** documents the five scripts that were built instead
    (`run_generate`, `run_unpublish`, `build_brick_map`, `build_video_map`, `report_quality`).
  - **`WPMLAdapter` is implemented and in production.** §4.5 called it a stub raising
    `NotImplementedError`, slated for v0.2, and `PREPARATION.md` §3.18 said to install Polylang.
    The pilot has been publishing live nl+fr pages through WPML plus a site-side helper route.
    Polylang must **not** be installed on the pilot site.
  - **`lib/errors.py` has 13 exception classes**, not the 8 listed in §4.1.
- **`README.md`** no longer describes the project as "Phase 1 (repository skeleton). Not yet
  functional"; it now carries a quickstart, a status block, and a safety section.
- **`product_name` maps to GDSN attribute 3301**, not 3318 (which carries material and colour
  noise) or 3297 (an internal logistics string) — verified against the live site.
- **`market_priority` replaced the 1:1 `market_language` mapping**, which cost coverage and
  could not resolve products whose markets disagree. Every market row carries every language, so
  the parser walks the priority order and takes the first non-blank value per
  product/field/language.

## [0.0.1] - 2026-07-09

### Added
- Repository skeleton per `docs/PROJECT_HANDOVER.md` §7: source tree (`lib/`,
  `scripts/`, `mcps/`, `skills/`, `templates/`, `tests/`).
- MIT `LICENSE`, baseline `README.md`, `CONTRIBUTING.md`, and this changelog.
- `.gitignore` covering secrets, per-client config, and build artifacts.
- `clients.example.yml` and `.env.example` configuration templates.
- `schema/clients.schema.json` — JSON Schema for `clients.yml`.
- `pyproject.toml` (Python tooling: ruff, mypy, pytest) and root `package.json`
  (npm workspaces over `mcps/*`).
- GitHub Actions CI: `ruff check`, `ruff format --check`, `mypy --strict lib`,
  and `pytest` on push and pull request.

[Unreleased]: https://github.com/NextGenDataLead/gs1-product-link/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/NextGenDataLead/gs1-product-link/releases/tag/v0.1.0
<!-- 0.0.1 was never tagged; it links to the commit that set the version. -->
[0.0.1]: https://github.com/NextGenDataLead/gs1-product-link/commit/728ae92
