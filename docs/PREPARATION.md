# Operator Preparation Checklist

**Purpose:** Complete these before starting Phase 1 with Claude Code. This checklist consolidates every "get ready" action that lives scattered across `PROJECT_HANDOVER.md` and `IMPLEMENTATION_SPEC.md`, presented in the order you actually do them.

**Audience:** You — the operator (currently: MDP, working for Noviplast as pilot).

**Version:** 0.3 — updated for GS1 NL Digital Link API v2

---

## How to use this document

Work top to bottom. Each item has:
- **What** — the action
- **Where** — file/env var/system to touch
- **Verify** — how you know it's done
- **Blocks** — which project phase this item blocks if skipped

If a step blocks a specific phase, you can defer it until that phase — but it's easier to bulk-complete the same category (all GS1 items together, all WP items together) so you're not context-switching later.

**Source-of-truth note:** the canonical location for all specification documents is the Obsidian vault at `10_Clients/MDP/Projects/Noviplast_2D/Project/`. Every Claude Code session should start by pulling the relevant notes from there.

---

## Part 1: One-time operator setup

Do this once, regardless of how many clients you eventually onboard.

### 1.1 Local machine

- **What:** macOS or Linux machine with terminal access.
- **Verify:** you have a shell, you can `cd` around.
- **Blocks:** all phases.

### 1.2 Python 3.11 or newer

- **What:** install via `pyenv`, `uv`, or homebrew. Not the system Python.
- **Verify:** `python3.11 --version` returns 3.11.x or higher.
- **Blocks:** all phases involving Python code.

### 1.3 Node.js 20 or newer

- **What:** install via `nvm`, `fnm`, `mise`, or homebrew.
- **Verify:** `node --version` returns v20.x or higher.
- **Blocks:** Phase 2 (GS1 MCP), Phase 4 (WP MCP), Phase 5 (QR MCP).

### 1.4 Git installed and configured

- **What:** Git with your name and email set.
- **Verify:** `git config user.name` and `git config user.email` return values.
- **Blocks:** Phase 1 (repo skeleton) and everything after.

### 1.5 Editor with markdown preview

- **What:** VS Code, Cursor, or similar. Cursor is a natural fit given Claude Code integration.
- **Verify:** you can open a markdown file and see rendered markdown.
- **Blocks:** nothing hard, but working blind through the specs is painful.

### 1.6 Claude Code installed and authenticated

- **What:** Claude Code CLI installed, authenticated with your Anthropic account, working in a terminal.
- **Verify:** `claude` command opens a session; a test message returns a response.
- **Blocks:** all phases (this is how you'll build).

### 1.7 GitHub account with permission to create repos

- **What:** GitHub account, on a plan that lets you make repos (any tier works for public repos).
- **Verify:** you can create a new empty test repo, then delete it.
- **Blocks:** Phase 1 (repo skeleton), Phase 11 (release).

### 1.8 Obsidian vault reachable

- **What:** confirm the vault containing this note is available on this machine.
- **Verify:** you can navigate to `10_Clients/MDP/Projects/Noviplast_2D/Project/` and see the six documents.
- **Blocks:** starting Claude Code sessions with the canonical docs at hand.

---

## Part 2: One-time project setup

### 2.1 License decision

- **What:** decide MIT (recommended) or another OSI-approved license.
- **Where:** later becomes `LICENSE` in the repo.
- **Verify:** decision written down.
- **Blocks:** Phase 1.

### 2.2 GitHub repo name

- **What:** pick a repo name. Recommend `gs1-digital-link-orchestrator` for clarity.
- **Verify:** name is available at `github.com/{your-org}/{name}` (not yet created).
- **Blocks:** Phase 1.

### 2.3 GitHub repo created (empty)

- **What:** create an empty repo on GitHub with the chosen name. **Do not** initialise with a README, license, or `.gitignore` — Claude Code will do that in Phase 1.
- **Verify:** the empty repo exists; you can `git clone` it (empty result).
- **Blocks:** Phase 1.

### 2.4 Local clone location

- **What:** decide where the local clone lives. Recommend `~/code/gs1-digital-link-orchestrator/`.
- **Verify:** parent directory exists (`~/code/` exists or you can create it).
- **Blocks:** Phase 1.

### 2.5 Project structure in Obsidian vault verified

- **What:** the project structure under `10_Clients/MDP/Projects/Noviplast_2D/` exists with `Project/` subfolder containing the six documents ([[PROJECT_HANDOVER]], [[IMPLEMENTATION_SPEC]], [[PREPARATION]] (this note), [[OBSIDIAN_NOTE_content]], [[GS1_NL_EMAIL]], [[architecture]]). Deliverable [[Preparation plan (Noviplast_2D)]] under `Deliverables/`, todo [[Investigation (Noviplast_2D)]] under `Todos/`.
- **Verify:** open Obsidian, navigate the paths, all notes present.
- **Blocks:** starting Claude Code sessions with reachable specs.

---

## Part 3: Per-client setup — Noviplast pilot

Repeat this section (Part 3) for every new client. For the initial build, complete for Noviplast.

> **Auth is OAuth2 client-credentials** (confirmed in Phase 2). You do **not** get a
> ready-to-use token — you get a **client id + client secret** and mint a short-lived
> JWT from them. See [[PROJECT_HANDOVER]] §4.1.

### 3.1 GS1 NL Digital Link API v2 — test/sandbox client credentials

- **What:** in the acceptance developer portal (`https://gs1nl-api-acc-developer.gs1.nl/`, signed in with the MyGS1 account) obtain the **Client ID** and **Client Secret** for the Digital Link API v2, and subscribe to that API product.
- **Verify:** both values received and copied.
- **Store as:** `NOVIPLAST_GS1_CLIENT_SANDBOX_ID` and `NOVIPLAST_GS1_CLIENT_SANDBOX_SECRET` in local `.env`.
- **Blocks:** Phase 2 (need them to mint tokens), Phase 9 (testing).

### 3.2 GS1 NL Digital Link API v2 — production client credentials

- **What:** same as 3.1, for the production developer portal / environment.
- **Store as:** `NOVIPLAST_GS1_CLIENT_ID` and `NOVIPLAST_GS1_CLIENT_SECRET`.
- **Blocks:** Phase 11 (production cut).

### 3.3 GS1 API credentials smoke-tested

- **What:** mint a token, then do a **read-only** GET (harmless — no writes):
  ```bash
  H=gs1nl-api-acc.gs1.nl   # production: gs1nl-api.gs1.nl
  TOKEN=$(curl -s -X POST \
    -H "client_id: $NOVIPLAST_GS1_CLIENT_SANDBOX_ID" \
    -H "client_secret: $NOVIPLAST_GS1_CLIENT_SANDBOX_SECRET" \
    "https://$H/authorization/token" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')
  # The account you may write to is in the token's accountNumber claim (base64 middle segment).
  curl -i -H "Authorization: Bearer $TOKEN" \
    "https://$H/digitallinkv2/v2/digitalLink/Gtin/00000000000000"   # expect 404
  ```
- **Verify:** mint returns `200` with `{"access_token": ...}`; the GET returns `404` (auth works, not-found confirmed). `400 "Your ClientId or ClientSecret might be incorrect."` on mint → wrong/inactive credentials, or lowercase `client_id`/`client_secret` header names not used.
- **Blocks:** Phase 2 completion.

### 3.4 Digital Link contract on the account (critical)

- **What:** confirm the account has a **Digital Link contract** — the entitlement that permits creating Digital Links (separate from API access). Without it, every create returns `400 21011 "No valid contract found."` even with valid credentials. In MyGS1, also activate Digital Link on at least one GTIN under that account (Edit → Web page → check "Activeer GS1 Digital Link", after setting a default `pip` link).
- **Verify:** a create (`POST /digitallinkv2/v2/digitallink`) for a GTIN under the account returns `200`/`201`, not `21011`.
- **If `21011`:** contact GS1 NL to provision the Digital Link contract on the account — this is a GS1-side step, not a code issue.
- **Blocks:** capturing successful sample responses (3.5), Phase 9.

### 3.5 GS1 API sample fixtures captured

- **What:** once 3.4 passes, mint a token and run the six calls in [[IMPLEMENTATION_SPEC]] §13.2 (single/bulk create, deliberate 400/401, GET existing, GET missing).
- **Store as:** files in `tests/fixtures/gs1_api/`.
- **Verify:** six response bodies + a `README.md` recording the confirmed facts — auth = OAuth2 (Bearer JWT), not-found = `404` empty body, 400 = `ErrorResult[]` shape, and the per-environment `accountNumber` from the token claim.
- **Blocks:** Phase 2 completion (unit test fixtures).

### 3.6 MyGS1 Excel export downloaded

- **What:** in MyGS1 for Noviplast, export the article list to Excel. If they use filters (e.g. by category), consider exporting **without filters** first to see all columns.
- **Store as:** `input/noviplast/products.xlsx` inside the code repo (once repo exists).
- **Verify:** file opens in Excel/LibreOffice; you can see product rows.
- **Blocks:** Phase 3.

### 3.7 Excel columns inspected and column-map drafted

- **What:** after Phase 3 provides `scripts/inspect_export.py`, run it against the Excel and draft a `column_map` in `clients.yml`. Iterate with `--dry-run` until zero warnings on required fields.
- **Verify:** `python -m scripts.parse_export noviplast --dry-run` completes with zero warnings on required fields.
- **Blocks:** Phase 3 completion.

### 3.8 WordPress staging site available

- **What:** coordinate with Noviplast to get a staging WP site or a safe subdomain of the production site (they hint at TransIP hosting; `novipl.site.transip.me` appears in one canonical URL). Confirm you can log into WP admin.
- **Verify:** you can reach `https://{staging-host}/wp-admin/` and log in.
- **Blocks:** Phase 4 completion.

### 3.9 WordPress version 5.6+

- **What:** verify WP version.
- **Where:** WP admin → Dashboard → Updates.
- **Verify:** version ≥ 5.6.
- **Blocks:** Phase 4 (Application Passwords require 5.6).

### 3.10 WordPress REST API reachable

- **What:** verify `/wp-json/` returns JSON.
- **Verify:** `curl https://{staging-host}/wp-json/` returns JSON with WordPress metadata.
- **Blocks:** Phase 4.

### 3.11 HTTPS enforced on staging

- **What:** TLS certificate on the staging domain.
- **Verify:** the site opens via `https://` without warnings.
- **Blocks:** Phase 4 (Application Passwords stripped over HTTP).

### 3.12 Security plugin conflicts identified and resolved

- **What:** check if Wordfence, iThemes/Solid Security, or All In One WP Security are installed. If so, verify REST API + Application Passwords are enabled. See [[PROJECT_HANDOVER]] §5.4.4 for the details.
- **Verify:** no security plugin is blocking `/wp-json/` or Application Password headers.
- **Blocks:** Phase 4.

### 3.13 Automation user created

- **What:** in WP admin → Users → Add New, create user `automation-bot` with role **Editor**.
- **Email:** a dedicated address or a `+bot@` alias — not someone's personal inbox.
- **Verify:** the user exists and can log in independently.
- **Blocks:** Phase 4.

### 3.14 Application Password generated

- **What:** log in as `automation-bot`, WP admin → Users → Profile → Application Passwords → name `gs1-orchestrator` → generate.
- **Store as:** environment variable `NOVIPLAST_WP_APP_PASS`.
- **Verify:** `curl -u "automation-bot:$NOVIPLAST_WP_APP_PASS" https://{staging}/wp-json/wp/v2/users/me` returns your user data.
- **Blocks:** Phase 4.

### 3.15 Custom post type registered with REST

- **What:** verify `noviplast` post type exists and is REST-enabled.
- **Verify:** `curl https://{staging}/wp-json/wp/v2/types | jq 'keys'` includes `noviplast`, and `.noviplast.rest_base` is set.
- **Fix if missing:** add `'show_in_rest' => true` to the `register_post_type` call in the theme's `functions.php` or the plugin registering the type.
- **Blocks:** Phase 4.

### 3.16 Custom taxonomies registered with REST

- **What:** verify `noviplast-categories` taxonomy is REST-enabled.
- **Verify:** `curl https://{staging}/wp-json/wp/v2/taxonomies | jq 'keys'` includes it.
- **Blocks:** Phase 4 if taxonomy integration is used.

### 3.17 Required taxonomy terms exist

- **What:** for each unique category value in the Excel `category` column, verify a matching term exists in WP admin → Products/Noviplast → Categories.
- **Verify:** all unique categories from Excel have a WP term.
- **Fix:** create missing terms manually (v0.1.0 does not auto-create; deferred to v0.2).
- **Blocks:** Phase 9 (pilot run).

### 3.18 Polylang configured

- **What:** install Polylang (if not already), configure NL + FR, NL default, subdirectory URL structure (`/fr/`), mark `noviplast` post type as translatable.
- **Verify:** `curl https://{staging}/wp-json/pll/v1/languages` returns 200 with both languages.
- **Blocks:** Phase 4 completion (multilingual detection test).

### 3.19 Media library upload limits raised

- **What:** in `wp-config.php` or PHP config, ensure `upload_max_filesize`, `post_max_size`, and `memory_limit` accommodate expected image sizes (5+ MB is common for product photos).
- **Verify:** upload a 5 MB test image via WP admin — should succeed.
- **Blocks:** Phase 4 completion if `image_handling: url_in_export` is used.

### 3.20 Theme's single-noviplast.php inspected

- **What:** open the theme file `wp-content/themes/noviplast/single-noviplast.php` (or equivalent). Understand what surrounding layout (header, sidebar, related products) will wrap our template content.
- **Verify:** you know what our template content needs to look like to fit visually.
- **Blocks:** Phase 5 template polish.

### 3.21 Permalinks configured

- **What:** WP admin → Settings → Permalinks → set to **Post name** or a custom structure with slug.
- **Verify:** view a manually-created page, URL contains the slug (not `?p=123`).
- **Blocks:** Phase 4 completion.

### 3.22 Slug strategy decided

- **What:** confirm with client whether QR-target URLs should use GTIN-based slugs (`p-8712345678905`) or product-name slugs. Recommend GTIN-based for determinism.
- **Where:** `wordpress.slug_pattern` in `clients.yml`.
- **Verify:** decision recorded; permanent because it appears in printed QR URLs.
- **Blocks:** Phase 5.

### 3.23 Draft product template created

- **What:** create `templates/noviplast/product.nl.html` and `templates/noviplast/product.fr.html` as first-cut Mustache templates that fit the theme's `single-noviplast.php` layout.
- **Verify:** template renders without Mustache errors against a sample `ProductRecord`.
- **Blocks:** Phase 5 completion.

### 3.24 Local `.env` file populated

- **What:** copy `.env.example` to `.env`, fill in `NOVIPLAST_GS1_CLIENT_SANDBOX_ID`, `NOVIPLAST_GS1_CLIENT_SANDBOX_SECRET`, `NOVIPLAST_GS1_CLIENT_ID`, `NOVIPLAST_GS1_CLIENT_SECRET`, `NOVIPLAST_WP_APP_PASS`.
- **Where:** `.env` at the repo root (gitignored).
- **Verify:** `python -c "import os; print(bool(os.getenv('NOVIPLAST_GS1_CLIENT_SANDBOX_ID')))"` returns `True` in a shell that sourced `.env`.
- **Blocks:** any Phase 2+ run.

### 3.25 `clients.yml` populated

- **What:** copy `clients.example.yml` to `clients.yml`, fill in the `noviplast` block per [[PROJECT_HANDOVER]] §10.1.
- **Where:** `clients.yml` at the repo root (gitignored).
- **Verify:** `python -m scripts.parse_export noviplast --dry-run` runs (may report warnings on optional fields, but no schema errors).
- **Blocks:** Phase 3.

---

## Part 4: Verify ready for Phase 1

Before starting the first Claude Code session:

- [ ] Part 1 fully complete
- [ ] Part 2 fully complete
- [ ] Part 3.1, 3.2 done (GS1 keys)
- [ ] Part 3.6 done (Excel export in hand)
- [ ] Vault project structure verified (item 2.5)
- [ ] [[Noviplast_2D]] project hub note reachable

Parts 3.7 through 3.25 can be completed during Phases 2–5 as they become relevant. But if you can front-load 3.8–3.14 (WP staging setup) before Phase 4 starts, you'll save a week of calendar time.

---

## Part 5: Continuous items (do not check off)

These are ongoing hygiene, not one-off tasks:

- Keep the vault spec documents in sync with any material change discussed in a Claude Code session. Bump version numbers in metadata when materially updated.
- Every material decision that changes the architecture goes into [[PROJECT_HANDOVER]] §3 as a new decision entry with rationale
- Every new client repeat Part 3 for that client's context
- Every version bump: `pyproject.toml`, `package.json`, `CHANGELOG.md`, and spec metadata

---

## Cross-references

- Full onboarding walkthrough: [[PROJECT_HANDOVER]] §5
- WordPress setup detailed reference (15 items with fixes): [[PROJECT_HANDOVER]] §5.4
- Noviplast-specific discovery findings: [[PROJECT_HANDOVER]] §5.5
- Developer verification steps (curl commands): [[IMPLEMENTATION_SPEC]] §13
- Phase-by-phase development plan: [[PROJECT_HANDOVER]] §8.2
- Definition of Done per phase: [[IMPLEMENTATION_SPEC]] §12

---

**End of document.**