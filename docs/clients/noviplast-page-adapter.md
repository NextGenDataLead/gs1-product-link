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
| Product name (heading) | WP **post title** | `TradeItemDescription` — **attr 3318**, nl/fr | **strip leading `"Noviplast "`** |
| Tagline | ACF `product_title` + `product_header_video_text` | `TradeItemMarketingMessage` — **attr 1083**, nl/fr (113/127 nl, 112/127 fr) | use as-is |
| Eigenschappen + Technische details | ACF `product_description` (HTML, per language) | **LLM-generated** from the marketing message + net content / dimensions / material — `TradeItemFeatureBenefit` covers only **6/127** products | generate → **human-approve** → render as HTML |
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
3. **Title — `TradeItemDescription` (attr 3318).** Per language, minus a leading `"Noviplast "` (brand
   is a separate field, `BrandName` 3336). **Fixed in `clients.yml`** — `product_name` was bound to
   `DescriptionShort` (3297), an internal logistics string (*"Schroefverwijderaar metaal grs"*); 3297
   is now carried in `extras.logistics_name` instead.
   - **Data-quality caveat:** attr 3318 can differ per `TargetMarketCountryCode`. For **121 of 124**
     products the nl value is identical across BE (056) and NL (528), but **3 diverge** — e.g.
     `08713195000473`: BE-nl = *"Noviplast Screw Remove Tool"* (the name on the live page) vs NL-nl =
     *"Noviplast Schroefverwijderaar metaal grijs"*. `market_language` maps `528 → nl`, so the tool
     takes the NL row. These 3 are **Noviplast datapool corrections**, not a tool rule — the tool
     follows the feed.
4. **Tagline.** `TradeItemMarketingMessage` (**attr 1083**), per language. Verified exactly against the
   live page (`08713195000473` nl → *"Verwijder makkelijk beschadigde schroeven"*, the page's tagline).
   Coverage: **113/127 nl, 112/127 fr**. Products without one leave the tagline empty for the marketer.
5. **Eigenschappen / Technische details — LLM *generation*, not classification.**
   `TradeItemFeatureBenefit` (attr 1067) is populated for only **6 of 127** products, so it cannot be
   the source. Instead:
   - **Generate** both bullet lists with an LLM from the **marketing message + net content +
     dimensions + material** (and `TradeItemFeatureBenefit` when it happens to exist), per language —
     Eigenschappen (benefits) and Technische details (specs).
   - **Human-approve** every generated block before publishing — it is marketing copy on a live site.
     This slots into the flow-orchestrator confirmation step; draft-first publishing backstops it.
   - **Idempotency:** generate **once** and **cache** the result keyed by the source inputs, so re-runs
     don't drift and flip the content hash.
   - Render each bucket as the bulleted HTML `product_description` expects.
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
7. **Category → deferred to its own phase (Phase 7.5).** Derived from the GPC brick via the **GS1 DIY
   sector datamodel**. It is not a simple lookup, which is why it gets a phase of its own:
   - **~70 distinct bricks** across the 127-product pilot export (many singletons).
   - **Bricks span categories** — e.g. `10003865` holds garden tools *and* a nutcracker (keuken). A
     pure brick map will misclassify, so a **per-GTIN override list** is required.
   - **The client's categorisation is not purely semantic** — *Power Splash* is a shower head, filed
     under **keuken** on the live site. No inferred map reproduces that; it needs client sign-off.
   - **Lighting (~20 products, 6 bricks)** has no category of its own and falls into `specials`.
   - Output: `brick_category_map` + per-GTIN overrides in `clients.yml`. Unmapped bricks **warn and
     leave the category unset** — never guess. Terms: `keuken`, `doe_het_zelf`, `schoonmaak`, `tuin`,
     `dier`, `specials`.
8. **Bilingual (WPML):** create the nl and fr drafts, set each page's language, and link them as
   translations. WPML has **no clean REST endpoint** for language assignment / translation linking
   (its REST API is translation-*workflow* oriented), so this needs a **small server-side helper**
   (custom REST route using WPML's PHP API — see §7).

## 6. Open decisions

- ~~Tagline source~~ — **resolved:** `TradeItemMarketingMessage` (attr 1083), verified against the live page.
- ~~Feature/benefit source~~ — **resolved:** LLM-**generated** from the marketing message + net content /
  dimensions / material, human-approved (the feed covers only 6/127).
- **GPC brick → category mapping** — **deferred to Phase 7.5** (GS1 DIY sector datamodel; see §5.7).
- **Auto-create missing category terms**, or require them to pre-exist? (Recommend: require pre-exist,
  warn on miss.) — settle in Phase 7.5.
- **LLM provider/prompt + cache location** for the bullet generation — settle when that phase is planned.

## 7. WordPress-side enablers (onboarding tasks)

- **CPT + gtin meta in REST** — mu-plugin/Code-Snippet. **Done** (this session).
- **ACF field group → Show in REST** (or a write helper) so `product_title`, `product_description`,
  `product_gallery`, image/video fields can be written via REST. **Todo.**
- **`noviplast-categories` taxonomy → `show_in_rest`.** Currently 404s on the REST API, so the tool
  cannot read or assign category terms. Add to the same enabler snippet. **Todo.**
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
- `lib/gdsn.py` / parser + `clients.yml` `gdsn_map`:
  - **fix `product_name` → `TradeItemDescription` attr 3318** (currently `DescriptionShort` 3297 — wrong);
  - add `marketing_message` → attr **1083** (the tagline), localised;
  - extract **multiple** referenced images (all 12 slots, with mime / `IsPrimaryFile` / `FileName`);
  - expose `TradeItemFeatureBenefit` (1067) as a repeatable nl/fr list (sparse, but use it when present).
- New **feature/benefit generator** (LLM) with a deterministic cache + human-approval gate.
- New **image pipeline**: download → convert/resize (Pillow) → upload → dedupe.
- A **Noviplast page-build step** that assembles the above into the ACF payload — replacing the
  Phase 5 HTML-template render for this client.

## 9. Relationship to the rest of the project

Phase 7 (`run_plan`, `diff_against_state`, `flow-orchestrator`) is complete and committed and is the
engine underneath this: it still computes *what* to act on. This adapter changes only **how the
WordPress page is built** for Noviplast, and adds the media/WPML/LLM pieces the real site requires.
It is effectively a new phase of work and should be planned and specced as such before coding.
