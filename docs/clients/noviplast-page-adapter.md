# Noviplast Page Adapter — Design

> Status: **design / tool-side not yet built.** This specifies a Noviplast-specific WordPress
> page-building adapter, discovered during live pilot reconnaissance (July 2026). It is
> **new scope** on top of the completed Phase 7 (plan / change-detection / flow-orchestrator),
> which stands unchanged underneath it.
>
> **The WordPress-side enablers in §7 are now live** (CPT, `meta.gtin`, ACF fields and taxonomy all
> REST-exposed) — except the **WPML helper endpoint**, which remains the last WP blocker and fails
> every `fr` row until it exists. The tool-side work in §8 is unstarted: `clients.yml` still says
> `multilingual_plugin: polylang`, `WPMLAdapter` is still a `NotImplementedError` stub, and
> `wp_client` still writes `post_content` (which this theme ignores). **Until §8 lands, a live
> `run_execute` would publish blank pages** — Oxygen renders from ACF, so template HTML into
> `post_content` produces a page that returns 200, passes `verify_url`, reports `ok`, and shows
> nothing to the customer.

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
  Wordfence had "Disable application passwords" enabled; the site admin turned it off. Verified live:
  `GET /wp/v2/users/me?context=edit` → 200, role `editor`, with `edit_posts`, `publish_posts`,
  `upload_files`, `edit_others_posts`, `unfiltered_html`. **Use `context=edit` when checking** —
  `context=view` omits `capabilities` entirely and makes a working account look permissionless.
  In `.env`, `NOVIPLAST_WP_APP_PASS` **must be single-quoted**: WP app passwords contain spaces, so an
  unquoted value breaks `source .env` at the first space and the variable loads *empty* — the run then
  fails with blank credentials rather than a clear error.
- **CPT REST:** `noviplast` is REST-writable, and `meta.gtin` + the ACF fields + the
  `noviplast-categories` taxonomy are all exposed, via one Code Snippet. Getting `meta.gtin` to
  appear required adding **`custom-fields`** to the CPT's supports — see §7, including the silent
  failure it caused.
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

Read live via a temporary `get_fields()` debug route (remove after mapping — §7). The list below
is the **full set** the group exposes, cross-checked against the CPT's REST write schema now that
the field group is `show_in_rest` (§7) — the two agree exactly, 9 fields.

| ACF field | Holds | Source |
|---|---|---|
| `product_title` | tagline (e.g. "Reinigingssticks voor je afvoer") | **not in GDSN** (open, §6) |
| `product_description` | one HTML blob: tagline + **Eigenschappen** + **Technische details** bullets | `TradeItemFeatureBenefit` + LLM split |
| `product_header_image` / `product_regular_image` | hero + main image | GDSN primary image |
| `product_gallery` | repeater `{product_gallery_image}` | additional GDSN images |
| `product_header_video_file` / `_text` | product video + caption | video folder / not in GDSN |
| `product_header_video` | **unmapped** — empty (`""`) on the sampled live page; distinct from `product_header_video_file` (which holds the attachment object). Purpose unconfirmed | leave untouched until confirmed |
| `is_new_product` | "new" flag | n/a (leave default) |

The Oxygen template is a **single fixed group** across categories (verified on *keuken* and
*doe-het-zelf*) — no per-category ACF variation.

**Write-format note:** values read back are *not* the shape ACF expects on write.
`product_header_video_file` reads as a full attachment object (`{"ID": 1351, "title": …}`) but is
written as an attachment **id**; `product_header_image` / `product_regular_image` read as URL
strings; `is_new_product` reads as `[]` rather than a boolean/null. Confirm each field's write
shape against a scratch draft before trusting a read-back round-trip.

### 3.1 The required write sequence (verified live)

Three findings from scratch drafts against `www.noviplast.nl`, each of which fails **silently**:

1. **Both languages request the same slug** (`slug_pattern: p-{gtin}` has no language component).
   Created without a language, the second page collides and WordPress dedupes it to `p-{gtin}-2` —
   so the French page lands at `/fr/noviplast/p-{gtin}-2/` while `target_url_pattern` builds
   `/fr/noviplast/p-{gtin}/`, and **the GS1 resolver would point every French QR at a 404**.
   Passing **`?lang={lang}` on create** fixes it: WPML scopes slug uniqueness per language, and
   both pages keep `p-{gtin}`.
2. **`?lang=` and `acf` in the same create request are incompatible.** The ACF values are
   **silently dropped** — `201 Created`, fields empty, no error. Measured: create without `?lang`
   + acf → value persists; create with `?lang=fr` + acf → empty; create with `?lang=fr` then acf in
   a second request → value persists. Left unhandled this yields French pages with the correct URL
   and no content, which `verify_url` passes as `ok`.
3. **WPML does not copy ACF across the pair** (the fields behave as *Vertalen*, not *Kopiëren*):
   re-saving the nl page's ACF leaves the fr page's values intact. No `wpml-config.xml` override is
   needed, and there is no clobbering risk.
4. **Lookups must be language-scoped, or they destroy data.** An unscoped collection query answers
   for the **default language only**: verified live, `?slug=p-X` returned the nl page while the fr
   page was invisible without `&lang=fr`. Since both pages share the GTIN-derived slug *and* the
   same `meta.gtin`, the fr row's lookup finds the **nl** page, the E8 guard passes (the GTIN does
   match), and the nl page is **overwritten with French** — no fr page is created and the row
   reports `ok`. This is the only finding here that destroys correct data rather than merely
   failing, and no unit test can catch it: it needs a real WPML site with two same-slug pages.
   `find_by_slug` and `_find_by_meta_gtin` now pass `lang`; `existing_id` needs no scope.

So the per-(GTIN, language) write is **three calls**, uniform across languages (no special case for
the default language), with every lookup scoped to the row's language:

```
GET  /wp/v2/{post_type}?slug=…&lang={lang}     lookup — the &lang is not optional
POST /wp/v2/{post_type}?lang={lang}            title, slug, status, meta.gtin   — no acf
POST /wp/v2/{post_type}/{id}                   acf: {...}                       — second call
POST /noviplast/v1/translations                {"translations": {...}, "source_language": "nl"}
```

**Implemented** in `lib/wp_client.py` (`_lang_params`, `_write_acf`, `upsert_page(acf=...)`) and
`lib/multilingual.WPMLAdapter`. Verified end-to-end through `upsert_page` against the live site:
distinct pages per language, both keeping `p-{gtin}`, linked as translations, and a re-run
returning the same ids rather than duplicating (§6.5).

## 4. Data map: page element → storage → source → transform

| Page element | Stored in | GDSN / other source | Transform |
|---|---|---|---|
| Product name (heading) | WP **post title** | `TradeItemDescription` — **attr 3318**, nl/fr | **strip leading `"Noviplast "`** |
| Tagline | ACF `product_title` + `product_header_video_text` | `TradeItemMarketingMessage` — **attr 1083**, nl/fr (113/127 nl, 112/127 fr) | use as-is |
| Eigenschappen + Technische details | ACF `product_description` (HTML, per language) | **Mostly feed data; only the Eigenschappen bullets are generated** — see §4.1 | assemble → generate the gap → **human-approve** → render as HTML |
| Main image | featured media + `product_header_image` + `product_regular_image` | GDSN referenced files — hero selected by `IsPrimaryFile` → view code → sequence | download → **convert/resize (TIFF→web)** → upload |
| Gallery images | ACF `product_gallery` | remaining GDSN referenced images | download → **convert/resize** → upload → repeater rows |
| Video | ACF `product_header_video_file` | **media folder**, file named `{gtin}*` | match by GTIN prefix → upload |
| Category | `noviplast-categories` term | **GPC brick code** → category map | lookup table (§5) |
| GTIN | post meta `gtin` | GTIN | direct |
| GS1 Digital Link + QR | GS1 resolver + QR files | GTIN + page URL | existing pipeline |
| Page body (`post_content`) | — | — | **left empty** (Oxygen-driven) |

### 4.1 `product_description` is three parts, and only one is generated

Read off a live page (id 1347, *Drain sticks*) the field decomposes cleanly, and the tagline is
**not** a separate piece of content — it is the description's own opening line, repeated in two
other fields:

```html
<p><strong>Reinigingssticks voor je afvoer</strong></p>          <!-- = product_title -->
<p><strong>Eigenschappen</strong><br />
• 12 sticks voor het hele jaar<br />
• Voorkomt extra onderhoud</p>
<p><strong>Technische details</strong><br />
• 12 sticks</p>
```

That same tagline is `product_title` **and** `product_header_video_text` **and** this first
paragraph — one value written to three places, not three values.

| Part | Source | Coverage | Generated? |
|---|---|---|---|
| Opening tagline | `description_short` — attr **1083** | 113/127 nl, 112/127 fr | **see §4.2 — the source is wrong for ~half** |
| **Eigenschappen** bullets | `description_long` — attr **1067** | **6/127 nl, 5/127 fr** | **yes**, for the rest |
| **Technische details** bullets | `net_content` (+ dimensions/material) | 125/127 | no — deterministic |

So the generator's real scope is **the Eigenschappen bullets** (~121 products) plus **the ~14
missing taglines** — not "write the description". Everything else is assembly from data the parser
already extracts. This matters twice over: it is a much smaller thing to review, and every value
that comes from the feed instead of a model is one fewer line in the upstream report (§6).

### 4.2 OPEN: attr 1083 is a marketing message, not a tagline

**Needs a client decision; blocks a good-looking pilot page.** §3's table and §4's table contradict
each other on where the tagline comes from — §3 says *"not in GDSN (open, §6)"*, §4 said *"attr
1083 — use as-is"*. Measured against the real export, **§3 was right**:

| | count | median | over 120 chars |
|---|---|---|---|
| nl | 113 | **54 chars** | 40% |
| fr | 112 | **150 chars** | **54%** |

The live page's real tagline is **31 characters** (`Reinigingssticks voor je afvoer`). But the fr
1083 for `04895069002951` is a **~700-character marketing essay** (*"Découvrez l'outil parfait pour
tous vos besoins en joints élastiques ! Que vous soyez un professionnel du bâtiment…"*), and the
longest is **1433** (`08713195007076`). This is not a data-entry mistake: GS1 attr 1083
*TradeItemMarketingMessage* is free-text marketing copy **by definition**. It and a tagline are
different things, so "use as-is" holds only for the short half.

`acf_map` wires `description_short` → `product_title` + `product_header_video_text` regardless.
That is correct as far as it goes — and for much of the catalogue it would render a wall of text
where one line belongs. **Nothing is published yet**, so this is a decision, not damage.

**Decided (a): flag, don't repair.** `gdsn_map.description_short` declares `max_length: 120`, and
`parse_export` reports every longer value to `source_issues.json` as `value_too_long`, keeping the
text verbatim — truncating would sever a sentence mid-word on the page while the value stayed too
long in MyGS1 and returned on the next export.

**The measured scale is larger than first estimated: 107 findings, not ~60** (46 nl + 61 fr, out of
127 products — the earlier guess counted French only). Total report: 111 findings, i.e. nearly one
per product. That is the honest size of the mismatch, and it may well justify revisiting:

- **(b) Let the generator summarise them** — no manual copywriting, but ~107 more generated values
  to review and transcribe upstream, with the model guessing at the brand's voice.
- **(c) Both** — hand-write the ones that matter, generate the tail.
- **(d) Reconsider the mapping itself.** 107/127 is less "the data is untidy" than "attr 1083 is
  not the tagline field". If a real tagline exists nowhere in the feed (as §3 always said), then
  no amount of MyGS1 editing is the fix — the tagline is client content that belongs in the
  page-build step, and 1083 is better used as generator *input* than as the value.

`max_length` is tunable per field in `clients.yml`; 120 was chosen against a live tagline of ~31
chars and an nl median of 54. Raising it hides the problem rather than solving it.

Whichever is chosen, **reconcile §3's and §4's tables**: their disagreement is what made this look
settled when it was not.

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
- ~~Who writes the French pages — the tool, or the translator?~~ **Resolved (client decision):
  the tool writes both languages.** French comes from the GDSN feed where present — it is
  Noviplast's own datapool data (**36 of 37** planned products have a French `TradeItemDescription`
  3318; **112 of 127** a French `TradeItemMarketingMessage` 1083), i.e. parallel source data rather
  than a translation to author, and the reference text for regulated product info. Where French is
  **missing**, the LLM generator fills it (see the feature/benefit generator below — same
  deterministic cache and human-approval gate). The manual translator's queue is not involved.

  **This requires no WPML settings changes** — verified live, every dependency already holds:
  the post type is Vertaalbaar; ACF fields are per-language (*Vertalen*, not *Kopiëren* — an nl
  re-save left fr intact); "Alles vertalen" is off; language assignment and linking go through our
  own REST route. The tool never opens ATE, the translation proxy, or the jobs queue, so existing
  manual work is untouched.

  **Known caveat (not a pilot issue).** WPML flags a translation as "needs update" when its
  *source* post is edited, surfacing it in the Translation Dashboard. The pilot is create-only (the
  `website_status` gate makes every row NEW), so this cannot fire yet. Once product **updates**
  begin, product pages may appear in the translator's dashboard as "needs update" even though the
  tool has just rewritten the French itself — dashboard noise, not breakage. Unverified from here:
  the TM endpoints are admin-only and the automation user is an editor. Revisit when updates start;
  clearing the flag via WPML's API is the likely fix.
- **GPC brick → category mapping** — **deferred to Phase 7.5** (GS1 DIY sector datamodel; see §5.7).
- **Auto-create missing category terms**, or require them to pre-exist? (Recommend: require pre-exist,
  warn on miss.) — settle in Phase 7.5.
- **LLM provider/prompt + cache location** for the bullet generation — settle when that phase is planned.

## 7. WordPress-side enablers (onboarding tasks)

All REST enablers live in one Code Snippet, **"Noviplast GS1 – expose CPT to REST"** (the site runs
the *Code Snippets* plugin — `code-snippets/v1` in the REST namespace list). The CPT and the
taxonomy are both registered elsewhere (theme/plugin), so the snippet adjusts them through the
`register_post_type_args` / `register_taxonomy_args` filters rather than editing them at their
source — the filters survive updates to whatever registers them.

- **CPT in REST** — `show_in_rest` + `rest_base` via `register_post_type_args`. **Done.**
- **`gtin` post meta in REST** — `register_post_meta`, `show_in_rest`, `auth_callback` =
  `edit_posts`. **Done**, but see the trap below.
- **CPT must declare `custom-fields` support** — **done**, and non-obvious. Core only adds `meta` to
  a post type's REST schema when `post_type_supports($post_type, 'custom-fields')`
  (`WP_REST_Posts_Controller::get_item_schema()`). The CPT's supports were
  `title, editor, thumbnail, page-attributes, autosave` — no `custom-fields` — so
  `register_post_meta` registered the key but it **never reached REST**, and writes to `meta.gtin`
  were **silently discarded**: HTTP 200, GTIN absent, page unidentifiable on the next run. Since
  `meta.gtin` is the §6.1 idempotency key, this would have made every created page invisible to
  re-runs. The filter now merges `custom-fields` into `$args['supports']`.
- **ACF field group → Show in REST** — **done.** All 9 fields are now in the CPT's write schema
  (`product_title`, `product_description`, `product_gallery`, `product_header_image`,
  `product_regular_image`, `product_header_video`, `product_header_video_file`,
  `product_header_video_text`, `is_new_product`).
- **`noviplast-categories` taxonomy → `show_in_rest`** — **done** via `register_taxonomy_args`.
  (The earlier note that it "404s" was wrong: it returned **403 `rest_forbidden`**. That distinction
  is a useful diagnostic — core returns **404 `rest_taxonomy_invalid`** when a taxonomy does not
  exist and **403** when it exists with `show_in_rest` false. So the taxonomy was always registered
  and correctly attached to the CPT; only the REST flag was missing. The CPT's `taxonomies: []` had
  the same single cause — that list is filtered to REST-visible taxonomies.)
- **`meta.gtin` collection filtering** — `rest_noviplast_query` filter, scoped to the `gtin` key
  only (no arbitrary meta querying). **Todo.** `meta_key`/`meta_value` are **not** core REST
  features: core drops unknown query params silently rather than erroring, so without this the
  tool's §6.1 gtin lookup receives an unfiltered page of *every* post. Verified live — a query for
  a GTIN matching nothing returned 10 rows. `lib/wp_client._find_by_meta_gtin` now verifies
  `meta.gtin` on the way out and so is correct with or without this filter (it simply cannot find
  by GTIN without it, and correctly falls through to *create*). The filter is still wanted: without
  it the lookup only ever sees the first page of results, so a page whose **slug changed** would not
  be found by GTIN and would be recreated rather than updated.
- **WPML helper endpoint** — **done**, as the Code Snippet *"Noviplast GS1 – WPML translation
  linking"*. Exposes `POST /wp-json/noviplast/v1/translations` taking
  `{"translations": {"nl": id, "fr": id}, "source_language": "nl"}`, mirroring Polylang's
  `/pll/v1/translations` shape so `lib/multilingual.py` stays symmetric. It assigns each post's
  language and links the set as one translation group via WPML's PHP API
  (`wpml_set_element_language_details` + `wpml_element_trid`), validates every id before writing
  any (a half-linked group has no rollback), and **reads the group back** from
  `wpml_get_element_translations` so a silent no-op fails loudly. Verified live: a scratch nl/fr
  pair linked under `trid` 626, confirmed from WPML's own tables.
  Prerequisite, already satisfied: WPML → *Vertaling berichttypes* → `noviplast` =
  **Vertaalbaar – alleen vertaalde items weergeven**. That is also the right choice on the merits —
  the fallback variant ("val terug op de standaardtaal") would serve **Dutch content at French
  URLs** for any untranslated product, returning 200 so `verify_url` passes and the row reports
  `ok`. It would also have made all 40 existing pages appear under `/fr/` immediately. Untranslated
  should 404.
- **Translation workflow** — the site runs full WPML **Translation Management** (TM + ATE +
  translation proxy + local translators); translations have historically been manual, done by a
  named translator through *Vertaaltaken*. **"Alles vertalen" (automatic translation) is off** —
  it requires the Geavanceerde vertaal-editor, and no translation editor is selected; a human job
  queue is only consistent with the manual mode. So nothing will auto-translate or race the tool's
  French pages. **Open decision (§6):** whether the tool writes `fr` from the GDSN feed or the
  translator continues to.
- **GPC → category** map populated; category terms exist. **Todo.**
- Remove the temporary `noviplast-debug/v1/fields` route once mapping is frozen. It is
  **auth-gated** (401 unauthenticated), so it is not a public data leak — keep it until the ACF
  mapping is verified, then delete the *"TEMP - ACF field name discovery"* snippet.

## 8. Tool-side work (new development)

- `clients.yml`: `multilingual_plugin: wpml`; add `brick_category_map`, media-folder path, and the
  ACF field-name mapping (so field names live in config, not code).
- `lib/multilingual.py`: implement `WPMLAdapter` against the helper endpoint (replaces the
  `NotImplementedError` stub).
- `lib/wp_client.py`: the **three-call write sequence** of §3.1 — `create ?lang=` (no acf) → write
  acf → link translations. All three constraints behind it are live-verified and each fails
  silently, so none may be dropped as an optimisation. Plus media upload **from a URL** and **from
  a local file**.
- `lib/state.py` / `run_plan`: **E18 changes meaning.** Today a missing `product_name.{lang}` omits
  that row with a warning, on the assumption that no source text means no page. Now that the LLM
  fills missing French (§6), the row should be **planned and flagged for generation** instead of
  skipped — otherwise the one pilot product without a French name (`08713195007649`) silently never
  gets a French page. Keep a skip path for the case where *neither* a feed value nor a generated one
  is available.
- `lib/gdsn.py` / parser + `clients.yml` `gdsn_map`:
  - ~~fix `product_name` → attr 3318~~ — **done** (commit `c76492b`); measured 127 nl / 124 fr.
  - ~~add the tagline → attr 1083~~ — **done**, parsed as `description_short`; 113 nl / 112 fr.
  - ~~expose `TradeItemFeatureBenefit` (1067)~~ — **done**, parsed as `description_long`; 6 nl / 5 fr,
    confirming how sparse it is.
  - **strip the leading `"Noviplast "`** from `product_name` — still todo. The feed gives
    `"Noviplast Microvezeldoek stof"`; live pages carry the bare name (`"Drain sticks"`), and brand
    is its own field.
  - **decode `net_content` unit codes** — still todo. The feed gives the raw UN/ECE code
    (`"5 H87"`); the page needs words, per language (`H87` → *stuks* / *pièces*). Deterministic —
    a lookup table, not a generator.
  - extract **multiple** referenced images (all 12 slots, with mime / `IsPrimaryFile` / `FileName`).
- New **feature/benefit generator** (LLM) with a deterministic cache + human-approval gate. Scope now
  also covers **filling missing French** (§6): the feed carries French for most products but not all
  (name 36/37 planned, tagline 112/127). Prefer the feed value whenever present — it is the
  authoritative datapool text — and generate only into the gaps. Generated text carries the same
  approval gate as generated descriptions, and should be distinguishable from feed text in the cache
  so a later feed update can supersede it.
- **Source-data issue report — `output/{client_id}/data/source_issues.json`.** **Built.** The
  operator's work queue for fixing the **GS1 datapool itself**: `parse_export` writes it on every
  run (always, even when empty — a report that only appears with bad news is one whose absence you
  cannot trust), and prints the count with the path. Each
  :class:`~lib.records.SourceIssue` carries `gtin`, `field` (dotted, e.g. `product_name.nl`),
  `issue` (machine-readable kind), the current `value` verbatim, and a one-sentence `detail`.
  The tool **reports rather than repairs**: the datapool is authoritative, so a value silently
  corrected here stays wrong in MyGS1 and returns on the next export. Success is this file
  shrinking to empty.
  - Currently emits `brand_prefix_mismatch` — 4 findings in the pilot export (§8).
  - **To add: the generated-content entries (client requirement).** Every LLM-generated value
    belongs here too: the gap it fills is a datapool gap, and the generated French belongs back in
    MyGS1 as the authoritative value — the tool filling it at publish time is a stopgap, not the
    fix. Those entries additionally need the source-language value they were derived from, so the
    text can be judged before being transcribed upstream. Once a value lands upstream the next
    export carries it, the generator stops firing for that field, and the feed value supersedes the
    cached generation (hence the feed-vs-generated distinction above).
- New **image pipeline**: download → convert/resize (Pillow) → upload → dedupe.
- ~~**Make `tests/integration/test_run_execute_staging.py` safe before it is ever run.**~~ **Done** —
  along with `test_wp_staging.py`, which had the same defect. Both now clean up in a `finally`, so a
  failed assertion still tears down. Three findings reshaped the fix:
  - **`post_status: draft` was the wrong prescription** — it breaks the run rather than securing it.
    `verify_url` issues an **unauthenticated** HEAD, which a draft answers with 404, and
    `_execute_row` calls it mid-pipeline. So `test_run_execute_staging.py` still **publishes**; its
    safety is the teardown, and the page is live only between the upsert and the force-delete. The
    three non-publishing tests in `test_wp_staging.py` did move to `draft`.
  - **The GS1 entry cannot be deleted** — the v2 API has no DELETE. `GS1DigitalLinkClient.retract`
    does the most that is possible (clear `links`, *then* disable via `isEnabled`), leaving a dead,
    linkless, disabled record on the account **forever**. Its `get()`-first guard is load-bearing:
    the clearing call is an *upsert*, so retracting an absent GTIN would **create** a production
    record and then disable it.
  - **The prefix rule alone is not enough.** `STAGING_GTIN` must be in the `8713195` prefix *and*
    must not already have a page — enforced by a pre-flight that reuses `_lookup_existing`, the same
    resolution the write performs. A real product's GTIN passes the prefix check, and the run would
    then adopt its live page, overwrite it, and let teardown delete it **with every ownership guard
    correctly passing**, because the GTIN genuinely matches. `STAGING_GTIN` now has no default.

  `pytest` also gained `addopts = "-m 'not staging'"`: the env-var skipif was satisfied by any shell
  that had sourced `.env`, so a bare `pytest` could hit production. `pytest -m staging` still opts in.
- **BUG: the GS1 link set is written per language, so the fr write destroys the nl link.**
  Found while making the staging tests safe; **not yet fixed**. `PlanRow` is *"one (GTIN, language)
  unit of work"* and `_execute_row` runs once per row, but `_build_links` (`run_execute.py:103`)
  sets `language=row.language` — it builds links for **one** language, and `safe_upsert` then sends
  that one-element array as the record's **entire** `links` set. So a nl+fr GTIN gets two upserts,
  `links:[nl]` then `links:[fr]`.

  **CreateOrUpdate REPLACES the links array — confirmed live, 2026-07-17.** Probed against
  `08713195000374` (disabled throughout, restored after): sending `links: []` left the record with
  **0 links**. The array is authoritative; a write sets the whole set. So this is **data loss, not
  duplication**: the fr row wipes the nl link, the Dutch QR resolves nowhere, and the row reports
  `ok`. The GS1 docs do not specify this anywhere — `CreateOrUpdateDigitalLink`, `GetDigitalLink`
  and `UpdateDigitalLinkIsEnabledStatus` are the whole relevant surface and none mentions merge
  semantics; it took a probe.

  Two further defects in the same few lines:
  - `gs1_links[0].default: true` is applied to **both** languages, so both links would claim
    `defaultLinkType`. The client's rule is *standaardlink* for **nl only**, not fr.
  - `per_language: true` in `clients.yml` is **dead config** — `GS1LinkConfig` declares it, the
    config sets it, and nothing in `lib/` or `scripts/` ever reads it.

  This has not bitten yet only because `clients.yml:9` leaves `environment: test`, so a live run
  hits the contract-less sandbox and fails before writing. **The fix** (client requirement): group
  rows by GTIN and send **one** upsert per GTIN carrying the full `[nl, fr]` set, with
  `defaultLinkType` on **nl only** — never more than two links. Because the array replaces, that
  alone satisfies "adjust the existing links, don't duplicate them": a manual link at the same
  (linkType, language) is overwritten, not appended. **Open:** whether to preserve links of *other*
  link types that we do not manage — a wholesale replace would silently delete them.

- **`accountNumber` in `clients.yml` was wrong — fixed 2026-07-17.** It read `8713195000008`,
  commented *"Noviplast GLN — confirmed accepted (200) in prod"*: a guess derived from the
  `8713195` prefix rather than read from the token, unlike the sandbox entry beside it. Verified
  live: the production token's own `accountNumber` claim is **`8719965024137`**, and the live
  record for `08713195000374` is owned by `8719965024137` — so every production POST was carrying
  an account that is not ours. The 200 proved nothing. `clients.yml` is gitignored, so this note is
  the only durable record; `clients.example.yml` carries a placeholder and needed no change.
  **Rule: the accountNumber always comes from the minted token's claim, in both environments.**

- **BUG: `link_type: pip` was unrecognised — fixed 2026-07-17.** An earlier note here claimed
  *"the API normalises `linkType` `pip` → `gs1:pip`"*. **It does not.** That was inferred from a
  UI-created record without checking one of ours, and comparing the two settles it:

  | record | created by | `linkType` | `linkTypeTitle` |
  |---|---|---|---|
  | `08713195000374` | MyGS1 **UI** | `gs1:pip` | "Product Information Page" |
  | `08713195000527` | **our tool** | `pip` | **`null`** |

  Link types are GS1 Web Vocabulary **CURIEs**, and the API stores `linkType` **unvalidated** —
  a bare `"pip"` is accepted with a 200 and read back with a null `linkTypeTitle`, i.e. not
  recognised. So every link the tool has ever written carried an unrecognised type, silently.
  `clients.yml`, `clients.example.yml` and `_DEFAULT_LINK_TYPE` now use `gs1:pip`.

  `linkTypeTitle` *is* server-derived (it is in `LinkResponse`, not `LinkRequest`) — but only
  for a link type the server knows. It is the tell, not a decoration.

  Still true and not a bug: the API normalises a `mediaType` of `null` to `""`.
- A **Noviplast page-build step** that assembles the above into the ACF payload — replacing the
  Phase 5 HTML-template render for this client.

## 9. Relationship to the rest of the project

Phase 7 (`run_plan`, `diff_against_state`, `flow-orchestrator`) is complete and committed and is the
engine underneath this: it still computes *what* to act on. This adapter changes only **how the
WordPress page is built** for Noviplast, and adds the media/WPML/LLM pieces the real site requires.
It is effectively a new phase of work and should be planned and specced as such before coding.
