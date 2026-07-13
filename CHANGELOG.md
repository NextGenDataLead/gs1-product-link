# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
  - DoD note: the live end-to-end exit gate and live §6.5 check run via the staging test and
    are gated on WordPress staging being provisioned; the GS1 sandbox account has no Digital
    Link contract, so the run targets GS1 production (a disposable/pilot GTIN, protected by the
    `safe_upsert` guard and `--dry-run`). **Deferred this session:** production WordPress
    (`www.noviplast.nl`, a live WooCommerce store) has Application Passwords disabled by
    Wordfence, and no staging site exists yet — so the one live-run DoD item (`run_execute`
    end-to-end for 1 GTIN) is pending WP access. The other two Phase 6 DoD items (§6.5
    idempotency, state-file kill-mid-write atomicity) are met and covered by passing tests.

- **Phase 7 — Re-run & change detection.**
  - `lib/state.py` `diff_against_state(products, state, languages, wordpress)` (§4.8, §8.2):
    per `(GTIN, language)` it builds the slug, resolver target URL, and title from the
    WordPress patterns and classifies against prior state by content hash — NEW (no entry),
    UNCHANGED (equal hash), or CHANGED. A CHANGED row carries a best-effort `target_url`
    diff (old `wp_url` → new) when the URL moved; `StateEntry` stores no prior product
    fields, so a title before/after is never fabricated. A language with no `product_name`
    for a product is omitted with a warning (edge E18). Takes the whole `WordPressConfig`
    rather than §4.8's bare `target_url_pattern`, which alone cannot build a `PlanRow`.
  - `scripts/run_plan.py` (§8.2): loads config/state/products, classifies with
    `diff_against_state`, writes `output/{client_id}/plan.json`, and prints
    `N new, M unchanged, K changed` to stderr. Exit `0`/`2` (no per-row error class).
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
  - Tests: `diff_against_state` edge cases (NEW/UNCHANGED/CHANGED, target_url diff, E18,
    multi-language, missing patterns) in `tests/lib/test_state.py`; control-file parsing and
    eligibility in `tests/lib/test_website_status.py`; `run_plan` counts, gate filtering,
    default path, and exit-2 paths in `tests/scripts/test_run_plan.py`.
  - DoD note: change classification and the §10.6 chat format are met and test-covered. The
    full re-run flow in a fresh Cowork session (DoD item 3) and the true end-to-end pilot run
    require the operator's `website_status.xlsx`; verified against a hand-built fixture in the
    interim.

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

[Unreleased]: https://github.com/NextGenDataLead/gs1-product-link/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/NextGenDataLead/gs1-product-link/releases/tag/v0.0.1
