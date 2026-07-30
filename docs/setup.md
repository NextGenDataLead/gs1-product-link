# Setup

From a clean machine to a validated dry run, then onboarding a client of your own.

Every command here is verified against the code at HEAD, not against a plan. If a command in this
file does not do what it says, the file is wrong — please fix it.

- New to the project? Read this top to bottom.
- Onboarding a **second** client? Skip to [Onboard a client](#onboard-a-client).
- Something broke? [`troubleshooting.md`](troubleshooting.md).

## What you are setting up

The tool turns a GS1 Data Source export into (a) WordPress product pages, one per
`(GTIN, language)`, and (b) GS1 Digital Link resolver entries whose QR codes point at those pages.
There is **no server and nothing to host** — you run scripts locally, against your own WordPress
site and your own GS1 account, with your own credentials.

## Prerequisites

| | Requirement | Notes |
|---|---|---|
| Python | **3.11 or newer** | `requires-python = ">=3.11"` in `pyproject.toml`. CI pins 3.11; the suite also passes on 3.14. |
| Node.js | 20 or newer | Only needed to build the MCP servers in `mcps/`. The Python pipeline does not need it. |
| Git | any recent | |
| `ffmpeg` | optional | Only if a client sets `media.video_transcode: true`. |
| GS1 | a Data Source contract **and** a Digital Link contract | See [`gs1-nl-onboarding.md`](gs1-nl-onboarding.md). Without the Digital Link contract every write fails `400 21011`. |
| WordPress | 5.6+, REST reachable over HTTPS | See [`wordpress-onboarding.md`](wordpress-onboarding.md). |

## 1. Install

```bash
git clone https://github.com/NextGenDataLead/gs1-product-link.git
cd gs1-product-link

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
```

## 2. Verify the install

These are the four commands CI runs (`.github/workflows/ci.yml`). All four must pass before you
trust anything else.

```bash
ruff check                 # lint
ruff format --check        # formatting
mypy --strict lib          # type-check
pytest                     # test suite
```

Expected, on a clean checkout:

```
All checks passed!
91 files already formatted
Success: no issues found in 21 source files
519 passed, 2 skipped, 5 deselected
```

**Why a bare `pytest` is safe.** `pyproject.toml` sets `addopts = "-m 'not staging'"`. The 5
deselected tests are the staging integration tests — they write to a **live** WordPress site and the
**GS1 production** resolver. They are deselected by default deliberately, because relying on their
`skipif` env-var guard was not enough: a shell that had sourced `.env` satisfied that guard, and a
bare `pytest` then hit production. Do not run `pytest -m staging` unless you have read
`.env.example`'s staging block in full and understand that a GS1 record **cannot be deleted**.

The 2 skipped tests need optional local tooling and skipping them is normal.

## 3. Configure

Two files, both gitignored, neither ever committed:

```bash
cp clients.example.yml clients.yml   # per-client, non-secret configuration
cp .env.example .env                 # secrets only
```

`clients.example.yml` is a working example that **loads unedited** — useful for checking your install
before you have real credentials:

```bash
python -c "from lib.config import load_clients; print(sorted(load_clients('clients.yml')))"
# -> ['noviplast']
```

### Secrets

`.env` holds nothing but secrets, and `clients.yml` holds only the **names** of the environment
variables — never a value. `.env.example` documents every variable; read it rather than copying
names from here, and note two traps it calls out:

- **Quote the WordPress application password.** WordPress issues it as six space-separated groups.
  Unquoted, `source .env` stops at the first space and the variable silently loads empty.
- **Nothing auto-loads `.env`.** Load it yourself when a command needs credentials:
  ```bash
  set -a; source .env; set +a
  ```

The parse, plan, report, and map-building steps below need **no credentials at all** — only
`run_execute` and `run_unpublish` talk to WordPress and GS1.

### Client configuration

`clients.yml` is validated against `schema/clients.schema.json` and parsed into the Pydantic models
in `lib/config.py` — that module is the authoritative field list, including defaults. The blocks:

| Block | Purpose | Reference |
|---|---|---|
| `export` | Where the export is and how to read it (`format: flat \| gdsn`, `column_map` / `gdsn_map`, `market_priority`) | [`data-source-export-schema.md`](data-source-export-schema.md) |
| `wordpress` | Site URL, credentials env var, `post_type`, `languages`, `acf_map`, `multilingual_plugin` | [`wordpress-onboarding.md`](wordpress-onboarding.md) |
| `gs1` | Account numbers, credential env vars, `environment: test \| production` | [`gs1-nl-onboarding.md`](gs1-nl-onboarding.md) |
| `template` | HTML template overrides (optional — ACF clients don't need it) | [`template-variables.md`](template-variables.md) |
| `qr` | Formats, `size_mm`, error correction, `dpi` | |
| `categories` | GPC brick → site category, plus per-GTIN overrides | |
| `generator` | Optional LLM copy generation | [`costs.md`](costs.md) |
| `media` | Images and video, field names, write shape | [`wordpress-onboarding.md`](wordpress-onboarding.md) |
| `website_status` | Operator control file marking which products are already on the site | |

## 4. The pipeline

Nine scripts, all invoked as modules. Every one takes the `client_id` — the key under `clients:` in
`clients.yml` — as its first positional argument, except `inspect_export`, which takes a file path.

Exit codes are uniform: **0** success, **1** errors in the work itself, **2** configuration or usage
error. Run any of them with `--help` for the authoritative flag list — `inspect_export` takes a bare
path and no flags, so its `--help` just prints its one-line usage.

### Read-only steps — safe to run any time, no credentials

```bash
# Describe an export's sheets, attributes and sample values; suggest a gdsn_map.
python -m scripts.inspect_export input/{client_id}/products.xlsx

# Export -> output/{client_id}/data/products.json   (--dry-run validates and writes nothing)
python -m scripts.parse_export {client_id} [--dry-run] [--output PATH]

# Draft or gate the GPC brick -> category map. --check exits 1 if any brick is unmapped.
python -m scripts.build_brick_map {client_id} [--datamodel FILE.xlsx] [--sheet S] [--check]

# Draft or gate the video filename -> GTIN map. --check exits 1 if any file is unmapped.
python -m scripts.build_video_map {client_id} [--check]

# Consolidated data-quality report -> output/{client_id}/data-quality-report.md
python -m scripts.report_quality {client_id} [--out PATH]

# Classify every (GTIN, language) as new / changed / unchanged -> output/{client_id}/plan.json
python -m scripts.run_plan {client_id} [--products PATH]
```

Run `report_quality` after `parse_export`, `run_plan`, or `build_video_map` — it renders whatever
those steps last produced, and it is how you find source-data problems to fix in MyGS1 rather than
papering over them in code.

### Copy generation — optional

Only for clients with `generator.enabled: true`. Two backends share one cache and contract:

```bash
python -m scripts.run_generate {client_id} --emit      # write pending requests (default)
python -m scripts.run_generate {client_id} --ingest    # read a session's results into the cache
python -m scripts.run_generate {client_id} --backend api   # fill the cache directly via the API
```

`--emit` / `--ingest` is the **in-session** path: Claude writes the copy in your session and needs
**no API key**. `--backend api` is the headless path and is the only step that costs money — see
[`costs.md`](costs.md). After ingesting, re-run `run_plan` so the generated copy merges into the plan.

### Writing steps — these mutate live systems

```bash
# Preview everything, write nothing. Always do this first.
python -m scripts.run_execute {client_id} --plan output/{client_id}/plan.json --dry-run

# Real run.
python -m scripts.run_execute {client_id} --plan PATH [--revive] [--i-understand-production]
python -m scripts.run_execute {client_id} --confirmed PATH   # a reviewed ConfirmedPlan

# Take a product down: retract its Digital Link and draft its pages.
python -m scripts.run_unpublish {client_id} --gtin GTIN [--gtin GTIN ...] [--dry-run]
```

`--plan` treats every row as confirmed; `--confirmed` consumes a `ConfirmedPlan` that has been
through review. They are mutually exclusive and one is required.

> **The production guard.** A real run — not `--dry-run` — against a client whose
> `gs1.environment` is `production` is **refused with exit 2** unless you pass
> `--i-understand-production`. This exists so a bare `--plan` cannot publish live pages and create
> permanent GS1 records. `--dry-run` never needs it.

Writes are idempotent: re-running the same input converges on the same state rather than duplicating
pages, records or media.

### Driving it from chat instead

The six skills in `skills/` wrap these scripts with operator gates — language selection, a copy
review gate, a plan review gate, a per-row diff gate, and a mandatory production
environment-confirmation gate. In Claude Code, say *"run for {client_id}"* to load
`flow-orchestrator`. Prefer this for real runs: the gates are the reason nothing has been published
by accident.

## Onboard a client

Read-only until the final step.

1. **Add the client** to `clients.yml`. Copy the example block and change `client_id`,
   `display_name`, `export.path`, and the `wordpress` / `gs1` blocks. Add the credential env vars to
   `.env` — names in `clients.yml`, values only in `.env`.

2. **Drop the export** at `input/{client_id}/products.xlsx`. `input/` and `output/` are gitignored,
   so client data never enters the repository. Create the directory if it does not exist.

3. **Inspect it** and let the tool draft your mapping:
   ```bash
   python -m scripts.inspect_export input/{client_id}/products.xlsx
   ```
   For a GDSN datapool export this prints each sheet's attributes with their GDSN attribute ids and
   sample values, plus a suggested `export` block. Refine it into `gdsn_map` (or `column_map` for a
   flat export) — details in [`data-source-export-schema.md`](data-source-export-schema.md).

4. **Iterate until the parse is clean:**
   ```bash
   python -m scripts.parse_export {client_id} --dry-run
   ```
   Repeat until there are no warnings on required fields. `brand` and `product_name` are mandatory.
   Then drop `--dry-run` to write `products.json`.

5. **Review data quality** — `python -m scripts.report_quality {client_id}`. Fix what belongs in
   MyGS1 at the source. Blank or wrong source data must not be invented downstream.

6. **Map the page fields.** Set `wordpress.acf_map` (or a template) so every page slot has a source.
   See [`template-variables.md`](template-variables.md).

7. **Plan:**
   ```bash
   python -m scripts.run_plan {client_id}
   ```
   Confirm the counts are what you expect before going further.

8. **Dry-run the write:**
   ```bash
   python -m scripts.run_execute {client_id} --plan output/{client_id}/plan.json --dry-run
   ```

9. **Publish a small first wave** — two or three GTINs, not the whole batch. Keep
   `gs1.environment: test` until a page renders correctly. Then verify each one properly:
   - Fetch the page HTML and confirm the content is actually **rendered**. A `200` proves nothing —
     the ACF write path fails silently.
   - Check resolution with **GET**: `curl -sS -o /dev/null -w '%{http_code}' -L
     https://id.gs1.org/01/{gtin14}` → 307 → your page → 200. The resolver **404s to HEAD**.

10. **Scale up** once a wave is verified, and record what went live.

## Safety rules

- Dry-run before every real run.
- A live production run requires `--i-understand-production`. That prompt is the guard working, not
  an obstacle to route around.
- Never `pytest -m staging` casually — it writes to live WordPress and GS1 production.
- A GS1 Digital Link record **cannot be deleted**. `run_unpublish` deactivates it; the disabled
  record stays on the account permanently. Never point a smoke test at a real product's GTIN.
- `clients.yml`, `.env`, `input/`, `output/` are gitignored. Keep it that way, and keep secrets out
  of `clients.yml` — it holds env var *names*.
- Verify rendered HTML, never just a status code.

## Next

- [`troubleshooting.md`](troubleshooting.md) — every error type, and the traps already paid for.
- [`gs1-nl-onboarding.md`](gs1-nl-onboarding.md) · [`wordpress-onboarding.md`](wordpress-onboarding.md) — the two external systems.
- [`data-source-export-schema.md`](data-source-export-schema.md) · [`template-variables.md`](template-variables.md) — data in, page out.
- [`costs.md`](costs.md) — what running this costs.
- [`architecture.svg`](architecture.svg) — end-to-end diagram.
- [`IMPLEMENTATION_SPEC.md`](IMPLEMENTATION_SPEC.md) §8 — full script contracts.
