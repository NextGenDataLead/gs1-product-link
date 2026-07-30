# Troubleshooting

Every error this tool raises, plus the traps that have already cost real debugging time on a live
pilot. If you are mid-incident, start with [Traps that have actually bitten](#traps-that-have-actually-bitten)
— the failure is more likely there than in the reference tables.

## How to read a failure

**Exit codes** are uniform across the scripts:

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | The work itself had errors. Some rows may have succeeded — the run does **not** abort on a single row failure. |
| `2` | Configuration, credential, or usage error at startup. Nothing was attempted. Also the **refused production run**. |

**Where to look:**

| What | Path |
|---|---|
| Per-row outcome of every mutating run | `output/{client_id}/runs/{ts}.jsonl` — one `RunOutcome` per row, written whether the row succeeded or failed |
| What the tool believes is already published | `output/{client_id}/state.json` |
| Source-data problems | `output/{client_id}/data-quality-report.md` (`python -m scripts.report_quality {client_id}`) |
| What was actually published, per wave | `docs/clients/{client_id}-live-log.md`, where a client keeps one |

**Every exception** derives from `OrchestratorError` (`lib/errors.py`), so `except OrchestratorError`
catches anything this tool raises. Secrets never reach the logs:
`lib.logging_setup.scrub_response_body` redacts `password`, `secret`, `token`, `key`,
`authorization`, and the whole `meta.*` subtree of WordPress responses.

---

## Traps that have actually bitten

These are not hypothetical. Each one was diagnosed the hard way during the pilot.

### The resolver 404s to HEAD but 307s to GET

Testing Digital Link resolution with `curl -I` or any HEAD request returns **404** even when the
entry is live and correct. Always test with **GET**:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://id.gs1.org/01/{gtin14}       # -> 307
curl -sS -o /dev/null -w '%{http_code}\n' -L https://id.gs1.org/01/{gtin14}    # -> 200 at the page
```

A HEAD 404 is **not** evidence of a broken record. Re-test with GET before touching anything.

### `400 21011 "No valid contract found."`

The GS1 account has no **Digital Link contract**. This is a GS1-side provisioning matter, not a bug
and not something a code change can fix — the account needs the contract added. The Data Source
contract that issued your GTINs is a *different* contract. Note the asymmetry: on a **GET** this same
400 is interpreted as "no entry exists" and `get()` returns `None`; on a **write** it is fatal.

### An unquoted WordPress application password loads empty

WordPress issues application passwords as six space-separated 4-character groups. In `.env`:

```bash
NOVIPLAST_WP_APP_PASS='abcd EFGH ijkl MNOP qrst UVWX'   # correct — single-quoted
NOVIPLAST_WP_APP_PASS=abcd EFGH ijkl MNOP qrst UVWX     # BROKEN — silently empty
```

Unquoted, `source .env` stops at the first space and the variable loads as `abcd`. The symptom is a
confusing `401` even though the password is right. `python-dotenv` — which the scripts use — parses the
unquoted form correctly, so this bites only when you source `.env` by hand, as the staging tests
require. Keep the quotes and both paths work.

### `MissingCredentialError` when you expected the credentials to be there

`python -m scripts.<name>` loads `.env` for you — `load_env()` in `lib/env.py`, called from each
script's `if __name__ == "__main__":` block. So if a credential is missing, work through this order:

1. **Is the variable in `.env`, at the repository root?** That file is the single source of truth
   (OD-1). `lib/env.py` resolves it relative to the repo, not your working directory, so *where* you
   run from does not matter.
2. **Does the name match `clients.yml` exactly?** `clients.yml` stores env var **names**, not values;
   a typo in either place produces this error with everything apparently present.
3. **Is the value non-empty?** A bare `NAME=` in `.env` sets the variable to an empty string, which
   reads as present but fails at the API. Watch for the unquoted-password trap above.
4. **Is something already exporting that name?** `load_env()` uses `override=False`, so a variable
   already in your environment wins over `.env` — deliberately, so CI and one-off overrides work. An
   empty or stale export therefore beats a correct `.env`. `unset NAME` and retry.

**Two places `.env` is *not* loaded**, by design:

- **The test suite.** `load_env()` sits in the `__main__` block rather than in `main()`, and the tests
  call `main()` directly. `.env` holds all four variables the staging guards gate on, so loading it in
  the test path would arm tests that write to live WordPress and GS1 production. To run those
  deliberately: `set -a; source .env; set +a && pytest -m staging`.
- **`lib/` at import time.** A library must not have import side effects.

If you are sourcing manually, note that **environment variables do not survive between separate Claude
Code tool calls** — sourcing in one call and running in the next loses them silently. Keep both in one
command with `&&`.

### A real production run is refused with exit 2

```
run_execute: refused — gs1.environment is 'production' and --i-understand-production was not passed
```

Working as designed. A live run against a `production` GS1 client requires
`--i-understand-production`, so that a bare `--plan` cannot publish live pages and mint permanent
GS1 records. Either pass the flag deliberately, add `--dry-run`, or switch the client to `test`.
`flow-orchestrator` appends the flag itself, but only after its environment-confirmation gate.

### A page returns 200 but shows no content

**The ACF write path fails silently.** A `200` from WordPress means the post exists — it does not
mean your field values landed. Always fetch and inspect the rendered HTML:

```bash
curl -sS https://{site}/{slug}/ | grep -o 'your-expected-copy'
```

Common causes: an `acf_map` field name that does not exist on the site; ACF fields not exposed to
REST; or `media.image_write_shape` set to `url` where the theme expects an attachment **id**.

### Images write as an attachment id, not a URL

The ACF image field expects the **attachment id**. Writing a URL produces a page that renders
without its image and reports no error. This is what `media.image_write_shape: id` (the default)
exists for.

### Media re-runs create duplicates unless the slug is content-addressed

Media dedup cannot rely on a `content_sha256` meta key: WordPress **silently drops unregistered meta
on attachments**, so the marker vanished and every run re-uploaded. Worse, stale attachments squatted
the base slug. The fix in place is a **content-addressed slug**, `{base}-{sha12}` — dedup becomes a
pure slug lookup that needs no meta and cannot be squatted. Two consecutive runs now reuse the same
attachments. If you see media duplicating, check that this slug scheme is intact rather than adding a
meta key back.

### The state file reset itself and every row became NEW

See **E19** below. This is recoverable and safe by design, but it silently converts an incremental
re-run into a full rewrite of live pages. `run_plan` leads its summary with a warning and
`flow-orchestrator` surfaces it above the counts — **do not confirm past that warning** without
understanding why the reset happened.

### Two `run_execute` runs at once

Not supported. There is no lockfile in v0.1 (**E20**). Concurrent runs for the same client will
interleave state writes and lose updates. Run them one at a time.

### A GS1 record can never be deleted

The v2 API has **no DELETE**. `run_unpublish` / `retract` deactivates the entry (`isEnabled` →
`false`) and deliberately leaves its links intact so a later reactivation does not have to re-enter
the whole configuration. The deactivated record stays on the account **permanently**. This is why a
smoke-test GTIN must never be a real product's GTIN.

### A page you drafted republished itself

Drafting a page does not remove it from the plan. Use `run_unpublish`, which classifies the GTIN as
HELD so it cannot be picked up again; a manually drafted page will be re-published by the next run.
`run_execute --revive` is the deliberate opt-in to publish GTINs that `run_unpublish` took down.

---

## Exception reference

All thirteen classes in `lib/errors.py`. (`IMPLEMENTATION_SPEC.md` §4.1 names the original eight;
the five added since are marked ✚.)

### `OrchestratorError`

Base class. Catch this to handle any tool-originated failure.

### `ConfigError`

Configuration missing, malformed, or internally inconsistent. Exit 2.

Common causes: `clients.yml` fails `schema/clients.schema.json` validation; an unknown `client_id`;
`multilingual_plugin: wpml` without `wpml_helper_path` and `default_language`; a WPML source language
absent from the linked set; **GS1 rejecting your credentials** (a 4xx from the token endpoint raises
`ConfigError`, not `GS1APIError` — it is a configuration fault, not an API outage).

**Fix:** validate the config first — `python -c "from lib.config import load_clients;
load_clients('clients.yml')"`.

### `MissingCredentialError`

An environment variable named in `clients.yml` is unset. Resolution is **lazy**, so this surfaces at
the first API call, not at startup (**E15**).

**Fix:** see [the section above](#missingcredentialerror-when-you-expected-the-credentials-to-be-there)
for the checklist. In short: the value belongs in `.env`, which scripts load automatically; remember
`clients.yml` holds env var *names*, never values.

### `ExportParseError`

A row could not be parsed into a `ProductRecord`. `parse_export` writes **nothing** and exits 1 —
partial output is never produced.

Common causes: a GTIN row with no `product_name` in the default language (**E5**); a required column
missing (**E17**); a `column_map` target that is not a `ProductRecord` field (**E6**, raised at config
load).

**Fix:** `python -m scripts.parse_export {client_id} --dry-run` and iterate until clean. Required
fields are `brand` and `product_name`.

### `GS1APIError`

A Digital Link API call returned a non-success response after retries.

Attributes: `status_code`, `response_body` (raw, unscrubbed, for inspection), `error_results`,
`request_id`. `status_code == 0` means a transport failure below HTTP, not a server response.

`error_results` holds the parsed v2 `ErrorResult[]` payload
(`[{"identifier": ..., "errors": [{"code": ..., "message": ...}]}]`) when the 400 body follows that
shape, and is `None` otherwise — e.g. a 5xx with a plain-text body. **Check both fields:**
`error_results` for programmatic handling, `response_body` as the fallback. Include `request_id` in
any report to GS1.

### `WordPressAPIError`

A WordPress REST call returned non-success. Attributes: `status_code`, `response_body`.

- `401` — terminal, never retried. Check the app password quoting trap above, and that the user still
  has the role.
- `409` — slug collision with an existing non-GTIN page (**E11**). Needs human intervention; the tool
  will not guess.
- Also raised when the **WPML helper reports back a translation group different from the one
  requested** — that is the silent-no-op failure mode this integration is most prone to, surfaced as
  a `409` rather than left as a page that looks published but is unreachable in its language.

### `OverwriteError`

`gs1_dl_client.safe_upsert` found an existing Digital Link for the GTIN and `overwrite=True` was not
passed — the GET-before-write guard against clobbering a live resolver target. Attributes: `gtin`,
`existing` (the snapshot that would have been replaced, usable for rollback).

**Fix:** confirm you intend to replace the live target, then pass `overwrite=True`.

### `GtinMismatchError` ✚

A WordPress page exists at the target slug or id but its `meta.gtin` belongs to a **different**
product (**E8**). Attributes: `gtin`, `existing_gtin`, `wp_page_id`.

Deliberately distinct from `WordPressAPIError`: the row is logged and **skipped**, not treated as a
transport failure. Usually means a slug-pattern collision between two products.

### `TemplateError`

A template could not be resolved or rendered — missing file, or none of the override/client/default
candidates exist. See [`template-variables.md`](template-variables.md).

### `StateError`

The state file could not be loaded, parsed, or written. Exit 2.

Note the split from **E19**: *corrupt JSON* is recovered from (backed up, fresh state, loud warning).
`StateError` is the *environmental* fault — permissions, I/O — and is fatal.

### `WebsiteStatusError` ✚

The operator control file (`input/{client_id}/website_status.xlsx`) is missing, unreadable, or lacks
a required column. Treated like `ConfigError` — exit 2 — because it gates which products are eligible
for publication.

**Fix:** check `website_status.path` and that the `gtin_column` / `on_website_column` names match the
actual sheet.

### `GeneratorError` ✚

`generated_cache.json` is corrupt or unwritable, or a producer result failed validation (for example
empty bullet lists). Like `StateError`, a between-runs artifact whose corruption the operator must
see.

**Fix:** inspect the cache, or delete it and re-run `run_generate --emit` to regenerate. Never
hand-edit it into a shape that fails the contract.

### `LLMAPIError` ✚

The Anthropic Messages API failed, or returned a 200 whose body lacks the forced `produce_copy` tool
call. Attributes: `status_code` (`0` for transport failure), `response_body`.

Only reachable via `run_generate --backend api`. The in-session producer (`--emit` / `--ingest`)
needs no API key and cannot raise this.

---

## HTTP outcomes

Condensed from `IMPLEMENTATION_SPEC.md` §5.1. 429 and 5xx have independent retry budgets with
exponential backoff.

| Layer | Status | Action | Retries |
|---|---|---|---|
| GS1 | 2xx | success | — |
| GS1 | 400 "No valid contract found" on **GET** | treated as not-found, `get()` → `None` | none |
| GS1 | 400 / 401 / 403 otherwise | `GS1APIError` | none |
| GS1 | 404 on GET | not-found → `None` | none |
| GS1 | 404 on POST, 409 | `GS1APIError` | none |
| GS1 | 429 | back off | up to 5 |
| GS1 | 5xx, timeout | exponential retry | up to 3 |
| WordPress | 2xx | success | — |
| WordPress | 400 / 401 / 403 | `WordPressAPIError`, **terminal** | none |
| WordPress | 404 on GET lookups | `None` | none |
| WordPress | 409 slug conflict | `WordPressAPIError` — needs human | none |
| WordPress | 429 | back off | up to 5 |
| WordPress | 5xx | exponential retry | up to 3 |
| WP verify URL | anything but 2xx/3xx | `WordPressAPIError` | none |

**Run-level policy (§5.3):** per-row failures are recorded as `RunOutcome(status="error")` and the
loop continues. Exit 0 if every row succeeded, 1 if any errored; state is saved with partial results.
Startup config/credential errors abort immediately with exit 2.

---

## Edge case inventory

`IMPLEMENTATION_SPEC.md` §7, with the handling location. Everything here is intended behaviour.

| # | Condition | Behaviour |
|---|---|---|
| E1 | GTIN with leading zeros | Preserved, never silently stripped |
| E2 | GTIN as an integer (openpyxl cast) | Coerced to string, zero-padded |
| E3 | Duplicate GTINs in the export | First wins; the rest WARN and are skipped |
| E4 | Empty Excel row | Skipped silently |
| E5 | GTIN with no `product_name` in the default language | `ExportParseError` naming the GTIN |
| E6 | `column_map` target that is not a `ProductRecord` field | `ExportParseError` at config load |
| E7 | `image_url` 404s or times out | Featured media skipped; **page still created**; noted on the `RunOutcome` |
| E8 | Page's `meta.gtin` ≠ row's GTIN | `GtinMismatchError` — log ERROR, skip the row |
| E9 | GS1 upsert succeeds, WP later 500s | GS1 state kept; WP failure logged; run continues |
| E10 | NL succeeds, FR fails | State reflects NL; FR retried next run |
| E11 | Slug collision with a non-GTIN page | `WordPressAPIError` — human intervention |
| E12 | Template uses `{{extras.foo}}`, `foo` absent | Renders empty; WARN once per run |
| E13 | Product data contains `{{` or `}}` | Escaped at insertion. **Never** use triple-brace |
| E14 | GS1 returns 401 mid-run | Row errored; later rows try again |
| E15 | `clients.yml` names an unset env var | `MissingCredentialError` at first API call (lazy) |
| E16 | More columns than `column_map` | WARN per unmapped column |
| E17 | Fewer columns than expected | `ExportParseError` if required, WARN if optional |
| E18 | Language has no `product_name.{lang}` for a GTIN | That language's row classified SKIPPED, surfaced in chat |
| E19 | State file is corrupt JSON | Backed up to `state.json.corrupt.{ts}`, fresh state, ERROR logged, **and the reset surfaced above the plan counts** |
| E20 | Two `run_execute` runs interleave | **Not supported.** No lockfile in v0.1 |
| E21 | Generator on, but a `(GTIN, language)` has no generated tagline | Row SKIPPED so a blank page can never publish; gap reported via `missing_generation_input` |
| E22 | `media.require_hero_image` set, source `image_url` blank | GTIN held out of the plan; reported via `value_blank`. A runtime fetch failure still degrades per E7 |

### E19 in full — why the reset is safe, and why it must stay loud

State is a **cache** of what the tool believes it already did, derivable from the live systems, so
rebuilding it is safe: every write path is idempotent. Without a known page id, `upsert_page` still
matches the live page by slug then `meta.gtin` and updates in place (no duplicates); `safe_upsert`
reads before it writes; `render_qr` is byte-deterministic. A reset costs redundant work, not
corruption — which is why aborting would be the wrong trade.

But a reset also **reclassifies every row as NEW**, silently turning an incremental re-run into a
full rewrite of live pages and resolver targets. An ERROR in the log is too quiet for that — the
operator is reading the chat, not stderr. So `load_state` sets `State.reset_from_corrupt`, `run_plan`
leads its summary with a warning, and `flow-orchestrator` surfaces it **above** the counts. The
confirmation gate is what makes the reset safe in practice, and it only works if the operator is
told.

---

## Rollback

v0.1.0 implements Levels A and B (§5.4).

**Level A — structured logs + manual rollback.** Every mutating operation writes a `RunOutcome` to
`output/{client_id}/runs/{ts}.jsonl`. WordPress pages revert through the admin UI (revisions are
preserved). GS1 entries revert via MyGS1 or a re-run. QR files are overwritten by re-runs.

**Level B — prevent rather than recover.** `run_plan` produces `plan.json`; the skills show it in
chat before executing; `--dry-run` walks the whole plan replacing every mutating call with a log
line. Plus two GET-before-write guards: `OverwriteError` on the GS1 side, `GtinMismatchError` on the
WordPress side.

**Level C — snapshot and automated rollback — is not implemented.** `state.json` carries
`content_hash` and `gs1_link_set_hash`, enough for change detection but **not** previous-value
preservation. That is the gap.

Practical rollback for one product:

```bash
python -m scripts.run_unpublish {client_id} --gtin {gtin} --dry-run   # preview
python -m scripts.run_unpublish {client_id} --gtin {gtin}             # retract + draft pages
```

This retracts the Digital Link and drafts the pages, and classifies the GTIN as HELD so a later run
will not republish it. The GS1 record itself remains on the account, deactivated, forever.
