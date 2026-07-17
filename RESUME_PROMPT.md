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

## Known bug, not yet fixed
**The GS1 link set is written per language.** `_build_links` (`run_execute.py:103`) uses
`row.language`, and rows are per-(GTIN, language), so a nl+fr GTIN gets two upserts each
sending a *one-element* `links` array as the whole record. Depending on whether GS1's
CreateOrUpdate replaces or appends, the fr write either **wipes the nl link** (and reports
`ok`) or **duplicates**. Which one is unknown, and it cannot be hedged — sending the full
`[nl, fr]` is right under replace and wrong under append. Also: `default: true` applies to
both languages (should be nl only), and `per_language: true` is dead config. Hasn't fired
yet only because `clients.yml:9` still points at the contract-less sandbox. See §8.

## Next, once unblocked
§8's remaining tool-side work: the LLM generator (Eigenschappen bullets ~121 products
+ ~14 missing taglines; deterministic cache + human-approval gate; every generated
value reported into source_issues.json for upstream entry), the image pipeline
(TIFF→web; 322 files exceed 10MB), and E18's change of meaning (a missing
product_name.{lang} should now be planned-and-generated, not skipped).

Ask before starting anything that writes to production.
````
