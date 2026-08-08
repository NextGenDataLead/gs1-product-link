# Research brief: making the GS1 Digital Link Orchestrator installable and operable by a non-technical user

**Audience:** a research agent tasked with proposing a sandboxed, easy-to-install packaging and a UI shell for this tool.
**Status:** nothing has been built. This document is context and constraints only.
**Repo:** `gs1-product-link` (working copy `Noviplast_Wordpress_GS1DataLink`), `main` @ `492e8b2`, v0.1.0 released, clean.

---

## 0. The question being researched

> *For a non-technical person, installing and configuring this repo is hard. Can we sandbox the whole thing so it is easy to install and configure behind a UI shell — possibly driving `claude -p` (headless Claude Code) under the hood?*

Read §8 before proposing anything involving `claude -p`. There is a specific, serious trap in the obvious version of that idea.

---

## 1. What the system is

A single-tenant publishing pipeline. It takes a client's GS1 product data export (an Excel workbook) and:

1. **parses** it into normalised product records,
2. **generates** marketing copy (a tagline and an "Eigenschappen" feature block) with an LLM,
3. **publishes** one WordPress page per `(GTIN, language)` via the WP REST API, writing ACF fields,
4. **registers** a GS1 Digital Link record per GTIN in the GS1 NL production resolver, pointing `https://id.gs1.org/01/{gtin14}` at that page,
5. **renders** a QR code encoding that Digital Link URI, for printing on physical packaging.

It runs **from inside a Claude Code session**. The operator types `publish {client} to GS1`; Claude loads a skill and drives Python CLI scripts via Bash tool calls, pausing at operator confirmation gates.

There is one live pilot client (`noviplast`, 10 GTINs published and resolving, a 38-row process list pending). "One client per repo" is a settled decision — the multi-client *capability* exists but the product is single-tenant.

---

## 2. Why the stakes are unusual

This is not a CRUD app. Four properties shape every design decision:

| Property | Consequence |
|---|---|
| **A GS1 Digital Link record can never be deleted.** The v2 API has no DELETE. Retraction only clears the links and disables the record; a dead record stays on the account forever. | Every write against a real GTIN is permanent. A QR printed against a wrong URL is permanent in the physical world. |
| **The ACF write path fails silently.** A `200` proves the post exists, not that its fields landed. | "It returned success" is not evidence. Verification means fetching rendered HTML. |
| **A successful run and a silent no-op look identical.** E21: with a generator configured, `run_plan` omits any row with no generated tagline — an empty cache yields an empty plan and a run that reports success having published nothing. | Any UI must distinguish "did nothing" from "did the work". |
| **Corrupt state silently converts an incremental run into a full rewrite.** E19: `run_plan` quarantines a bad `state.json`, starts fresh, and **exits 0**. Every row re-plans as NEW. The counts alone read as a routine first run. | A UI that shows only the counts would hide the single most dangerous state. |

The full failure taxonomy is E1–E22 in `docs/troubleshooting.md` L445–472.

---

## 3. Current architecture

```
Claude Code session
  └─ .claude/skills/*/SKILL.md         ← 9 skills, 957 lines of prose
       └─ Bash tool calls
            └─ python -m scripts.<name>   ← 9 argparse CLIs
                 └─ lib/                  ← the actual library (mypy --strict)
                      └─ httpx → WordPress REST, GS1 NL API, Anthropic API
```

**Nine Python entry points**, all invoked as `python -m scripts.<name>`. There are **no console_scripts** — `pyproject.toml` has no `[project.scripts]`.

| Script | Purpose | Network? |
|---|---|---|
| `inspect_export` | Print a workbook's GDSN attributes + a paste-ready config block | no |
| `parse_export` | Workbook → `output/{c}/data/products.json` | no |
| `report_quality` | Render all issue files → `data-quality-report.md` | no |
| `run_plan` | Classify each `(GTIN, lang)` NEW/UNCHANGED/CHANGED → `plan.json` | no |
| `run_generate` | Fill the copy cache (`--emit`/`--ingest` in-session, or `--backend api`) | only with `--backend api` |
| `run_execute` | **Writes pages, Digital Links, QR** | yes |
| `run_unpublish` | Retract the Digital Link, then draft the pages | yes |
| `build_brick_map` | Draft a GPC-brick → site-category map | no |
| `build_video_map` | Draft a video-filename → GTIN map | no |

Parse, plan, report, and both map builders are **entirely offline**. Only `run_execute` and `run_unpublish` touch live systems. That asymmetry is a gift to a UI design: most of the flow can be exercised safely.

**Nine skills** in `.claude/skills/`. `flow-orchestrator` (337 lines) is the gated pipeline; `/gs1-pages`, `/gs1-links`, `/gs1-publish` are thin mode-pinning wrappers; the other five cover individual steps.

---

## 4. The install burden today, measured

`docs/setup.md` is 252 lines. Walking it end-to-end for a new client is **~33 discrete steps, of which roughly 5 are trivial for a non-technical person.** `docs/PREPARATION.md` adds ~40 more numbered prerequisites.

**Prerequisites before step 1:**

| Requirement | Notes |
|---|---|
| Claude Code, installed and authenticated | The operating surface. claude.ai / Desktop / Cowork are explicitly out of scope. |
| Python ≥ 3.11 | No `.python-version`, **no lockfile** (`uv.lock` is gitignored), plain `venv` + `pip install -e ".[dev]"` |
| Node ≥ 20 | Only to build the MCP servers, which are not wired in (§10) |
| git | To clone |
| ffmpeg | Only when `media.video_transcode: true`. The one external binary. |
| **GS1 Data Source contract** | Blocking, provisioned by GS1 |
| **GS1 Digital Link contract on the same account** | **Hard blocker.** Without it every write is `400 21011 "No valid contract found."` Not fixable in code or config. |
| GS1 OAuth2 client_id + secret, per environment | Issued via MyGS1 |
| WordPress 5.6+, REST reachable over HTTPS | Plus an 11-point site checklist (`docs/wordpress-onboarding.md`) |
| Anthropic API key | **Optional** — only for the headless generator backend |

The WordPress side alone requires: an automation user with editor rights, an application password, a custom post type registered `show_in_rest: true`, taxonomies `show_in_rest: true` with terms pre-created, ACF fields exposed to REST, raised media upload limits, and — **for WPML — a hand-installed PHP mu-plugin or Code Snippet on the site**, because WPML has no core REST route for language assignment. That is developer work on someone else's server.

**The four highest-frequency novice failures:**

1. Leaving the `democlient:` example in `clients.yml` alongside their own → `client_id` becomes mandatory on every command.
2. Not single-quoting the WordPress application password in `.env` — WP issues it as six space-separated groups, so an unquoted value truncates at the first space. This is documented in **four separate files**, which is a measure of how often it happens. Symptom: a `401` with a password the operator knows is correct.
3. `MissingCredentialError` fires **lazily at the first API call**, not at startup — so the operator gets all the way through parse, plan, and dry-run before discovering a missing secret.
4. The export at the wrong path. `export.path` in YAML is authoritative and has **no CLI override**; a fresh export dropped in a new folder is invisible to the tool.

---

## 5. The configuration surface

Three files must be authored by hand.

**`.env`** (gitignored, template at `.env.example`). Variable *names* are not fixed by code — `clients.yml` names them. Six live values in practice: four GS1 OAuth credentials (test + production pairs), one WordPress app password, one optional Anthropic key. Plus a ten-variable staging-test block that must stay empty unless deliberately running live-writing tests.

**`clients.yml`** (gitignored, template `clients.example.yml` is **243 lines**). Validated twice — JSON Schema at `schema/clients.schema.json`, then Pydantic in `lib/config.py`. Eleven blocks:

| Block | What it holds | Authorable by a non-expert? |
|---|---|---|
| `gs1` | account numbers, env-var *names*, environment | with a form, yes |
| `export` | format, path, `market_priority`, **`gdsn_map`**, `gdsn_extras` | **no** — per-attribute GDSN mapping |
| `wordpress` | site_url, username, post_type, languages, **`acf_map`**, `slug_pattern`, `target_url_pattern` | half — credentials yes, ACF map no |
| `qr` | formats, size, DPI | yes |
| `template` | override dir, per-language filenames | yes |
| `gs1_links` | link types, title patterns | mostly |
| `flow` | prompt behaviours | yes |
| `process_list` | path, GTIN column name | yes |
| `categories` | terms, **`brick_category_map`**, per-GTIN overrides | **no** — needs the GS1 sector datamodel |
| `generator` | enabled, model, prompt_version, key env name | yes |
| `media` | image sizing, **ACF media field names**, video folders, mapping path | half |

**Input files** — all paths configured in YAML, none overridable at the CLI: `products.xlsx`, `process-list.xlsx`, per-language video folders (case-sensitive), `videos/mapping.yml`.

**Also hand-authored per client:** `prompts/{client}/generation.{version}.md` (the voice template — required by the API generator backend) and optionally `templates/{client}/product.{lang}.html`.

---

## 6. Two different problems — do not conflate them

This is the most important framing in this document. "Configuration" covers two tasks with completely different economics:

### (A) Onboarding a client — once, expert work

Authoring `gdsn_map`, `acf_map`, `brick_category_map`, the voice template, and the WordPress-side setup. This requires a **field walk against the live site**: discovering which GDSN attribute actually holds the product name (the pilot's answer — attribute 3301, not 3318 or 3297 — took an exhaustive search and is documented at length), and which ACF fields the theme actually renders from.

A UI cannot make this trivial. It can make it *guided, validated, and reviewable*. This is also where `claude -p` has genuine leverage: `inspect_export` already prints a paste-ready config block, and a model could reconcile that against a probe of the WordPress site's ACF fields to **propose** a `clients.yml` for expert sign-off.

### (B) Operating a client — recurring, the actual pain

Drop a new export, prune the process list, run the flow, read the result. This is what a non-technical operator does repeatedly, and it is **highly automatable**. The whole recurring loop is: one file upload, one row-pruning grid, seven menu choices, one results screen.

**A proposal that solves only (B) is still a large win.** A proposal that claims to fully solve (A) should be treated with suspicion.

---

## 7. Runtime facts a sandbox designer needs

**Filesystem.** Mixed path anchoring, which matters for volume mounts:

- Repo-root-anchored (resolved from `__file__`): `clients.yml`, `.env`, `schema/clients.schema.json`
- **CWD-relative**: `output/`, `input/`, and `prompts/` (`lib/llm.py` does `Path("prompts") / client_id / ...`)

`output/{client}/` holds: `state.json` (**the single source of truth for what is live** — atomic writes, corrupt files quarantined), `plan.json`, `plan.confirmed.json`, `data/*.json` (products, generated cache, issue reports), `qr/`, `media/`, and `runs/{ts}.jsonl` (append-only per-row run log). `input/` and `output/` are gitignored.

**There is no file logger.** `lib/logging_setup.py` documents one as "lands in a later phase"; today it only provides secret/PII scrubbers. Logging goes to stderr.

**Network egress** — a short, auditable list:

| Host | Purpose | Configurable |
|---|---|---|
| `gs1nl-api-acc.gs1.nl` / `gs1nl-api.gs1.nl` | GS1 Digital Link API v2 + its OAuth token endpoint (same host, `/authorization/token`) | **hardcoded**, selected by `gs1.environment` |
| the client's WordPress site | page/media/ACF writes, plugin probing | fully configurable |
| `api.anthropic.com/v1/messages` | headless generator backend only; raw httpx, **no SDK** | test-only `base_url` override |
| **arbitrary image hosts** | fetching product images from URLs in the feed | **no allowlist** — determined by feed data |

Only three `httpx.Client` instances exist in the entire codebase. Everything else is offline.

**Secrets.** `lib/env.py` is 55 lines and one function. Two properties are load-bearing:

- `ENV_PATH` resolves from `__file__`, not CWD.
- **`load_env()` must be called from each script's `if __name__ == "__main__":` block, never from `main()`.** Nine test modules call `main()` directly, and `.env` carries the four staging-guard variables — loading it in the test path would arm tests that write to live WordPress and GS1 production. `tests/lib/test_env.py` enforces this with an AST check.

> **Direct consequence for a UI shell:** a shell that imports `scripts.run_execute.main()` **in-process will not have credentials loaded**. It must either subprocess (`python -m scripts.…`) or load `.env` itself — and if it does the latter, it must not do so in any process that might also run pytest.

`clients.yml` holds only env-var *names*, never values. `chmod 600 .env` is documented in three places but **nothing in code checks or enforces it**.

**Packaging gaps relevant to reproducibility:** no lockfile, no Docker/devcontainer/Nix (verified — zero hits repo-wide), no Makefile, no console_scripts, no release workflow. CI pins Python 3.11; the local `.venv` is 3.14.5. The installed editable dist reports version `0.0.1` while `pyproject.toml` says `0.1.0`.

**Test hazard.** `pyproject.toml` sets `addopts = "-m 'not staging'"`. This is not belt-and-braces: the `skipif` env-var guard alone proved insufficient, because a shell that had sourced `.env` satisfied it and *made a bare `pytest` hit production*. Two test modules write to live WordPress and the GS1 production resolver.

---

## 8. Where the safety actually lives — and the trap in `claude -p`

**The operator gates are prose in a Markdown file, executed by a language model.** `CLAUDE.md` states it plainly: *"The operator gates live **only** in `.claude/skills/flow-orchestrator/SKILL.md`. Calling `scripts/run_execute.py` directly bypasses every one of them."*

The Python scripts carry exactly two hard guards, both deliberately in code *"precisely because prose can be skipped"*:

- `run_execute` refuses a live production run without `--i-understand-production` (exit 2). The skill appends that flag only **after** the operator confirms at a gate.
- `--only links` refuses any GTIN whose target URL does not serve, before writing anything permanent.

Everything else — mode classification, export-path cross-check, language selection, copy review, plan review, per-row diffs, the production environment confirmation, the mandatory dry run — is prose.

**The flow has nine operator touchpoints, seven of them fixed-option menus:**

| Step | Gate | Menu |
|---|---|---|
| 0 | Intent: mode, export path + mtime cross-check, product count, environment, permanence warning | `[confirm \| change-mode \| cancel]` |
| 2 | Language selection | `[all \| nl \| fr \| nl,fr]` |
| 3 | **Review gate 1 of 2** — eyeball the generated copy before it can reach a page | free-form |
| 4 | Missing-field prompt, per warning | `[skip-row \| ask-me-later \| fail-run]` |
| 5 | **Review gate 2 of 2** — plan counts, exclusions, E19 reset warning *above* the counts | `[all \| new-only \| changed-review \| cancel]` |
| 6 | Per-row diff (only on `changed-review`) | `[apply \| skip \| show-full-diff]` |
| 8 | **Production environment confirmation** — mandatory, non-overridable, per-run | `[confirm \| switch-to-test \| cancel]` |
| 8.5 | **Mandatory dry run** — same command, `--dry-run`, every other flag identical | operator reads output |
| 11 | Post-run summary + retry | `[yes \| no \| detail]` |

### The trap

**`claude -p` is non-interactive. It cannot ask the operator anything.**

So `claude -p "publish noviplast to GS1"` would load `flow-orchestrator` and execute the entire sequence with **every gate answered by the model, or skipped**. That is the exact failure the architecture is built to prevent, and it writes permanent, undeletable records. Any proposal that routes the gated flow through a single headless invocation is unsafe and should be rejected on sight.

### The reframing that makes it work

The gates are already a **fully specified state machine with fixed menus and verbatim prompt text**. That is close to a UI spec. The productive inversion:

- **The UI shell owns the gates.** Renders each as a form. A required checkbox on an irreversible action is a *stronger* gate than prose a model might paraphrase, compress, or skip when the context is long.
- **`claude -p` is used only for narrow, bounded, non-gating sub-tasks** where a model is genuinely needed — with restricted tool access and no authority to execute anything irreversible. Candidates:
  - **Copy generation** (step 3) — today's in-session path. This is the strongest fit: it needs a model, it writes only to a cache, and the output is reviewed at two gates before it can reach a page.
  - **Error explanation** (step 11 `detail`) — read the run JSONL and explain each failure.
  - **Qualitative run observations** (step 12) — the "worth a glance" flags that only a review would notice.
  - **Onboarding proposal** (§6A) — read `inspect_export` output, probe WordPress, propose a `clients.yml` diff for expert sign-off.
- **The deterministic legs call the Python CLIs directly.** Parse, plan, dry-run, execute — no model in the loop.

**The cost of this inversion is real and must be stated:** the gate logic moves from `SKILL.md` prose into code. Two implementations of the same safety contract will drift unless they are single-sourced. How to prevent that drift is a first-class research question (§11).

**A second `claude -p` wrinkle:** it authenticates against the machine's Claude Code installation. Inside a container, that means either mounting host credentials or falling back to `ANTHROPIC_API_KEY`. This tension pulls against containerisation and should be weighed explicitly.

---

## 9. Prior decisions that constrain the solution space

These are settled. A proposal that reopens one needs to argue the case, not assume it.

| Decision | Detail |
|---|---|
| **No cloud sandbox.** | Claude Cowork was evaluated and **removed** — *"it executes in a remote cloud sandbox, which would mean handing production WordPress and GS1 credentials to an environment outside your control."* Egress from a remote sandbox to the client's WordPress was also unproven. **"Sandbox" here must mean local isolation, not hosted.** |
| **Nothing leaves the machine.** | `README.md`: *"There are no central services, nothing to host, and nothing of yours leaves your machine."* |
| **One client per repo.** | Decided 2026-07-31, PR #34. `client_id` is an optional positional; inferred when exactly one client is defined. Not multi-tenant. |
| **The MCP servers stay private.** | OD-2, decided 2026-07-31. All three `private: true`, unpublished, no `.mcp.json`, not registered anywhere. `server.json` drafts stay committed so the decision is cheap to reverse. |
| **`.env` is the single source of truth for credentials.** | OD-1. The `~/.claude/settings.json` `env` block was deleted; `lib/env.py` replaced it. |
| **`main` is branch-protected.** | Changes go through a PR. CI: `ruff check`, `ruff format --check`, `mypy --strict lib`, `pytest`. |

---

## 10. Assets already in the repo a solution could reuse

Do not rebuild these.

- **`mcps/` — three working TypeScript MCP servers**, currently dormant by choice. `gs1-nl` (3 tools), `wordpress` (5 tools), `qr-render` (1 tool). They read `clients.yml` from CWD or `GS1_CLIENTS_FILE`. They are strict *subsets* of the Python clients (no retract, no delete, no translation linking) — which is a safety property, not just a gap. A UI shell could adopt them, or OD-2 could be revisited. **Not committed as built artifacts** — `dist/` is gitignored, so a fresh clone needs `npm ci && npm -w mcps/<name> run build`.
- **`schema/clients.schema.json`** — a real JSON Schema for `clients.yml`. A config UI can be generated from or validated against it rather than hand-written.
- **`lib/config.py` Pydantic models** — the second validation layer, with typed errors.
- **`scripts/inspect_export.py`** — already prints a paste-ready `export`/`gdsn_map` block from a workbook.
- **`build_brick_map --check` and `build_video_map --check`** — the only two existing coverage gates. Both exit 1 on gaps and write issue JSON. Precedent for a preflight.
- **`scripts/report_quality.py`** — renders every machine-readable issue file into one Markdown report. A UI's "what needs fixing" screen already exists in data form.
- **`lib/generator.py`** — the `LLMClient` protocol and cache. Model is deliberately **excluded** from the cache fingerprint so the in-session and API producers are interchangeable. **A `claude -p` producer is a third implementation of an existing seam, not a new architecture.**
- **`lib/logging_setup.py`** — secret/PII scrubbers already written (`authorization`, `ocp-apim-subscription-key`, `x-api-key`, `meta` subtree redaction). Essential if a UI surfaces logs.
- **Uniform exit codes** across all nine scripts: `0` success · `1` the work had errors (partial success is normal; runs do not abort per-row) · `2` config/credential/usage error at startup, and the refused production run.
- **`runs/{ts}.jsonl`** — structured per-row results, ready to render as a results table.

**What does not exist:** any UI, TUI, wizard, doctor, or preflight command. Zero hits for `input(`, `click`, `typer`, `rich`, `questionary`, `prompt_toolkit`. Every script is non-interactive argparse. The closest thing to a config validator is a `python -c` one-liner in `setup.md`.

---

## 11. Research questions

Ranked by how much the answer changes the design.

1. **How is the gate contract kept single-sourced?** If the UI owns the gates and `SKILL.md` also describes them, they will drift. Options to evaluate: extract the gates to a declarative spec both consume; make the UI the only path and reduce the skill to a pointer; generate the skill prose from the spec. What does each cost, and what breaks if they diverge?
2. **What does "sandbox" mean here, given credentials must not leave the machine?** Compare: Docker Desktop (true isolation; a real install hurdle for a non-technical user; complicates `claude -p` auth), a `uv`-based self-contained venv installer (no container, but reuses host Claude Code auth and needs no Docker install), a signed desktop app bundling Python, and a plain TUI. Weigh install friction against isolation honestly — for this audience install friction *is* the problem being solved.
3. **What is the minimum viable UI surface?** Map §8's nine touchpoints plus file upload, process-list pruning, and preflight to concrete screens. Which of §5's config blocks can be forms, which stay expert-authored-but-validated, and which are read-only?
4. **Where exactly does `claude -p` earn its place?** For each candidate in §8, specify the prompt, the allowed tools, the output contract, and what happens on a bad or empty response. Note that the copy-generation case already has a defined seam (`lib/generator.py`) and a defined handoff format (`generation_requests.json` → `generation_results.json`).
5. **`claude -p` vs `ANTHROPIC_API_KEY` vs both.** The API path is already built and tested (`lib/llm.py`, raw httpx, forced tool call, temperature 0, 4-attempt retry). `claude -p` avoids a separate API key and per-token billing but adds a hard dependency on Claude Code being installed and logged in. Auto-detection is possible. What are the real trade-offs, including inside a container?
6. **What does a preflight/doctor check, and when?** The current lazy credential check (`MissingCredentialError` at first API call) means an operator can complete parse, plan, and dry-run before discovering a missing secret. Design the checks: `.env` completeness and permissions, `clients.yml` schema, WordPress reachability + auth + post-type/ACF/taxonomy presence, GS1 token mint + contract presence, input file presence and mtime, ffmpeg on PATH.
7. **How is reproducibility fixed?** No lockfile; CI on 3.11, local venv on 3.14. Evaluate `uv` + `uv.lock` (currently gitignored), pip-tools, or a container image digest.
8. **What is the smallest first release that is genuinely useful?** §6 argues for run-only. Is that right, or does the onboarding half have to ship together to be worth anything?

---

## 12. How to judge a proposal

A good proposal must:

- [ ] **Preserve every gate in §8**, or explicitly argue why a specific one is redundant in a UI context. The step-8 production confirmation and the step-8.5 dry run are non-negotiable.
- [ ] **Never route the gated publish flow through a single non-interactive `claude -p` call.**
- [ ] Keep credentials on the operator's machine (§9).
- [ ] Distinguish "did nothing" from "did the work" — E19 and E21 must be visible, not smoothed over. The E19 reset warning must appear *above* the plan counts, not below.
- [ ] Subprocess the scripts, or explicitly handle the `load_env()`-in-`__main__` constraint (§7).
- [ ] Reuse the assets in §10 rather than reimplementing them.
- [ ] Be honest about §6A: onboarding a new client involves a field walk against a live site and cannot be fully automated. A proposal claiming otherwise is overpromising.
- [ ] State its install prerequisites plainly. If it requires Docker Desktop, say so — that is a real cost against the actual goal.

---

## Key files for the research agent to read

| Purpose | Path |
|---|---|
| The gate sequence — **read this first** | `.claude/skills/flow-orchestrator/SKILL.md` (337 lines) |
| Project invariants, learned the hard way | `CLAUDE.md` |
| Current install/config walkthrough | `docs/setup.md` |
| Full operator prerequisite checklist | `docs/PREPARATION.md` |
| WordPress-side requirements incl. the WPML PHP helper | `docs/wordpress-onboarding.md` |
| GS1 contract prerequisites and the 21011 blocker | `docs/gs1-nl-onboarding.md` |
| Error taxonomy E1–E22 | `docs/troubleshooting.md` |
| Settled decisions incl. OD-1 (.env) and OD-2 (MCPs private) | `docs/OPEN_DECISIONS.md` |
| Config schema and models | `schema/clients.schema.json`, `lib/config.py` |
| The full worked config example | `clients.example.yml` (243 lines) |
| The credential-loading constraint | `lib/env.py`, `tests/lib/test_env.py` |
| The LLM seam both producers share | `lib/generator.py`, `lib/llm.py`, `.claude/skills/content-generator/SKILL.md` |
| The two in-code guards | `scripts/run_execute.py` (production flag; target-serves check ~L258–286) |
| How live verification is actually done | `docs/verifying-live.md` |
