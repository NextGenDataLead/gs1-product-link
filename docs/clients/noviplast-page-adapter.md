# Noviplast Page Adapter — Design

> Status: **design / not yet built.** This specifies a Noviplast-specific WordPress
> page-building adapter, discovered during live pilot reconnaissance (July 2026). It is
> **new scope** on top of the completed Phase 7 (plan / change-detection / flow-orchestrator),
> which stands unchanged underneath it.

## 1. Why this exists

The generic model from Phase 5 — *render an HTML template into `post_content`* — **does not
fit Noviplast.** Their product pages are built with **Oxygen Builder** reading **ACF fields**;
`post_content` is empty and ignored by the theme. The site also runs **WPML** (not Polylang, as
`clients.yml` currently says) and a custom `noviplast` post type. So publishing a correct page
means **populating the specific ACF fields the Oxygen template reads**, in **both nl and fr**,
sourced from the **GDSN datasource export** plus a small amount of non-feed content.

This adapter replaces the HTML-into-body approach *for Noviplast*.

## 2. Discovery summary (verified live against `www.noviplast.nl`)

- **Auth:** dedicated `automation-bot` user (role **Editor**) with an Application Password works.
  Wordfence had "Disable application passwords" enabled; the site admin turned it off.
- **CPT REST:** `noviplast` is now REST-writable via a mu-plugin/Code-Snippet that forces
  `show_in_rest` + registers the `gtin` post meta. (See §7.)
- **Multilingual:** the site runs **WPML** (`wpml/v1`, `wpml/st/v1`, `wpml/tm/v1` REST namespaces).
  `clients.yml` says `multilingual_plugin: polylang` — **wrong, must become `wpml`.**
- **Page model:** Oxygen template renders from ACF; `post_content` empty on every published page.
- **URL pattern confirmed correct:** `…/noviplast/{slug}/` (default lang) and `…/fr/noviplast/{slug}/`.
- **Images (`ReferencedFileDetailInformation`):** up to **12 files per product**
  (`ReferencedFileHeader[0..11]`), each documented with URL (2485), MimeType (2602),
  `ReferencedFileTypeCode` (2469), `IsPrimaryFile` (4277), `FileName` (2481),
  `FileSequenceNumber` (4591), plus pixel dimensions, aspect ratio and file size. Measured over
  the pilot export — **375 files across 124 of 127 products**:
  - **All 375 are `PRODUCT_IMAGE`** → **no videos in the feed** (confirmed).
  - **92% are `image/tiff`** (345); only 16 PNG + 14 JPEG. **322 files exceed 10 MB** (up to 45 MB,
    3200×3200 print masters). WordPress will not accept TIFF, so files **must be converted +
    resized** before upload.
  - **Only 48 of 124 products have `IsPrimaryFile = TRUE`**; view codes are mostly the C-series
    (`C1L1`, `C1R1`, `C1R0`, `C1C0`…) with only 14 `A1N0` front shots. **76 products have neither a
    primary flag nor an A1N0** → the hero image must be chosen by a deterministic fallback.
- **Control file** (`input/noviplast/website_status.xlsx`): Strict OOXML, header on row 4, data on
  sheet "Blad1", 13-digit barcodes. Its `Categorie` column is a **temporary personal action tracker**
  (`webpage + QR`, `GS1 + webpage + QR`, `QR only`, `moet niet`, `mag weg`) — **not** a product
  category, and absent from future exports; the tool does not use it.

## 3. ACF fields on a Noviplast product page

Read live via a temporary `get_fields()` debug route (remove after mapping):

| ACF field | Holds | Source |
|---|---|---|
| `product_title` | tagline (e.g. "Reinigingssticks voor je afvoer") | **not in GDSN** (open, §6) |
| `product_description` | one HTML blob: tagline + **Eigenschappen** + **Technische details** bullets | `TradeItemFeatureBenefit` + LLM split |
| `product_header_image` / `product_regular_image` | hero + main image | GDSN primary image |
| `product_gallery` | repeater `{product_gallery_image}` | additional GDSN images |
| `product_header_video_file` / `_text` | product video + caption | video folder / not in GDSN |
| `is_new_product` | "new" flag | n/a (leave default) |

The Oxygen template is a **single fixed group** across categories (verified on *keuken* and
*doe-het-zelf*) — no per-category ACF variation.

## 4. Data map: page element → storage → source → transform

| Page element | Stored in | GDSN / other source | Transform |
|---|---|---|---|
| Product name (heading) | WP **post title** | `TradeItemDescription` (nl/fr) | **strip leading `"Noviplast "`** |
| Tagline | ACF `product_title` + `product_header_video_text` | **open** (§6) | — |
| Eigenschappen + Technische details | ACF `product_description` (HTML, per language) | `TradeItemFeatureBenefit` (repeatable, nl/fr) | **LLM classifies each item** into the two buckets; render as HTML |
| Main image | featured media + `product_header_image` + `product_regular_image` | GDSN referenced files — hero selected by `IsPrimaryFile` → view code → sequence | download → **convert/resize (TIFF→web)** → upload |
| Gallery images | ACF `product_gallery` | remaining GDSN referenced images | download → **convert/resize** → upload → repeater rows |
| Video | ACF `product_header_video_file` | **media folder**, file named `{gtin}*` | match by GTIN prefix → upload |
| Category | `noviplast-categories` term | **GPC brick code** → category map | lookup table (§5) |
| GTIN | post meta `gtin` | GTIN | direct |
| GS1 Digital Link + QR | GS1 resolver + QR files | GTIN + page URL | existing pipeline |
| Page body (`post_content`) | — | — | **left empty** (Oxygen-driven) |

## 5. Component decisions

1. **Eligibility (unchanged):** a product is a candidate when it is already in GS1 **and** not yet on
   the website (control-file `Al in GS1` filled + `Momenteel op Website` blank). The `Categorie`
   action column is **not** used.
2. **Draft-first:** the tool creates each page as a **draft**. A marketer completes the tagline and
   any media the feed can't supply, then publishes. The tool never auto-publishes marketing pages.
3. **Title:** from `TradeItemDescription` per language; strip a leading `"Noviplast "`.
4. **Feature/benefit split (LLM):**
   - Classify **per `TradeItemFeatureBenefit` item** (each item has an nl+fr pair) so nl and fr land in
     the **same bucket** — Eigenschappen (benefit/feature) vs Technische details (spec).
   - **Idempotency:** classify **once** and **cache** the result keyed by the item text, so re-runs
     don't drift and flip the content hash.
   - **Human review:** the proposed split is shown for approval in the flow-orchestrator confirmation
     step before publishing (pilot requirement).
   - Render each bucket as the bulleted HTML that `product_description` expects.
5. **Images — a real pipeline, not a copy.** The GDSN files are **print masters** (92% TIFF, many
   10–45 MB), so "download → upload" is not viable. Required steps:
   1. **Extract** all referenced files per GTIN from `ReferencedFileDetailInformation` (the parser
      currently takes only one — extend it to all 12 slots, keeping URL / mime / `IsPrimaryFile` /
      `FileName` / `FileSequenceNumber`).
   2. **Select the hero** by a deterministic rule, since only 48/124 carry a primary flag:
      `IsPrimaryFile = TRUE` → else preferred GS1 view code (`A1N0` → `C1N0` → `C1C0` → …) → else
      lowest `FileSequenceNumber`. Remaining images → `product_gallery` (ordered by sequence,
      capped at a sensible max).
   3. **Convert + resize** with **Pillow** (already a dependency via `qrcode[pil]`; v12.3 reads
      TIFF): TIFF/PNG → web JPEG or WebP, max ~1600 px, quality ~85 — a 45 MB 3200×3200 master
      becomes a ~200 KB web image.
   4. **Upload** to WP media and assign: hero → `featured_media` + `product_header_image` +
      `product_regular_image`; rest → `product_gallery`. **Dedupe by GTIN + view code** so re-runs
      don't create duplicate attachments.
   - **Caveat:** for the 76 products with no primary flag and no front shot, the chosen hero is a
     best guess from an angled C-series image. Draft-first publishing covers this — the marketer
     can swap the hero before publishing. 3 of 127 products have no images at all.
6. **Video (folder):** a single flat folder, e.g. `input/noviplast/media/`, with files named
   `{gtin}*.mp4`; the tool matches by GTIN prefix, uploads, and sets `product_header_video_file`.
   Products without a matching file simply get no video.
7. **Category:** a **GPC brick → `noviplast-categories`** lookup in `clients.yml`. The parser already
   captures `gpc_brick_code`; the map resolves it to a taxonomy term. Missing/unmapped bricks → leave
   category unset on the draft and warn.
8. **Bilingual (WPML):** create the nl and fr drafts, set each page's language, and link them as
   translations. WPML has **no clean REST endpoint** for language assignment / translation linking
   (its REST API is translation-*workflow* oriented), so this needs a **small server-side helper**
   (custom REST route using WPML's PHP API — see §7).

## 6. Open decisions

- **Tagline source.** Not in GDSN. Options: (a) a column in the control file, (b) LLM-suggested from
  the name/description and human-approved, (c) left blank for the marketer on the draft. **Undecided.**
- **GPC → category mapping table.** Needs to be populated (which bricks map to keuken / tuin /
  doe-het-zelf / dier / schoonmaak / specials).
- **Auto-create missing category terms**, or require them to pre-exist? (Recommend: require pre-exist,
  warn on miss.)

## 7. WordPress-side enablers (onboarding tasks)

- **CPT + gtin meta in REST** — mu-plugin/Code-Snippet. **Done** (this session).
- **ACF field group → Show in REST** (or a write helper) so `product_title`, `product_description`,
  `product_gallery`, image/video fields can be written via REST. **Todo.**
- **WPML helper endpoint** — a custom REST route (mu-plugin) that: sets a post's language, and links a
  set of post ids as one translation group, via WPML's PHP API. **Todo.**
- **GPC → category** map populated; category terms exist. **Todo.**
- Remove the temporary `noviplast-debug/v1/fields` route once mapping is frozen.

## 8. Tool-side work (new development)

- `clients.yml`: `multilingual_plugin: wpml`; add `brick_category_map`, media-folder path, and the
  ACF field-name mapping (so field names live in config, not code).
- `lib/multilingual.py`: implement `WPMLAdapter` against the helper endpoint (replaces the
  `NotImplementedError` stub).
- `lib/wp_client.py`: ACF-field writes; media upload **from a URL** and **from a local file**; set
  language + link translations via the WPML helper.
- `lib/gdsn.py` / parser: extract **multiple** referenced images; expose `TradeItemFeatureBenefit` as a
  repeatable nl/fr list.
- New **feature-benefit classifier** (LLM) with a deterministic cache.
- A **Noviplast page-build step** that assembles the above into the ACF payload — replacing the
  Phase 5 HTML-template render for this client.

## 9. Relationship to the rest of the project

Phase 7 (`run_plan`, `diff_against_state`, `flow-orchestrator`) is complete and committed and is the
engine underneath this: it still computes *what* to act on. This adapter changes only **how the
WordPress page is built** for Noviplast, and adds the media/WPML/LLM pieces the real site requires.
It is effectively a new phase of work and should be planned and specced as such before coding.
