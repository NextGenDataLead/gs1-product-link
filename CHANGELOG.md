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
