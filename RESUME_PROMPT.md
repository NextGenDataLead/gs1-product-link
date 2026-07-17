# Resume prompt — Noviplast page adapter

Paste the block below into a fresh session **in normal mode** (not plan, not auto-accept):
it is an orientation brief, and the project writes to a live WooCommerce store and the GS1
**production** resolver, so each step wants review. Switch to plan mode afterwards if you
want a reviewable plan for the next chunk.

Disposable — delete or rewrite it once the state it describes is stale.

---

````
Resuming the GS1 Digital Link Orchestrator — Noviplast pilot page adapter.
Fresh context; everything below is verified, but re-verify before trusting it.

## Read first, in this order
1. `docs/clients/noviplast-page-adapter.md` — the live-verified design. Especially
   §3.1 (write sequence + four silent traps), §4.1 (product_description is three
   parts), §4.2 (OPEN: the tagline source — the main blocker), §6 (open decisions),
   §7 (WP enablers — all done), §8 (tool-side work remaining).
2. `git log --oneline dbf29c8..HEAD` on branch `phase-7-audit-fixes` — 16 commits,
   pushed. `main` is untouched at `dbf29c8`. The commit messages carry the *why* for
   each fix; several are non-obvious and worth reading before changing that code.
3. `docs/IMPLEMENTATION_SPEC.md` §12 for phase DoD (Phase 7's Cowork gate moved to
   Phase 8).

## The one thing that matters most
**This integration fails silently.** Six separate defects in one session returned
200/201 while doing nothing, or the wrong thing — meta.gtin discarded, ACF dropped,
the multilingual adapter swapped for a no-op, a slug lookup returning an unrelated
page, and one that would have overwritten every Dutch page with French. Every one of
them passed `pytest` and `--dry-run`.

So: **verify against the live site, not against green tests.** The pattern that works
is a scratch draft — create → assert → delete in a `finally`. Drafts aren't public, so
it's safe, and it is how all six were found. Don't trust a schema that says a field is
writable; write it and read it back.

## State
- **WordPress side: complete.** CPT, `meta.gtin` (needs `custom-fields` in supports —
  non-obvious), ACF field group, taxonomy, the `rest_noviplast_query` gtin filter, and
  `POST /wp-json/noviplast/v1/translations` are all live via the Code Snippets plugin.
  Sources and rationale in §7.
- **Tool side: plumbing done, content assembly not.** `WPMLAdapter`, the three-call
  write path (`?lang=` create → ACF second call → link), language-scoped lookups, and
  `lib/acf.py` assembly all work and are live-verified end-to-end (distinct pages per
  language, same slug, linked, idempotent re-run).
- `parse_export` writes `output/noviplast/data/source_issues.json` — the operator's
  MyGS1 work queue. Currently 111 findings: 107 `value_too_long`, 4 `brand_prefix_mismatch`.
- 250 tests pass; ruff / ruff format / mypy --strict clean.

## Blocked on the client, not on code — don't code around these
1. **Tagline source (§4.2) — the live question.** GS1 attr 1083 is a *marketing
   message* by definition, not a tagline: fr median 150 chars, 54% over 120, max 1433,
   against a real live tagline of 31. 107 of 127 products are flagged. That scale
   argues the *mapping* is wrong, not the data — which is what §3 said all along
   ("not in GDSN (open)") before §4 asserted otherwise. Four options are in §4.2;
   (d) — treat the tagline as client content owned by the page-build step and use 1083
   as generator *input* — fits §5's draft-first decision best. **Client's call.**
2. **GS1 DIY sector datamodel** — operator must supply the file. Phase 7.5's first DoD
   item; also carries the H87 → "stuks"/"pièces" decoding Technische details needs.
   `net_content` currently parses as the raw UN/ECE code ("5 H87").
3. **Four brand typos** in the datapool — fix in MyGS1; GTINs are in source_issues.json.

## Do not
- ~~Run `pytest -m staging` against production~~ — **fixed.** Both staging files now clean
  up in a `finally` (page force-deleted, GS1 entry retracted), `STAGING_GTIN` has no
  default and is guarded twice (8713195 prefix + a pre-flight that aborts if the GTIN
  already has a page), and `addopts = "-m 'not staging'"` stops a bare `pytest` reaching
  them. Two things to know before running it anyway: it still **publishes** (draft breaks
  `verify_url`, which HEADs unauthenticated), and the **GS1 record cannot be deleted** —
  retract clears + disables it, and a dead record stays on the account forever. So
  `STAGING_GTIN` must still be a GTIN dedicated to smoke testing and nothing else.
- Unquote `NOVIPLAST_WP_APP_PASS` in `.env` — WP app passwords contain spaces, and
  unquoted it loads *empty*, so everything 401s while looking like a permissions problem.
- Run `run_execute` live expecting good pages: `product_description` isn't assembled
  yet (needs the generator + H87), so pages would publish with title + tagline only.

## The pilot is live — one product is published
**`08713195000527` (*Microvezeldoek stof*) is the first product this tool has published**, on
the real site, with an **enabled** GS1 production resolver entry (2026-07-17). Pages 1447
(nl) / 1448 (fr), same slug, linked as translations (`trid` 626), 2 links, nl default only,
idempotent on re-run. It carries **title + tagline only** — no description, images, or
category — a knowing trade to verify the mechanism. `clients.yml` now has
`environment: production` on the noviplast block (the default stays `test`).

Four bugs were fixed getting there, all of which returned 200 while doing the wrong thing:
- **The GS1 link set was written per language**, and CreateOrUpdate **replaces** the array
  (confirmed by probing `08713195000374`: `links: []` → 0 links), so the fr write destroyed
  the nl link. `run_execute` now groups by GTIN: per-row upsert/verify, then one per-GTIN
  phase for translations + a single resolver write. Also `default: true` applied to both
  languages (now nl only), and `per_language` was dead config (deleted).
- **`wp.link_translations` was never called** by the pipeline — zero call sites. nl and fr
  were left as unrelated pages. Same root cause; same fix.
- **`link_type: pip` was unrecognised.** The API stores `linkType` unvalidated: ours read
  back with `linkTypeTitle: null`, the UI's as `gs1:pip` / "Product Information Page". Now
  `gs1:pip`. Every link the tool ever wrote had a bad type.
- **`accountNumber` was a guess** (`8713195000008`, from the GTIN prefix). The token claims
  **`8719965024137`**, as does the live record — every prod POST carried an account that
  wasn't ours. It always comes from the token claim. `clients.yml` is gitignored, so §8 is
  the durable record.

## Two traps worth knowing before you verify anything
- **Re-run `parse_export` before `run_plan`.** `run_plan` reads `products.json` off disk and
  cannot date it. The pilot's first plan carried *"Noviplast Microvezeldoek stof"* because
  the artifact predated `strip_prefix` — which was working fine. The tell: `source_issues.json`
  was absent while `products.json` was not, and the same run writes both.
- **Never verify ACF via a language-scoped collection query.** `?slug=…&lang=fr` returns
  `acf.product_title: null` for the non-default language *even when the value is stored*; a
  direct `GET /noviplast/{id}` shows it. This produced a false alarm on the live run. Read
  ACF back **by page id**. (§3.1 finding 5 — the mirror of the `?lang=`+acf write trap.)

## Next, once unblocked
§8's remaining tool-side work: the LLM generator (Eigenschappen bullets ~121 products
+ ~14 missing taglines; deterministic cache + human-approval gate; every generated
value reported into source_issues.json for upstream entry), the image pipeline
(TIFF→web; 322 files exceed 10MB), and E18's change of meaning (a missing
product_name.{lang} should now be planned-and-generated, not skipped).

Ask before starting anything that writes to production.
````
