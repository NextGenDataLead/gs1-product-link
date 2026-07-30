# Setup

From a clean machine to a validated dry run, then onboarding a client of your own.

**This tool is operated from Claude Code** — see [How you run this](#how-you-run-this-claude-code-not-raw-python) before anything else, because it changes how you should read the rest of this file.

Every command here is verified against the code at HEAD, not against a plan. If a command in this file does not do what it says, the file is wrong — please fix it.

- New to the project? Read this top to bottom.
- Onboarding a **second** client? Skip to [Onboard a client](#onboard-a-client).
- Just want to publish a wave? [Running it](#4-running-it).
- Something broke? [`troubleshooting.md`](troubleshooting.md).

## What you are setting up

The tool turns a GS1 Data Source export into (a) WordPress product pages, one per `(GTIN, language)`, and (b) GS1 Digital Link resolver entries whose QR codes point at those pages. There is **no server and nothing to host** — everything runs locally, against your own WordPress site and your own GS1 account, with your own credentials.

## How you run this: Claude Code, not raw Python

> **This tool is operated from Claude Code. That is a deliberate decision, not a preference.**
>
> You tell Claude Code what you want — *"run for noviplast"* — and it loads the `flow-orchestrator` skill, which walks you through the operator gates and invokes the Python scripts for you. **You are not expected to type the script commands yourself.**
>
> **Claude.ai, Claude Desktop, and Claude Cowork are explicitly out of scope.** Cowork was evaluated and removed: it executes in a remote cloud sandbox, which would mean handing production WordPress and GS1 credentials to an environment outside your control, and its network egress to `www.noviplast.nl` and the GS1 API was never proven. Claude Code runs on your machine with your credentials staying on it.

So why does this document list Python commands at all? Three reasons, and none of them is "type these during a normal run":

1. **Verifying the install** — §1 and §2 below are genuinely something you run once, by hand.
2. **Knowing what Claude Code is doing on your behalf** — the gates it presents map onto these commands. When something fails, [`troubleshooting.md`](troubleshooting.md) talks about them by name.
3. **Onboarding a new client**, where the read-only inspect/parse loop is iterative and hands-on.

For a real publishing run, **drive it from chat** — see [Running it](#4-running-it).

## Prerequisites

| | Requirement | Notes |
|---|---|---|
| **Claude Code** | required | The operating surface. See the note above. |
| Python | **3.11 or newer** | `requires-python = ">=3.11"` in `pyproject.toml`. CI pins 3.11; the suite also passes on 3.14. |
| Node.js | 20 or newer | Only needed to build the MCP servers in `mcps/`. The Python pipeline does not need it. |
| Git | any recent | |
| `ffmpeg` | needed for video | See [Why media gets converted](#why-media-gets-converted). Only consulted when a client sets `media.video_transcode: true`, but the pilot needs it. |
| GS1 | a Data Source contract **and** a Digital Link contract | See [`gs1-nl-onboarding.md`](gs1-nl-onboarding.md). Without the Digital Link contract every write fails `400 21011`. |
| WordPress | 5.6+, REST reachable over HTTPS | See [`wordpress-onboarding.md`](wordpress-onboarding.md). |

### Why media gets converted

Two different problems, two different fixes — worth knowing because both are silent if you skip them.

**Images — WordPress rejects the source files.** GDSN feeds carry **print masters**: in the pilot, 92% were `image/tiff` and many ran 10–45 MB at 3200×3200. WordPress will not accept those. So `lib/media.convert_image_for_web` converts **every** image — TIFF, PNG, and already-JPEG alike — to a baseline web JPEG capped at `media.image_max_dim` (default 1600 px). Converting uniformly rather than only when needed also makes the output byte-deterministic, so re-runs reuse the existing attachment instead of piling up duplicates. **No extra tooling needed** — this is Pillow, already a dependency.

**Video — the browser won't play the source files.** The operator's video folders hold `.mpg` / `.mpeg`, which are **MPEG-1/2**. WordPress will generally *store* those, but an HTML5 `<video>` element **cannot play them**, so the page publishes with a video that silently does nothing. That is what `media.video_transcode: true` fixes: `lib/media_video.prepare_video` shells out to **ffmpeg** to produce an H.264/AAC MP4 with `faststart` for web streaming and metadata stripped.

**This is the one step with an external binary dependency.** If `ffmpeg` is missing or fails, the transcode returns `None`, the video is skipped with a warning, and the page still publishes — so a missing `ffmpeg` costs you the video, not the run. Install it (`brew install ffmpeg`) before a wave that includes video, or override the path with `media.ffmpeg_bin`.

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

These are the four commands CI runs (`.github/workflows/ci.yml`). All four must pass before you trust anything else.

```bash
ruff check                 # lint
ruff format --check        # formatting
mypy --strict lib          # type-check
pytest                     # test suite
```

Expected, on a clean checkout:

```
All checks passed!
98 files already formatted
Success: no issues found in 21 source files
522 passed, 2 skipped, 5 deselected
```

**Why a bare `pytest` is safe.** `pyproject.toml` sets `addopts = "-m 'not staging'"`. The 5 deselected tests are the staging integration tests — they write to a **live** WordPress site and the **GS1 production** resolver. They are deselected by default deliberately, because relying on their `skipif` env-var guard was not enough: a shell that had sourced `.env` satisfied that guard, and a bare `pytest` then hit production. Do not run `pytest -m staging` unless you have read `.env.example`'s staging block in full and understand that a GS1 record **cannot be deleted**.

The 2 skipped tests need optional local tooling and skipping them is normal.

## 3. Configure

Two files, both gitignored, neither ever committed:

```bash
cp clients.example.yml clients.yml   # per-client, non-secret configuration
cp .env.example .env                 # secrets only
```

**`clients.example.yml` is a template to replace, not a config to adopt.** It ships with one worked example client (`noviplast:`) because an empty skeleton teaches nothing — the example shows what a fully-tuned client looks like, `gdsn_map` and `acf_map` and all. When you onboard your own client you **replace that block**, you do not publish alongside it.

It is nonetheless useful exactly once, before you have any real credentials, as an **install smoke test** — it is a known-good file, so if the loader parses it your install is sound and any later failure is your config, not your environment:

```bash
python -c "from lib.config import load_clients; print(sorted(load_clients('clients.yml')))"
# -> ['noviplast']      # proves the loader works. It does NOT mean you are set up for Noviplast.
```

Delete or replace the `noviplast:` block before you configure your own client. Nothing will ever act on a client you do not name on the command line, but leaving a stale example in your live `clients.yml` invites confusion later.

### Secrets

`clients.yml` holds only the **names** of environment variables — never a value. The values live in **`.env` at the repository root, which is the single source of truth** (decided in [`OPEN_DECISIONS.md` → OD-1](OPEN_DECISIONS.md#od-1--where-credentials-live-claude-code-settingsjson-vs-env)). `.env.example` documents every variable; copy it, fill it in, and keep it `chmod 600`. It is gitignored.

Each script loads it for you. `python -m scripts.<name>` calls `load_env()` (`lib/env.py`) at process start, which reads `.env` with `override=False` — so a variable already exported in your shell still wins, and CI, which has no `.env`, is unaffected:

```bash
python -m scripts.run_plan {client_id}      # .env is loaded automatically
```

> **`.env` is plain text.** Keep it `chmod 600`, out of any shared backup, and rotate anything that
> leaks. A WordPress application password is revoked and reissued in seconds from
> Users → Application Passwords.

Two things this deliberately does **not** do:

- **The test suite never loads `.env`.** `load_env()` is called from each script's `if __name__ == "__main__":` block, not from `main()` — and the nine test modules under `tests/scripts/` call `main()` directly. `.env` carries all four variables the staging guards gate on, so loading it anywhere in the test path would arm tests that write to the live WordPress site and the GS1 production resolver. Running those tests stays a deliberate act: `set -a; source .env; set +a && pytest -m staging`.
- **It is not imported by `lib/`.** A library must not have import side effects, so nothing is loaded merely by importing the package.

One trap survives: **quote the WordPress application password.** WordPress issues it as six space-separated groups. `python-dotenv` parses the unquoted value correctly, but `source .env` — which you still need for the staging tests — stops at the first space and loads the variable empty, producing a baffling `401` with a password you know is correct. Keep the quotes.

Note also that **environment variables do not survive between separate Claude Code tool calls**, so any manual `source .env` must be joined to the command that needs it with `&&`, in one call.

**Most steps need no credentials at all.** Parse, plan, report, and the map builders are entirely local; only `run_execute` and `run_unpublish` talk to WordPress and GS1. You can get a long way before credentials matter.

### Client configuration

`clients.yml` is validated against `schema/clients.schema.json` and parsed into the Pydantic models in `lib/config.py` — that module is the authoritative field list, including defaults. The blocks:

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

## 4. Running it

**Say what you want in Claude Code:**

```
run for {client_id}
```

That loads the `flow-orchestrator` skill, which drives the whole pipeline and stops at each operator gate: language selection → the generated-copy review gate → the plan review gate → a per-row diff gate for changed rows → a **mandatory production environment-confirmation gate** → execute → progress → post-run summary → retry. Nothing proceeds without your answer, and the skill passes `--i-understand-production` only *after* you confirm at that gate.

**Use this for every real run.** Those gates are the reason nothing has been published by accident, and they exist only on this path — invoking the scripts directly bypasses all of them. Other useful phrasings: *"parse the export for {client_id}"*, *"generate copy for {client_id}"*, *"create product pages for {client_id}"*, *"render QR for {client_id}"*, *"update the Digital Link for {client_id}"* — one per skill in `skills/`.

### What it runs underneath

You do not type these during a normal run. They are here so you can recognise what Claude Code is doing, follow [`troubleshooting.md`](troubleshooting.md) when a step fails, and work the iterative read-only loop when onboarding a new client.

Nine scripts, all invoked as modules. Every one takes the `client_id` — the key under `clients:` in `clients.yml` — as its first positional argument, except `inspect_export`, which takes a file path.

Exit codes are uniform: **0** success, **1** errors in the work itself, **2** configuration or usage error. Run any of them with `--help` for the authoritative flag list — `inspect_export` takes a bare path and no flags, so its `--help` just prints its one-line usage.

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

Run `report_quality` after `parse_export`, `run_plan`, or `build_video_map` — it renders whatever those steps last produced, and it is how you find source-data problems to fix in MyGS1 rather than papering over them in code.

### Copy generation — optional

Only for clients with `generator.enabled: true`. Two backends share one cache and contract:

```bash
python -m scripts.run_generate {client_id} --emit      # write pending requests (default)
python -m scripts.run_generate {client_id} --ingest    # read a session's results into the cache
python -m scripts.run_generate {client_id} --backend api   # fill the cache directly via the API
```

`--emit` / `--ingest` is the **in-session** path: Claude writes the copy in your session and needs **no API key**. `--backend api` is the headless path and is the only step that costs money — see [`costs.md`](costs.md). After ingesting, re-run `run_plan` so the generated copy merges into the plan.

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

> **The production guard.** A real run — not `--dry-run` — against a client whose `gs1.environment` is `production` is **refused with exit 2** unless you pass `--i-understand-production`. This exists so a bare `--plan` cannot publish live pages and create permanent GS1 records. `--dry-run` never needs it.

Writes are idempotent: re-running the same input converges on the same state rather than duplicating pages, records or media.

> **If you find yourself typing `run_execute` by hand, stop and ask why.** The direct invocation exists for tests, recovery, and the odd surgical re-run — not for publishing. A normal wave goes through the chat flow above.

## Onboard a client

Read-only until the final step. This is the one workflow where working hands-on with the scripts is expected: mapping an unfamiliar export is iterative, and you want to see each result before deciding the next change.

1. **Add the client** to `clients.yml`. Copy the example block and change `client_id`, `display_name`, `export.path`, and the `wordpress` / `gs1` blocks — then remove the leftover `noviplast:` example so your live config describes only your own clients. Add the credential env vars to `.env` (see [Secrets](#secrets)); `clients.yml` gets the **names** only.

2. **Drop the export** at `input/{client_id}/products.xlsx`. `input/` and `output/` are gitignored, so client data never enters the repository. Create the directory if it does not exist.

3. **Inspect it** and let the tool draft your mapping:
   ```bash
   python -m scripts.inspect_export input/{client_id}/products.xlsx
   ```
   For a GDSN datapool export this prints each sheet's attributes with their GDSN attribute ids and sample values, plus a suggested `export` block. Refine it into `gdsn_map` (or `column_map` for a flat export) — details in [`data-source-export-schema.md`](data-source-export-schema.md).

4. **Iterate until the parse is clean:**
   ```bash
   python -m scripts.parse_export {client_id} --dry-run
   ```
   Repeat until there are no warnings on required fields. `brand` and `product_name` are mandatory. Then drop `--dry-run` to write `products.json`.

5. **Review data quality** — `python -m scripts.report_quality {client_id}`. Fix what belongs in MyGS1 at the source. Blank or wrong source data must not be invented downstream.

6. **Map the page fields.** Set `wordpress.acf_map` (or a template) so every page slot has a source. See [`template-variables.md`](template-variables.md).

7. **Plan:**
   ```bash
   python -m scripts.run_plan {client_id}
   ```
   Confirm the counts are what you expect before going further.

8. **Dry-run the write:**
   ```bash
   python -m scripts.run_execute {client_id} --plan output/{client_id}/plan.json --dry-run
   ```

9. **Publish a small first wave — from chat, not from the command line.** Say *"run for {client_id}"* and take the gates one at a time. Two or three GTINs, not the whole batch, and keep `gs1.environment: test` until a page renders correctly. Then verify each one properly:
   - Fetch the page HTML and confirm the content is actually **rendered**. A `200` proves nothing — the ACF write path fails silently.
   - Check resolution with **GET**: `curl -sS -o /dev/null -w '%{http_code}' -L https://id.gs1.org/01/{gtin14}` → 307 → your page → 200. The resolver **404s to HEAD**.

10. **Scale up** once a wave is verified, and record what went live.

## Safety rules

- **Publish through Claude Code's gated flow**, not by invoking `run_execute` yourself. The gates are the safety mechanism; the scripts have only the production guard.
- Dry-run before every real run.
- A live production run requires `--i-understand-production`. That prompt is the guard working, not an obstacle to route around.
- Never `pytest -m staging` casually — it writes to live WordPress and GS1 production.
- A GS1 Digital Link record **cannot be deleted**. `run_unpublish` deactivates it; the disabled record stays on the account permanently. Never point a smoke test at a real product's GTIN.
- `clients.yml`, `.env`, `input/`, `output/` are gitignored. Keep it that way, and keep secrets out of `clients.yml` — it holds env var *names*. **The repository is public**, so anything committed is world-readable permanently.
- Keep credentials in `.env` and nowhere else. A Claude Code `settings.json` `env` block also works, but it is machine-wide — those secrets end up in the environment of *every* command in *every* project, which is how a password once got echoed into a chat transcript. Rotate anything that leaks; a WordPress application password is revoked and reissued in seconds from Users → Application Passwords.
- Verify rendered HTML, never just a status code.

## Next

- [`troubleshooting.md`](troubleshooting.md) — every error type, and the traps already paid for.
- [`gs1-nl-onboarding.md`](gs1-nl-onboarding.md) · [`wordpress-onboarding.md`](wordpress-onboarding.md) — the two external systems.
- [`data-source-export-schema.md`](data-source-export-schema.md) · [`template-variables.md`](template-variables.md) — data in, page out.
- [`costs.md`](costs.md) — what running this costs.
- [`architecture.svg`](architecture.svg) — end-to-end diagram.
- [`IMPLEMENTATION_SPEC.md`](IMPLEMENTATION_SPEC.md) §8 — full script contracts.
