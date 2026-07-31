# Announcement draft — v0.1.0

**Status: DRAFT. Nothing here has been published.** Phase 11's DoD asks for a drafted
announcement; this is it. Choose a channel, cut it to length, and check the claims below before it
goes anywhere.

---

## The short version (release notes / repo description)

> **GS1 Digital Link Orchestrator v0.1.0** — turn a GS1 Data Source (GDSN) export into GS1 Digital
> Link QR codes and multilingual WordPress product pages.
>
> Point it at your datapool export and it parses the products, generates page copy, plans exactly
> what will change, and — after you approve it — publishes the pages, registers the Digital Link
> records, and verifies that each QR resolves to the right page in the right language.
>
> Proven on a live pilot: 10 products published to a production site in Dutch and French, each with
> an enabled GS1 production record, every QR resolving, one confirmed scanning off a printed label.

## The longer version (blog post / LinkedIn)

### The problem

A GS1 Digital Link QR code on a package is a promise: scan it and you land on a page about *that*
product, in *your* language. Keeping that promise for a full catalogue is tedious in a way that
punishes manual work. The product data already exists in the GS1 datapool export — but somebody has
to turn each row into a page, register each Digital Link, and check that every code actually
resolves. Do it by hand across two languages and a hundred products and the mistakes are not
dramatic, just quiet: a French page with a Dutch title, a QR pointing at a draft, an image the CMS
silently refused.

### What this does

It runs the whole path from export to resolving QR code, and it is built to be interrupted:

- **Parse** the GDSN export — a 24-sheet datapool file, not a tidy article list — resolving each
  field across target markets and languages.
- **Generate** page copy from the feed attributes, either in-session or through the API, with any
  claim written beyond the literal source text flagged for a human to check.
- **Plan** — a diff of exactly what would change, per product and per language, before anything is
  written.
- **Execute** — publish the pages, upload and convert the media, register the Digital Link records,
  then verify the rendered HTML rather than trusting a status code.
- **Report** — a data-quality worklist grouped by who can fix each item and whether it blocks
  publishing.

### What it refuses to do

The guardrails are the part worth talking about, because every one of them exists because something
went wrong or nearly did:

- A real run against a production account is **refused outright** unless you pass
  `--i-understand-production`. Review gates that live only in an operator flow are bypassed the
  moment someone calls the script directly.
- A product with no generated copy, or no hero image when you require one, is **held** rather than
  published as a convincing-looking blank page.
- Media failures degrade to a published page — the image is never allowed to stop the product.
- A GS1 Digital Link record **cannot be deleted**; the API has no DELETE. The most any retraction
  does is clear its links and disable it, so a mistake is permanent. The tool treats every write to
  a real GTIN as irreversible, because it is.
- The test suite will not touch live services by accident. Credentials reach the scripts but never
  the tests — enforced by an assertion, not by convention.

### Where it is honest about its limits

One pilot, one catalogue, one WordPress site. The WordPress side assumes ACF and WPML. The client
configuration is per-project YAML, not a general integration layer. It is a working tool with real
mileage, not a finished product.

---

## Before publishing this — check

- [x] **Rotate the WordPress application password first.** Done 2026-07-30 — reissued, `.env`
      updated, old one revoked and verified dead (`OPEN_DECISIONS.md`).
- [ ] Confirm the pilot client is happy to be named. This draft deliberately does not name them.
- [ ] Re-check `docs/costs.md` against the current GS1 NL tariff page — pricing claims age badly.
- [x] Decide OD-2 (publish the MCP servers or not). Decided 2026-07-31: **keep them private.** The
      draft says so in *Limits*; do not add an install line for them.
- [ ] The repository is public. Nothing above should reveal a GTIN, a URL, or an account number
      that is not already public.
