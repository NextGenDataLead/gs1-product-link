# Resume prompt — Noviplast page adapter

Paste the block below into a fresh session **in normal mode** (not plan, not auto-accept):
it is an orientation brief, and the project writes to a live WordPress site and the GS1
**production** resolver, so each step wants review.

Disposable — rewrite it once the state it describes is stale.

---

````
Resuming the GS1 Digital Link Orchestrator — Noviplast pilot page adapter.
Fresh context; everything below was verified live, but re-verify before trusting it.

## What we do first, before any code
**Walk the fields the website actually needs, one at a time. I will tell you which source
field each one must come from — do not infer it.** That is the whole method, and it is the
lesson of the tagline (below): the mapping was inferred from attribute names and docs, and
it was wrong for a year's worth of reasoning. Ask, one field at a time.

Anything that goes wrong while building pages — a blank, or the **same language holding
different values in different target markets** — goes into a **data-quality report** to
assess *after* the run, not something to fix mid-flight or code around. The datapool is
authoritative; the tool reports, it does not repair.

## Read first, in this order
1. `docs/clients/noviplast-page-adapter.md` — the live-verified design. §3.1 (write
   sequence + **five** silent traps), §4.1, §4.2 (the tagline — now RESOLVED, see below),
   §7 (WP enablers, all done), §8 (tool-side work + the bugs fixed 2026-07-17).
2. `git log --oneline dbf29c8..HEAD` on branch `phase-7-audit-fixes` — ~28 commits,
   **unpushed**. `main` untouched at `dbf29c8`. The messages carry the *why*; several are
   non-obvious and worth reading before touching that code.
3. `/Users/idekker/.claude/plans/flickering-frolicking-manatee.md` — the drafted (not
   approved) plan for ranked-market language resolution + the blanks report, with every
   measurement behind it. Supersede it freely; the numbers in it are real.

## The one thing that matters most
**This integration fails silently.** Ten separate defects now, every one returning 200/201
while doing nothing or the wrong thing, every one passing `pytest` and `--dry-run`:
meta.gtin discarded, ACF dropped, the multilingual adapter swapped for a no-op, a slug
lookup returning an unrelated page, one that would have overwritten every Dutch page with
French, a per-language GS1 write that destroyed the other language's link, translations
never linked at all, an unrecognised `linkType`, an accountNumber that wasn't ours, and a
stale `products.json` silently feeding the planner.

**Verify against the live site, not against green tests.** Every one of those was found by
reading back from production, never by a test. Don't trust a schema that says a field is
writable, or a doc that says where a value comes from — write it and read it back.

## RESOLVED: the tagline is not in the GDSN feed (2026-07-17)
§4.2 asserted `TradeItemMarketingMessage` (attr **1083**) was the tagline. It is not.
Traced **36 live product pages** back to the export — **all 23 sheets, 55 localised
(sheet, attribute) candidates, 4 markets, both languages**, normalised for `strip_prefix`
and HTML entities:

  ACF tagline (product_title):  34 of 36 -> NOT IN DATASET.  2 match 1083.
  Page title:                   21 of 36 -> not in dataset; 6 -> 3318, 4 -> 3309, rest mixed

So §3's original *"not in GDSN (open)"* was right and §4.2 overrode it wrongly. **There is
no better attribute** — the "we linked the wrong column" hypothesis is refuted by
exhaustive search, not by argument. 1083 is a marketing message that coincides with a real
tagline twice in 36. That is why 107/127 are `value_too_long`: we were measuring the wrong
field, and no amount of MyGS1 editing would have fixed it.

**Open decision:** unwire `acf_map: product_title <- description_short` (§4.2 option (d) —
tagline becomes client/generated content, 1083 kept as generator *input*, and the 107
findings retire) vs keep it in the slot until the generator lands. **Not yet decided.**

## The pilot is live — and the user wants it unpublished
**`08713195000527` (*Microvezeldoek stof*)** is the first product this tool has published:
pages **1447** (nl) / **1448** (fr), same slug `p-08713195000527`, linked as translations
(`trid` 626), GS1 **enabled** with exactly 2 links (nl default only), idempotent on re-run.
  https://www.noviplast.nl/noviplast/p-08713195000527/
  https://www.noviplast.nl/fr/noviplast/p-08713195000527/

It carries **title + tagline only** (no description/images/category), and its tagline comes
from 1083 — i.e. the wrong field, per above. **The user asked to unpublish it** after
inspecting it visually. Not yet done. Recommended: draft both pages **and** `retract()` the
resolver — a drafted page with an enabled Digital Link resolves to a **404**, because
`verify_url` proved a draft is not publicly reachable.

## Four bugs fixed 2026-07-17 (all live-verified)
- **GS1 link set was written per language.** CreateOrUpdate **REPLACES** the links array
  (proved by probing `08713195000374` with `links: []` -> 0 links), so the fr write
  destroyed the nl link. `run_execute` now groups by GTIN: per-row upsert/verify, then one
  per-GTIN phase (translations -> a single resolver write -> QR). A sibling failure blocks
  the whole GTIN. Partial confirmations rebuild the missing language from state.
- **`wp.link_translations` was never called** — zero call sites. Same root cause, same fix.
- **`link_type: pip` was unrecognised.** The API stores `linkType` unvalidated: ours read
  back `linkTypeTitle: null`, the MyGS1 UI's as `gs1:pip` / "Product Information Page". Now
  `gs1:pip`. Every link the tool ever wrote had a bad type.
- **`accountNumber` was a guess** (`8713195000008`, derived from the GTIN prefix). The
  production token *claims* **`8719965024137`**, as does the live record — every prod POST
  carried an account that wasn't ours, and got a 200. **It always comes from the token
  claim.** `clients.yml` is gitignored, so §8 is the only durable record.

`clients.yml` now has `environment: production` on the noviplast block (the default stays
`test` so a new client can't reach production by omission).

## Traps — read before verifying anything
- **Re-run `parse_export` before `run_plan`.** `run_plan` reads `products.json` off disk and
  cannot date it. The pilot's first plan carried *"Noviplast Microvezeldoek stof"* because
  the artifact predated `strip_prefix` — which worked fine. Tell: `source_issues.json` was
  absent while `products.json` was not, and the same run writes both.
- **Never verify ACF via a language-scoped collection query.** `?slug=…&lang=fr` returns
  `acf.product_title: null` for the non-default language *even when stored correctly*; a
  direct `GET /noviplast/{id}` shows it. Cost a false alarm on the live run. **Read ACF back
  by page id.** (§3.1 finding 5 — the mirror of the `?lang=`+acf write trap.)
- **`scripts/inspect_export.py` is stale and misleading.** `_KNOWN_ATTRIBUTES` maps
  `3297 -> product_name` and `3318 -> description_long`. Both are known-wrong (`clients.yml`
  says 3297 is an internal logistics string; 3318 is the name, fixed in `c76492b`), and 1067
  is absent. The tool `clients.yml` points operators at for column discovery would
  re-introduce the bug the project already fixed. Fix it before trusting any mapping.
- **`.env`: keep `NOVIPLAST_WP_APP_PASS` single-quoted.** WP app passwords contain spaces;
  unquoted it loads *empty* and everything 401s while looking like a permissions problem.

## Measured facts about the export (2026-07-17, real data)
- 4 target markets: **056** (BE), **276** (DE), **442** (LU), **528** (NL). Sheets are keyed
  `Gtin` + `TargetMarketCountryCode`; a GTIN recurs once per market. `build_records` already
  dedupes to **one ProductRecord per GTIN** (127), so plans never carry duplicate GTINs.
- **Every market row carries both nl and fr.** The `market_language: {528: nl, 056: fr}` map
  is an invented 1:1 constraint; it costs coverage (product_name fr 124/127, vs 127/127 if
  any market may supply it).
- **19 products have 2+ markets carrying both languages with different text**, and no market
  is uniformly better: 528 wins `…0473` (056's Dutch is English), 056 wins `…4181` (528's
  Dutch slot holds French), and `…7496`'s two markets describe apparently different
  products. So resolution must be **deterministic and ranked** — "irrespective of country
  code" cannot decide these. These are exactly the "inconsistent values" for the report.
- Report scale: **423 blanks** across 1,795 existing (gtin, market, field) rows;
  `description_long` is 347 of them. 745 combinations have **no row at all** — not a gap.
- **`market_language` does two jobs.** Beside `lang_to_market` it computes `primary_market`,
  which picks the row for **scalars** (`brand`, `net_content`, `gpc_brick_code`,
  `image_url`). Remove the map and those have no row. A ranked `market_priority` list serves
  both.
- **`tests/lib/test_gdsn.py`'s fixture contradicts the real export** (528 = nl+de only,
  056 = fr only). Tests on it cannot catch this class of bug. Fix the fixture first and see
  what fails — that is the real blast radius.

## State
- 265 tests pass; ruff clean; `mypy --strict lib/ scripts/ tests/integration/` clean.
  `mypy --strict .` has **35** pre-existing errors in 3 test files — compare, don't chase.
- `pytest` deselects staging via `addopts = "-m 'not staging'"`. Both staging files now
  clean up in a `finally` and are guarded three ways (8713195 prefix; a live pre-flight; and
  the export's product list — the first two both pass a real product, proved by
  `08713195000374`). The GS1 record still **cannot be deleted**, only disabled.
- `parse_export` writes `output/noviplast/data/source_issues.json` — the MyGS1 work queue.
  111 findings: 107 `value_too_long` (**measuring the wrong field — see the tagline above**),
  4 `brand_prefix_mismatch`.

## Still blocked on the client
1. **GS1 DIY sector datamodel** — operator must supply the file. Carries the H87 ->
   "stuks"/"pièces" decoding; `net_content` currently parses as the raw code ("5 H87").
2. **Four brand typos** in the datapool — GTINs are in `source_issues.json`.

Ask before starting anything that writes to production.
````
