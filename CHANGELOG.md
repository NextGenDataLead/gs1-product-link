# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Every value the feed carries in one language and not another is now filled by translating it,
  and every filled value is reported for the client to put back into MyGS1.** The generator already
  did this for the product name, because a missing name stops a page publishing (E18) — it did it
  for nothing else, and it emitted no finding at all, so an LLM-written French title reached a live
  page with nothing anywhere to say so. Meanwhile the French page took the Dutch material word and
  the Dutch variation suffix, and its copy was written from a 1083 the input gatherer read as blank.

  A source opts in with **`translate: true`** in `clients.yml`, per field. Opt-in rather than
  implied by `localised: true`, for the reason `in_matrix` is an opt-out: `logistics_name` and
  `marketing_name` are carried and consumed by nothing, so filling them would be producer tokens
  spent on a value no page reads. It also lets `material` join on the same one rule, which
  `localised` could never have selected — it is language-agnostic in the feed yet renders verbatim
  on every language's page.

  **The line this does not cross:** a field blank in *every* configured language is never filled. It
  stays a source finding for MyGS1 (E23). Rendering a value the feed already holds into a second
  language is translation; writing one that exists nowhere would be invention, which this tool does
  not do. The distinction is what makes the practice defensible, and the reporting is the other
  half — this change *raises* how much LLM-written text is on a page, so **§4 of the data-quality
  report** lists each filled value with the exact text to paste into MyGS1, after which the next
  export carries it for real and the tool stops writing it. Video moves to §5, Categories to §6.

  Consequences worth knowing before the next run:
  - **Every existing cache entry is invalidated once.** `GenerationInputs` gained
    `translation_sources`, without which editing the Dutch 1083 left the French entry looking fresh
    — that entry's own inputs are all empty, so nothing else in its fingerprint moved and the
    translation of a since-changed value would have survived the edit. Re-run
    `run_generate --emit` → `content-generator` → `--ingest` before the next `run_plan`.
  - **`missing_generation_input` now fires only when 1083 is blank in every language.** A value the
    feed carries in Dutch is a pending translation, not a datapool gap; reporting it as one asked
    the operator to write French copy for a product that already had Dutch.
  - **A `products.json` from before this change should be re-parsed.** `ProductRecord` gained
    `extras_localised`, because a `localised: true` pass-through extra used to resolve to the
    default language and discard every other language the feed carried. Old files still load and
    behave exactly as before; re-parsing is what recovers the French the feed already had.
  - **`material` is reported but cannot be pasted back.** GS1 attr 4.012 has no per-language slot,
    so its translation is a page fix only. The §4 row says so rather than sending a wasted trip
    into MyGS1.
- **A double-click install for the operator's machine — `install.command` / `install.bat`, then
  `start.command` / `start.bat`.** The shell existed but still needed a clone, a virtualenv and
  `pip`, which is most of the 33-step install the shell was built to avoid. The installer fetches
  `uv` (one static binary), has `uv` fetch its own CPython 3.11, and builds `.venv` from the
  committed lockfile. Nothing is preinstalled, nothing is typed, no administrator rights, and
  nothing is written outside the folder and the user's home directory.
  [`docs/operator-install.md`](docs/operator-install.md) covers the handover, the two ways a
  managed machine refuses to open an unsigned file, and what IT is being asked to allow.

  **`uv.lock` is now committed** — it was gitignored. It is what the operator installs *from*:
  `uv sync --locked` refuses to resolve anything the lockfile does not already hold, so that
  machine gets the versions that were tested rather than whatever resolves that day, and there is
  one reviewable artifact (86 packages, with hashes) for a security team to vet before any of it
  is installed. It also closes a real reproducibility gap: CI pinned 3.11 while the development
  venv had drifted to 3.14.5, with `requires-python = ">=3.11"` permitting both.

  A lockfile is only worth having if it cannot silently go stale, so drift is checked twice: CI
  runs `uv lock --check` (authoritative, needs `uv`), and `tests/test_packaging.py` compares the
  lock's recorded requirements against `pyproject.toml` offline — a dependency added without
  re-locking installs fine on the maintainer's machine and *fails* on the operator's, which is
  the worst place to find out.

  The `uv` version and the Python version are written out in all four scripts and in
  `.github/workflows/ci.yml`; the same test fails if one copy moves alone. There is deliberately
  **no `.python-version` file** — pyenv reads that file too, and it would break `python` in this
  directory for anyone who has pyenv without 3.11 installed.

  CI still installs with `pip`. Adding a lockfile changed what the *operator* resolves, not what
  CI resolves, and conflating the two would have been an unannounced change to the thing that
  gates every merge.

- **A local operator shell — `pip install -e ".[ui]"` then `python -m ui`.** A desktop window
  over the same commands a person would type, so the recurring loop (drop an export, prune the
  process list, import the copy, run the flow, read the result) needs no terminal, no
  virtualenv, and no knowledge of which of nine scripts to call. Six screens: Setup, Preflight,
  Data, Content, Publish, Runs. Bound to `127.0.0.1`, native window, no shareable URL.
  Documented in [`docs/ui-operator-shell.md`](docs/ui-operator-shell.md).

  **It subprocesses the scripts; it never imports their `main()`.** `load_env()` lives in each
  script's `__main__` block by design, so an in-process call would have no credentials — and
  calling `load_env()` in the shell would put production secrets into a long-lived desktop
  process and arm the four staging-guard variables inside it. Subprocessing also inherits the
  production refusal, the `state.json` writes, the run JSONL and the `--only links`
  target-serves check unchanged, since all of those live in `scripts/`. `tests/lib/test_env.py`
  now asserts no module under `ui/` reads `.env` either.

  **It has no LLM, no `ANTHROPIC_API_KEY` and no Anthropic egress.** Content generation stays on
  the maintainer's machine; `generated_cache.json` is handed over as a file and uploaded on the
  Content screen, which reports coverage against the *current* export and names the pending
  units — a cache goes stale on any feed edit, and a pending unit with no producer is an E21
  omission.

  **`ui/session.py` refuses to build the run command while any required gate is outstanding.**
  Not a warning — a function that raises. That is the one thing prose cannot do: a paragraph can
  be paraphrased, compressed or skipped when the context is long. An empty plan is refused for
  the same reason, since publishing nothing successfully is the outcome indistinguishable from
  success.

  The `ui` extra is optional and nothing under `lib/` or `scripts/` imports it, so the suite and
  `mypy --strict lib` run unchanged without NiceGUI installed. `ui/` is **not** covered by
  `mypy --strict` yet.

- **`lib/gates.py` — the operator gates as data.** The gates are the safety mechanism and they
  now have two consumers: prose a model reads in `flow-orchestrator/SKILL.md`, and structure the
  shell renders as forms. Two implementations of one safety contract drift, and this one drifts
  *silently* — a gate that quietly stops being shown raises nothing.

  So the structure lives in one place — which gates exist, at which step, which are
  non-negotiable, which apply in which mode, and one sentence on why each is there — and
  `SKILL.md` gains a **Gate index** table that `tests/lib/test_gates.py` checks in **both
  directions**. Adding a gate to either without the other fails CI.

  `run_execute_argv` lives there too, because the command is part of the contract: `--only`
  comes from the intent gate, `--i-understand-production` from the production gate, and getting
  either wrong turns a reviewed decision into an unreviewed write. `production_acknowledged` is
  a positive statement that someone confirmed rather than an `is_production` fact derived from
  config — deriving it would make the flag a description of the environment instead of a record
  of a decision. A dry run never carries it.

- **`python -m scripts.doctor` — a preflight, so failures arrive before the work instead of
  during it.** Credentials resolved lazily at the first API call, so a
  `MissingCredentialError` could fire *after* parse, plan and a clean dry-run had all passed.
  A `clients.yml` with four blank fields reported them one run at a time, because
  `load_clients` raises on the first violation and discards its `json_path`. And a
  generated-copy cache that no longer matched the export reported nothing at all — those
  units simply vanished from the plan (E21).

  The checks live in `lib/preflight.py` as pure functions from configuration to a
  `CheckResult(name, title, status, detail, remedy)`, so a UI, a test and the CLI can run the
  same checks and disagree only about how to display them. `scripts/doctor.py` is the
  argument parsing, the `.env` load and the rendering; `--offline` stops before any check that
  reads a credential or opens a socket, `--json` emits the results for a caller to parse. Exit
  `1` on any failure; warnings do not fail the run, because a warning that stops you is a
  warning you learn to disable.

  What it checks, and the specific trap each one is for:

  - **Config** — validated with `jsonschema.iter_errors` directly, so **every** offending
    field is named with its path, not just the first.
  - **Scope** — how many products a run would actually touch after the process list and
    `media.restrict_to_mapped_gtins`. The number an operator most needs and is least often
    given: every gate between "38 GTINs listed" and "15 a run can publish" is silent by
    design. Every check below it reports on that scope rather than the whole catalogue —
    a report full of findings about work nobody asked for is a report people stop reading.
  - **Generator block** — a `generator:` block deleted as unused cleanup does not raise; it
    sets `require_generated_copy=False` and copy-less units publish blank taglines instead of
    being held. Reported as a failure when a generated-copy cache exists, which is proof one
    was configured.
  - **Cache coverage** — the fingerprint covers `{inputs, language, prompt_version}`, so any
    feed edit or version bump makes those units pending again, and a pending unit with no
    producer is an E21 omission.
  - **Process list, category coverage, video mapping, ffmpeg** — the existing `--check` gates,
    offline. An unconfirmed video is a *warning* even under `restrict_to_mapped_gtins`, and
    especially then: the restriction is what makes the gap safe.
  - **Site reachable / WordPress credential / GS1 resolver** — separate checks, so "the site
    is down", "the password is wrong" and "the account has no contract" are three different
    answers rather than one confusing one. A 401 names the six-groups truncation trap by name.
    A GS1 `21011` says outright that it cannot be fixed in code or config.

  The GS1 check refuses one specific false pass: a GTIN the resolver has never seen answers
  with the same `400 "No valid contract found for Gtin with id: …"` as the 21011 blocker, so a
  clean "no record" is reported as *credentials accepted* and explicitly **not** as
  contract-present. It also will not invent a GTIN to probe with — a GS1 record can never be
  deleted, so a typo would be permanent.

  Nothing here writes anything the pipeline reads, and nothing calls `load_state`: an idle
  peek at a corrupt state file quarantines it (E19), and a diagnostic must not change what the
  next run does. There is a test for exactly that.

  `WordPressClient` gains `whoami()` — `GET /wp/v2/users/me?context=edit`, the read-only check
  `docs/troubleshooting.md` has documented as a shell one-liner, now in code. The
  `context=edit` is load-bearing: a bare `users/me` answers 200 for any credential that
  authenticates and omits `roles`, so it cannot tell a working password from a working
  password on a demoted account — and those fail at very different moments.

- **`run_plan` writes `plan.summary.json` beside the plan.** Everything the run concluded
  but did not put *in* the plan — the process-list and pilot-gate exclusions, the tally of
  units dropped before classification, and the E19 state reset — existed only as prose on
  stderr, so the only reader that could ever see it was the process that ran the command.
  An operator returning to an hour-old plan, or anything driving `run_plan` from outside,
  had nothing to go on.

  The file carries `counts`, `total`, a `skipped` tally by reason, an `excluded` tally by
  gate, the category and generated-content issue counts, `state_reset_from_corrupt`, and
  `state_corrupt_backup` — the path the bad state file was quarantined to, which is the
  evidence for that flag and is knowable only inside `load_state`, since the name is
  stamped with the moment of the reset. `text` holds the stderr summary line verbatim, so
  a second reader shows the operator the same words rather than a reconstruction of them.

  Written on **every** run, never conditionally: a missing file has to mean "run_plan did
  not run", so that an empty tally can mean "it ran and found nothing". Those are different
  facts, and a reader that cannot tell them apart is the E21 trap in another costume.

  `State` gains `corrupt_backup` alongside `reset_from_corrupt`, excluded from
  serialisation for the same reason — it describes the load, not the persisted state.

- **`plan.json` now records the units it dropped.** Three checks remove a
  `(GTIN, language)` *before* classification — E18 (no `product_name` in that language),
  E21 (a generator is configured but the unit has no generated copy yet) and E22
  (`require_hero_image` with a blank source image). They left no trace but a
  `WARNING SKIPPED …` line, and since `Plan.total` is `len(rows)` the plan under-reported
  the work by exactly the units that had gone missing. A plan that dropped *everything*
  for want of copy was byte-comparable to a plan with nothing to do, and the run after it
  reported success having published nothing.

  `Plan.skipped` is now a list of `SkippedUnit(gtin, language, reason, detail)`, with
  `reason` one of `missing_product_name` / `no_generated_copy` / `blank_hero_image`. It
  sits *beside* `counts` rather than inside it: a skipped unit is not a fifth
  classification but an absence, and folding it in would change what every existing reader
  of a count believes it is reading. `total` and `counts` keep their exact meaning, and
  `skipped` defaults to empty so a `plan.json` or `plan.confirmed.json` written before it
  existed still validates.

  `run_plan`'s summary gains `; 6 skipped (4 no_generated_copy, 2 missing_product_name)` —
  the reason, not just the count, because "6 skipped" is a number to shrug at and
  "4 no generated copy" is an instruction. `flow-orchestrator` step 5 now has to show the
  same line above its menu.

  `lib.state.diff_against_state` returns a `PlanDiff(rows, skipped)` pair rather than a
  bare row list, so a caller cannot take the rows and leave the drops behind — which is
  what every caller did for as long as the drops were only a log line.

### Changed
- **Copy is now written only for the rows a run will actually publish.** `run_generate` asks a
  producer for the in-scope `(GTIN, language)` units that classify **NEW or CHANGED**, not for
  every unit in scope. UNCHANGED is never confirmed by either operator surface and HELD is dropped
  by `run_execute`, so copy for those was always text nothing would read. On the pilot client that
  is 54 units per run instead of 74; on a catalogue mostly already live it is close to nothing.

  **This is scope, not the cache coming back.** A unit is left out because nothing will be
  published for it — never because copy for it exists somewhere. Existing copy has no vote
  anywhere: write copy for every unit and the answer does not move. The distinction is stated in
  `lib/generator.py`, in `pending_requests`, and in `run_generate`'s module docstring, because the
  shape of the change looks exactly like the thing the previous entry removed.

  It became possible two entries ago: with generated copy out of the content hash, a unit's
  classification no longer depends on having copy, so a run can classify first and generate second.
  `lib.state.classify_units` answers that question with **no** skip rule applied, over the same
  `_plan_unit` core `diff_against_state` uses so the two cannot drift. E18 in particular must not
  run there — a translated French name is one of the things the producer supplies, so applying it
  would drop the unit before the producer could close the gap, and the gap would close itself out
  of existence. `lib.preflight.units_needing_copy` composes it with `in_scope` and the category
  assignment (lifted from `run_plan` into `lib.categories`, since the category is inside the
  content hash and a script may not import a script). It **peeks** at state rather than loading it:
  `load_state` quarantines a corrupt file, and consuming that reset outside `run_plan` means the
  operator never sees "every row re-plans as NEW" at the plan gate. When the answer cannot be
  decided — unparseable state, no URL patterns — every unit is asked for, because a run that
  quietly writes no copy for a page it is about to publish surfaces as a blank page, not an error.

- **E21 is asked after the classification, and only of a row that will be written.** Before, it
  could not tell "nothing was written for this unit" from "nothing needed to be", so under scoped
  generation it would have reported every already-live page as a work item — twenty of them on the
  pilot client, in a plan whose whole job is to say what there is to do. An UNCHANGED unit with no
  copy now keeps its row and its count; a NEW or CHANGED one is still held. A unit somebody
  unpublished now reports **HELD** rather than `no_generated_copy`, which is what it is: it is
  waiting for a decision, not for the generator.

- **`run_execute` enforces E21 too.** Its module docstring has claimed "the pilot allowlist,
  HELD-drop, and E21 still apply on top" since `--only` was added, and nothing enforced the third:
  E21 lived in `run_plan` alone, so it protected a plan this tool had just written and nothing
  else. `run_execute --plan plan.json` confirms **every** row in the file — a documented
  invocation (`docs/setup.md`, the pilot handoff) — so once UNCHANGED rows stopped carrying copy,
  that path would have rendered a blank tagline over a live page. Rows with no tagline for their
  language are dropped with a warning naming them, per row rather than per GTIN, and only for
  clients that have a `generator` configured.

- **`run_generate --validate` counts surplus apart from rejected.** Rejected means copy the run
  wanted and cannot use (stale fingerprint). A results file wider than this run's batch is now the
  ordinary state of things — the batch shrinks as rows go live — and reporting it as `rejected 20`
  puts an alarming number on a healthy run, which is how an operator learns to stop reading the
  number. "No pending unit" also gained its third cause: in scope, but already live and unchanged.

- **The data-quality report's section 1 is one section that says what it lists.** §1a (E23) and
  §1b (E24) repeated what §0's coverage matrix already shows, and §1d repeated the matrix's
  `product·3301` / `image·2485` columns — its only extra GTIN was out of scope entirely, a
  whole-catalogue finding leaking into a scoped report. All three are gone, and with one subsection
  left there is no subsection.

  What remains had drifted furthest of all. It said *"the generator produced nothing for these
  units … then re-run generation"*, which was never what it listed: the rows come from
  `missing_generation_input`, which fires on a blank **attr 1083**, and re-running generation
  cannot fill a field the datapool does not have. Under scoped generation that sentence would also
  have read as an accusation about every already-live unit, so the section now says explicitly that
  it is not a list of units without copy this run. Rows are per `(GTIN, language)` and each carries
  its real consequence, recomputed from `products.json`: blank 1083 with no 1067 is genuinely held
  (E21); blank 1083 with 1067 publishes from it. The Summary row follows — "**Yes** — where 1067 is
  blank too" — because a blocker count that includes non-blockers is how the real ones stop being
  urgent. §2's count now says "written this run", a number that had silently changed meaning.

- **The generated-copy cache is gone. Copy is written fresh every run and never stored.**
  `generation_results.json` keeps its name and its path and changes meaning: it stops being a
  hand-off buffer that `--ingest` folded into `generated_cache.json`, and becomes *the run's copy*,
  read directly by `run_plan`. Nothing accumulates, nothing is reused, and no run can skip
  generating. **BREAKING** for anyone driving `run_generate` by hand: `--ingest` is now
  `--validate`, and it writes nothing.

  The cache existed to make re-runs cheap and stable. It bought stability at the price of a store
  that decides when to skip work — and that store was quietly wrong: the live file held 30 units
  across 16 GTINs stamped 19–28 July, **20 of them from the Cowork experiment that no longer
  exists**, still loaded on every run, because nothing has ever pruned it. Idempotency now comes
  from the other end instead. Since the previous entry the content hash excludes generated copy, so
  writing it again does not republish a page; that is what makes always-regenerate affordable, and
  it is why the two changes had to land in this order.

  Cost is the trade, and it was measured before it was accepted: a full 127-product catalogue is
  ~$1.75–$2.65, so `docs/costs.md` no longer claims that re-runs "do not re-pay" — they do.

  **`input_fingerprint` survives as a validity check only, never a reuse key.** The results file
  outlives the producer session that wrote it, so a `parse_export` re-run in between would publish
  copy describing data the feed no longer holds. A mismatch now drops the unit, says so, and E21
  holds it out of the plan; `check_generation_results` catches the same thing *before* a wave,
  which is what turns a forgotten regeneration into a loud failure rather than wrong copy on a live
  page.

  Three facts the cache stored are now derived when the copy is read, so a hand-written file cannot
  mislabel itself: `origin` comes from the feed (a short attr 1067 is FEED, a long one TIGHTENED,
  none GENERATED — the same rule `pending_requests` already used for `mode`), `source_input` from
  the inputs, and `provenance` moves to the file, because one producer writes one file per run.

  **`prefill_from_feed` is gone and its rule is not.** It wrote feed-verbatim copy into the cache;
  with no cache there is nowhere to write it, so `_feed_verbatim` is consulted by both
  `pending_requests` — which skips those units, so they still cost no producer call — and
  `merge_generated`, which derives the copy from the feed on every run. One helper for both sides
  deliberately: #96's lesson is that one rule answered independently on two paths drifts into
  meaning two things. Measured: 8 of 254 catalogue units, 4 GTINs, **0 of the 74 in scope**.

  `--backend api` writes the results file too, so both producers feed one seam rather than the
  headless one bypassing it. The Content screen imports `generation_results.json`; copy in it for a
  GTIN outside this run's scope used to be ordinary accumulation and now means the file was written
  against a different process list, so the screen says so.
- **Generated copy no longer decides whether a page has changed.** The content hash now covers the
  product as the feed defines it, categories included; the generator's output is excluded. A
  re-generation over unchanged source data therefore leaves a published page **UNCHANGED** instead
  of reclassifying it CHANGED, and the copy still reaches the page exactly as before — only the
  comparison ignores it.

  The old behaviour was deliberate: fold the copy in first, and new copy reclassifies the row. That
  holds only while copy is *stored* and reused. Ask a producer the same question twice and it
  answers differently both times, so once copy is regenerated per run — which is where this is
  going — a hash that covered it would rewrite the entire live site on every run having changed
  nothing, and UNCHANGED would stop meaning anything. Measured on the real catalogue: under the old
  rule **253 of 253 units** re-hash on a re-generation; under the new rule, **none** do. A genuine
  feed edit still reclassifies — one product's brand edited moves exactly its own two units.

  `diff_against_state` takes the pre-generator records as **`hash_source`**; passing nothing keeps
  the old behaviour bit-for-bit, verified byte-identical against `main` across all 253 units. The
  skip decisions are unmoved — copy is still merged *before* classification, because E21 asks
  whether a tagline exists and a translated French name is still what stops E18 firing.

  **One-time cost, already paid.** Every live `content_hash` moves once, so the 20 live units
  reclassify CHANGED on the next run. PRs #93/#94/#96 had already moved all of them with no
  `run_execute` since, so this rides along free — a window that closes on the next publish.

  **The trade this accepts:** better wording on a re-run will no longer publish by itself, because
  nothing in the hash moved, and there is no force flag yet. That is the price of an idempotent
  re-run, and it is the right way round — a rewrite of every page is not something a tool should
  do because a model chose different adjectives.
- **One GDSN attribute, one field — enforced at config load, and the one attribute that broke the
  rule is now declared once.** Attr 3301 was mapped as `product_name` *and* declared again as
  `gdsn_extras.functional_name`. The two were byte-identical for all 127 products in both languages
  — necessarily so, being the same sheet and attribute — so the duplication added nothing and cost
  something everywhere the value was consumed:

  - **The data-quality report asked for one MyGS1 paste twice.** §4 emitted a row per field, so one
    cell in MyGS1 arrived as two jobs. The previous release papered over this with a dedupe on
    `(GTIN, language, attribute, value)`; that dedupe is **removed**, because it would have quietly
    absorbed the next duplicate instead of surfacing it. `lib.config` now raises `ExportParseError`
    at load when two sources read the same `(sheet, attribute)` — the sheet is half the identity,
    since GDSN numbers are unique only within a sheet. `lib.preflight.check_config` calls
    `load_clients`, so `python -m scripts.doctor` reports it before any run.
  - **The §0 coverage matrix carried two identical columns**, `product·3301` and `functional·name`.
    The second is gone, so every SKU's `score` drops by one or two. The ordering is unaffected —
    the shift is uniform.
  - **The client templates printed the product name twice**, once in the `<h1>` and again in an
    `{{extras.functional_name}}` block below it. A leftover from when the title came from 3318 and
    the two were genuinely different. Both blocks are removed; left in, they would render empty and
    log an E12 unknown-extra warning on every page.
  - **The generator read the extra with the mapped field as a fallback**, for a value that could not
    differ. `_gather_inputs` now reads `product_name.get(language, default_language)`. The producer's
    input keeps the name `functional_name` — it is what the prompt and the content-generator skill
    call it, and 3301 *is* the Functional Name — so `prompt_version` does not move. The
    default-language fallback used to arrive by accident, via extras collapsing to one language; it
    is now stated. That also closes a real hole: a unit with no name in its own language **and** no
    extra was previously handed nothing at all.

  Consequences worth knowing before the next run:
  - **Edit your own `clients.yml` too.** It is gitignored and hand-carried (`docs/operator-install.md`
    lists it among the five handover files), so merging this does not change it — and from this
    release a `clients.yml` that still declares 3301 twice **will not load**.
  - **Re-run `parse_export`.** `products.json` still carries `extras_localised.functional_name`
    until you do. Verified on the real export: the re-parse removes that key and changes nothing
    else — `product_name` is identical for all 127 products.
  - **One cached unit is invalidated, not the cache.** `translation_sources` holds one key per
    *gap*, not per translatable field, and 3301 is present in `fr` for 126 of 127 — so exactly
    `08713195007649/fr` re-fingerprints. (The cache is empty anyway until the regeneration the
    previous entry asks for.)
  - **Every content hash moves, so every row will classify CHANGED once it reaches classification** —
    `compute_content_hash` dumps the whole record, `extras_localised` included. Measured: all 127
    products and all 20 live `(GTIN, language)` entries. This is free *now*, because the previous
    entry already moved every hash and no `run_execute` has followed it; after a post-#93 publish it
    would cost a second full rewrite of every page.

### Fixed
- **The data-quality report marked its mandatory columns in bold, and markdown table headers are
  bold already — so the mark rendered as nothing.** §0's coverage matrix separates the two facts
  that decide what an operator does with a gap: a blank in a mandatory column **holds the whole
  SKU** (E23, and E24 for the video), a blank in an optional one only thins the page. That
  distinction was carried by `**…**` around the header text, which renders identically to the
  header without it, and the legend said *"Bold columns are mandatory"* while pointing at nothing
  visible. Fourteen columns all looked the same.

  Each header cell now carries its group — `MANDATORY<br>product·3301`, `optional<br>material` —
  because markdown has no row above the header to put a label in. The legend names the two groups
  and counts the mandatory ones instead of describing a typeface.

  **The video column moved into the mandatory block**, where it belongs: a GTIN with no
  client-confirmed video is held out of publishing entirely, but the column rendered last, after
  the optional ones and next to `score`, which left the table with a third group at the far end
  and made "mandatory first" untrue of the row as a whole. `_columns` is now the only thing that
  orders the matrix, video included, so the header and the cells cannot group them differently.

  Measured on the real 37-product report: **every cell and every score is unchanged** — same
  values under the same column names, same row order — and the rest of the report is untouched.
  The only differences are the header text, the legend, and the position of the video cell.

- **A product made of two materials published as one, because only the first repeated slot of an
  attribute was ever read.** `BrickGPCCommercialData` spreads a product's materials across
  `Material[0]`, `Material[1]` and `Material[2]`, all three labelled `Material (4.012)`. The
  `multivalue` flag exists for exactly this and had been solving it for attr 1067 for months — but
  it was honoured on **one** of four resolution paths, the localised mapped field. `material` is a
  language-agnostic *extra*, so setting the flag on it was a silent no-op; a localised *extra* was
  truncated the same way.

  `pick_scalar_all` is now the language-agnostic twin of `pick_localised_all`, and one
  `_scalar_picker` makes the single-or-every-slot choice for both language-agnostic resolvers, so
  the flag cannot mean one thing on one path and nothing on the others again. Three details it
  gets right: blank slots are skipped, so a hole does not become an empty item; each slot pairs
  with its **own** `MeasurementUnitCode`, so a repeated measurement cannot report slot 1's number
  in slot 0's unit; and `pick_scalar` keeps its exact previous behaviour — first slot, blank
  included — so nothing that did not ask for `multivalue` moves.

  **The separator differs by kind, deliberately.** A localised source still joins with a newline,
  because the generator splits attr 1067 back into ranked USP candidates on that character. A
  language-agnostic one joins with `", "`, because its value renders verbatim in a page's
  Technische details, where a newline collapses to a space and `kunststof metaal` reads as one
  fused material.

  **The `zzz…` placeholder guard had to follow, and this is the part worth reading.** It tested
  the whole string, so a joined `"kunststof, zzzanders"` would have read as an ordinary material:
  the datapool's own "no value" marker on the live page, and — worse — a §4 row telling the
  operator to paste it into MyGS1, turning a blank into fabricated master data. That is the exact
  failure the guard exists to prevent. It now drops placeholder and empty slots by the same rule
  whether or not a placeholder is present, because walking only when one is found made the gap in
  `a, , b` survive while the one in `a, , zzzanders` vanished.

  **Measured against the real export rather than the fixtures:** re-parsing changes **exactly
  seven** of 127 products and nothing else — `material` is the only field, in any record, that
  differs. `…1036`, `…3276`, `…6529` and `…7649` gain a second material, `…4501` and `…8202` gain
  glass, and `…8066` becomes `stof, kunststof, metaal`. No joined value contains a placeholder
  slot, and the two products whose material is only `zzzanders` still resolve to absent.

  **Re-run `parse_export` to pick this up.** The seven products' generation fingerprints move, so
  their copy regenerates — free right now, because the generated-copy cache is empty (0 of 74) and
  has to be rebuilt before the next `run_plan` regardless. This is not a third cache invalidation.

- **Video-mapping hints had been scoring on the product name alone, silently, since the localised
  extras landed.** `lib.media_video._candidate_fields` reads `marketing_name` (attr 3318) and
  `logistics_name` (attr 3297) to match a video filename against the feed. Those two are
  `localised: true`, and the change that kept every language of a localised extra moved them out of
  flat `extras` into `extras_localised` — which that function never read. The loop returned nothing
  from then on.

  It matters because **the filenames are English marketing names and the feed's English is in those
  two extras**, not in the product name: where attr 3301 reads *"huisdierspeelgoed"* / *"Jouets
  chiens"*, the French `marketing_name` reads *"Noviplast Pet Buddy"*. Every language of both is now
  offered, labelled `extras.{name}.{lang}` the way `product_name.{lang}` already was.

  **Measured against the mappings the client has already signed off** — 48 of the 166 rows — asking
  where the *true* GTIN ranks: it was #1 for 23 and in the top 3 for 27; it is now **#1 for 36 and
  in the top 3 for 39**. Sixteen rows moved and **none moved backwards**. That sample is
  self-selected, so it is evidence the scoring improved, not a claim about the 118 rows still unset
  — for those, the best available hint clears 0.90 for **9 files where it previously did for 2**,
  and improves for 71 of 118. Scores can only rise here: fields are added, never removed.

  Two things this deliberately does **not** do. It does not re-add `functional_name`, dropped in the
  same release: that was a second declaration of attr 3301 and could only repeat what the product
  name already says. And **it does not touch `input/{client}/videos/mapping.yml`.** That file is the
  operator's document — its trailing comments are the record of *why* each GTIN was chosen — so the
  hints already written into it are not updated and will not be. The operator shell recomputes hints
  live, and `build_video_map` still prints its draft to stdout and writes nothing. Because the
  stored note and the recomputed suggestions can now disagree, the shell labels the stored one
  *"Noted in the file:"* rather than presenting it as a current hint.

  Nothing on the publish path changes: this is hint ranking only. **Re-run `parse_export` if your
  `products.json` predates the localised-extras change** — a record that still holds those names
  flat is read through the fallback and gets exactly the old hints.

- **`state.json` said `gs1_enabled: true` for products with no GS1 record at all**, and two
  `--revive` paths left a product half-restored. The field was **overloaded**: written by
  `run_unpublish` as *"was this deliberately retracted"*, read by `lib.state._is_held` the same
  way, and named and documented as *"is the resolver record enabled"*. Those readings diverge
  exactly in the pages-only case, where `run_execute` writes an entry that takes the default. The
  naive fix — recording `False` after a pages-only run — would have classified every one of those
  products HELD and made `--only links` refuse to write the records it exists to write: the
  pages→links handoff breaking, the same class of bug as #38.

  It is now `retracted: bool = False`, which is what it records. The complaint **evaporates rather
  than being documented around**: a pages-only run's entry says `retracted: false`, which is true,
  because the product was never retracted. Whether a resolver record exists is already recorded one
  field up — an empty `gs1_link_set_hash` — so the rename adds no state. A state file carrying the
  old key is translated on the way in, inverted; pydantic drops unknown keys by default, and
  dropping this one would put every deliberately retracted product back on the site. That is the
  one migration failure with a live consequence, so a test asserts it rather than a default.

  **Two defects found while verifying it**, both reachable through `--revive` — which no skill
  passes, but which `docs/troubleshooting.md` and `docs/wordpress-onboarding.md` both point
  operators at:

  `--revive --only pages` on a retracted GTIN classified **UNCHANGED** afterwards. `_finish_pages`
  carries the *prior* `gs1_link_set_hash` forward and the retraction never cleared it, so the
  revived row read as fully linked — its content hash matches, which is what made the gap
  invisible. The resolver record stayed retracted for good, with no gate, plan or report saying
  so. Retraction now blanks the hash, because retraction **deletes the links**, and the existing
  `_has_no_resolver_link` rule reports the row CHANGED with the `gs1_link` diff the operator
  already reads at gate 6.

  `--revive --only links` after an *interrupted* take-down — resolver retracted, pages still
  published, the case `_is_held`'s docstring is about — left the product **HELD permanently**
  though `safe_upsert(is_enabled=True)` had just written the record back. `_commit_state` updated
  the hash and left the flag. It now clears `retracted` alongside, which is safe in both
  directions: a held row only reaches there under `--revive`, and `_is_held` is an OR, so a
  product whose pages are still drafts stays held. All three are asserted.

  One knock-on, checked rather than left to chance: `run_plan`'s pilot gate calls a GTIN finished
  when every language carries a link-set hash, so a retracted one now re-enters the plan as HELD
  instead of vanishing into the `already_present` tally — the better outcome, since the plan-review
  gate names it, and `run_execute` still drops it without `--revive`. Invisible on live data: all
  20 entries are published, enabled and linked.

- **The only button gate 6 could render cancelled the run, irrecoverably.** `apply` and `skip` are
  `chat_only` — they belong to the conversational per-row walk — so the Publish screen's per-row
  diff gate offers exactly one control, *Show full diff*, and clicking it ended the run. It was
  marked `proceeds=False` for the chat flow, where it prints the rest of the diff and re-prompts,
  so "does not advance" is true of it there. On a form surface it is the **terminal** answer to the
  gate: `PublishSession.cancelled` counted every answered-and-not-proceeded gate, and the execute
  panel then refused with *"A gate was answered with cancel."* Nothing un-answers a gate, so the
  only way on was to reload the page and answer every gate again — reached by choosing *Review
  changed*, the most careful answer the plan-review gate offers.

  One boolean was answering two questions — *does this advance the flow* and *does this stop the
  run* — which coincide everywhere except on a detour. `GateOutcome` splits them into `ADVANCES`,
  `STOPS` and `REDISPLAYS`, in `lib/gates.py` where the contract lives rather than as a special
  case in the screen; `proceeds` and `refuses` are derived properties, so no call site changed, and
  the bare positional `False` that made this hard to see at a glance is gone from every option.

  Four options are detours, not refusals: `show-full-diff`, `change-mode`, `regenerate`, `detail`.
  The middle two sit at **required** gates, so the run is held either way — what changes is that
  the screen now says *"Still to answer: Intent confirmation"* instead of repeating the same false
  claim about a cancellation nobody made. The band that does report a refusal names the gate and
  the answer rather than describing an unnamed one.

  Also closed: `execute_argv` consulted only `outstanding`, which is **required** gates, and a
  refusal is *answered* — so gate 4's *Stop the run*, on a gate that is deliberately not required,
  built a command. Its documented "abort before execute" was enforced by the publish screen
  returning early, which is display logic standing in for the function whose entire docstring is
  about refusing. Both checks exempt the dry-run gate, since the preview writes nothing and must
  stay re-runnable after being cancelled.

  `grep -rn show-full-diff tests/` was empty, which is what let this through. Six tests, two of
  them pinning the class rather than the instance: **no gate whose every shell option refuses**,
  and a redisplay option is never read as a refusal — the mirror of the existing
  `test_cancel_never_reads_as_consent`, which had no converse. That test and the one asserting
  every required gate offers a way out are
  strengthened from `not proceeds` to `refuses`: with a third outcome the weaker form would accept
  a `cancel` quietly turned into a redisplay, which is the failure that would make `cancelled`
  return `False` on a cancel.

- **`run_generate` had no idea what scope was, and asked for copy the run would never publish.**
  `grep process_list scripts/run_generate.py` returned nothing: the products file was taken whole,
  so `pending_requests` ran over the entire catalogue. The doctor and `--emit` answered the same
  question two orders of magnitude apart — on the pilot client, **224 requests emitted where 10
  are in scope**. That is real tokens and real time spent writing copy for products nobody is
  publishing, and a content-review gate with hundreds of units in it, which is the surest way to
  make a review gate go unread.

  The filter goes in `_prepare`, once, because all three producer paths run through it — `--emit`,
  `--ingest` and `--backend api`. It is `lib.preflight.in_scope`, imported rather than
  reimplemented: the same function the doctor's `scope` check reports, so the two commands now
  agree **by construction** rather than by coincidence. Live: 224 → 10 emitted, coverage
  denominator 254 → 30, against the doctor's `total=30 pending=10`. A test asserts that agreement
  by computing both sides independently — a cross-check, not a restatement, since they disagreed
  22× before and nothing anywhere noticed.

  Narrowing happens *before* `prefill_from_feed`, and that is the one behaviour change to notice:
  the verbatim prefill now fills in-scope units only, so the cache stops accumulating copy for
  products this client is not publishing — the same unbounded growth the Content screen had to
  fold away.

  **`--emit` still saves the cache**, deliberately: the prefill runs before the gaps are computed,
  and persisting it is what lets emit and ingest be called in either order. What was wrong was
  doing it silently. It is now named in `--help`, in the module docstring's Emits block, and in
  the line the command prints — a command called `--emit` writing a second file it never mentions
  is a surprise found by noticing an mtime.

  `_ingest` gained a third reason for skipping. It matches results to pending requests and skips
  the rest as "already fresh or input changed"; a results file emitted before the process list was
  pruned now also lands there, and reporting it that way would send a reader to the feed to
  explain a scope decision. The warning names which of the three it was, and the
  `content-generator` skill documents it.

  None of the 14 existing tests would have caught any of this — no test config carried a
  `process_list`, so `in_scope` was a no-op for every one of them.

- **The copy review listed the whole cache, under a coverage figure that was scoped.** The Content
  screen showed the doctor's figures — correctly scoped to the run — directly above a list that
  read `generated_cache.json` off disk and rendered every GTIN in it, captioned "N GTIN(s) in the
  cache". One screen, a scoped number and an unscoped list, and nothing distinguishing them.
  Nothing prunes that file, so it accumulates every unit ever generated on the machine: a
  two-product batch eventually sits under a list of hundreds.

  Scope is not recomputed in the shell. `check_scope` now reports **`in_scope_gtins`** beside the
  two counts, so a consumer can *filter* by scope rather than only report it, and
  `lib.preflight.in_scope` stays the single implementation. The list is `ProductRecord.gtin`
  verbatim — the field the generator keys its cache by — because a normalised variant would
  silently match nothing for a 13-digit feed, and would fail looking like "nothing is in scope"
  rather than like a bug. It is deliberately **uncapped**, unlike `pending_units`: that is a list
  to read, this is a list to filter with, and a truncated filter hides in-scope work.

  `ui.context.split_cache` does the division as a pure, separately tested function: this run's
  entries, the rest, and the in-scope GTINs with no entry at all — the last being the interesting
  set, since that is the copy still to be made. An unknown scope returns everything with
  `scoped=False` rather than an empty split, and the screen says so: filtering to nothing would
  read as "there is no copy", wrong in the direction that stops an operator looking. Out-of-scope
  entries are folded behind a count, not dropped.

  Coverage and the review now come from **one** preflight run — they answer the same question at
  two zoom levels, and a re-check that moved the count without moving the list would restore the
  disagreement being fixed. The import handler refreshes both, since they describe the file it just
  replaced.

  On the live pilot: 16 entries cached, 10 in scope, 6 folded, and 5 in-scope GTINs with no entry —
  exactly the five `plan.json` records as E21 `no_generated_copy`, with 10 + 5 matching the
  doctor's in-scope count of 15.

- **Gate 0 gave the size of the catalogue where the operator was asking about this run.** The
  intent gate led with `context.product_count` — the length of `products.json` — under the label
  "products in the catalogue". Honest, and the wrong number: during the install rehearsal it read
  **127** on a run scoped to one product. Gate 0 is where the operator forms their picture of what
  they are about to do, which makes it the worst place in the flow for the prominent figure to
  describe something other than this run.

  The fix is *not* to compute scope in the shell. `lib.preflight.in_scope` already composes the two
  gates that decide it — the process list, then the confirmed-video allowlist behind
  `media.restrict_to_mapped_gtins` — and a second implementation of "what will this run touch" is
  the same class of mistake as a second implementation of the operator gates. So the screen reads
  the doctor's `scope` check: **15 in scope**, **127 in the catalogue** one size down, and the
  doctor's own sentence naming what removed the rest, since *15 of 127* otherwise leaves a reader
  guessing whether that was intended.

  `ui.context` gains `Scope` and `scope_from`, plus `doctor_check` — which was `_find`, copied
  verbatim into two screens, and this would have been the third. An unreadable payload yields
  `None` and the gate shows a dash; it never falls back to the catalogue total, because a wrong
  number under the right label is worse than no number — the label vouches for it. `None` and
  `in_scope=0` stay distinct: zero is actionable and alarming, absent warrants no conclusion. An
  empty scope gets a danger band, that being the outcome this project keeps designing against.

  **One `doctor --json --offline` per redraw**, hoisted into `_redraw` and shared. Gate 3 already
  ran one; a second for gate 0 would have been ~500 ms of blocking subprocess on every answer, in
  a function already holding the event loop — and the two gates could have reported different
  numbers for the same run, which is the more expensive half. A contract test now fails if any
  gate renderer runs its own.

  Both surfaces moved together, because the gates are one contract. `SKILL.md`'s gate 0 leads with
  scope, cites where the number comes from, and keeps the "not the row count" caveat — now with
  the reason: scope deliberately cannot subtract the already-published units, because deciding
  that needs `state.json` and an idle read of a corrupt one quarantines it (E19). On the live pilot
  the two read 15 and 5, so "could touch" is never "will publish". The intent gate's own `purpose`
  promised "the catalogue size" and was stale the moment the screen changed; it moved too.

- **A gate asked about nothing, on every run, and its only live control was the destructive one.**
  `lib/gates.py` filtered the nine gates on mode, generator and environment and never on the plan,
  so the missing-field prompt (step 4) rendered whether or not `run_plan` had dropped anything: a
  card headed *"one per unit dropped for a missing `product_name`"* above a button reading *"Skip
  this unit"*, with no unit. The pilot client's own plan is the case — 10 dropped units, every one
  of them E21 `no_generated_copy`, not a single E18 — and the gate rendered over it.

  Of its three answers, `skip-row` and `ask-me-later` proceed and do nothing; only `fail-run` has
  an effect, setting `PublishSession.cancelled`, which makes the execute panel refuse. So the one
  operable control on a question about nothing was the one that stops the run. That is worse than
  clutter: this screen's entire safety argument is that gates are *read*, and a gate that appears
  with nothing to ask teaches answering without reading — a habit spent at the gates that matter.

  `Gate` gains `needs_missing_product_name`, and `applies`/`gates_for` a matching keyword-only
  `has_missing_product_name` with **no default**. A defaulted applicability input is precisely the
  drift this module exists to prevent — a caller that forgets one gets a walk quietly missing a
  gate, and a gate that stops being shown raises nothing — so a handful of call sites is a cheap
  price for a `TypeError` instead of a silence, and a signature test now pins that for all three
  inputs rather than leaving it to be re-argued. `lib/gates.py` stays stdlib-only: a `bool` crosses
  the boundary, never a `Plan`.

  `PublishSession` carries the dropped units themselves rather than a flag, so the value that hides
  the gate and the value that names them are one value and cannot disagree — two reads of
  `plan.json` could, and the way they would is the gate rendering over an empty list, the same
  defect with a condition on top. The Publish screen refreshes it at the top of **every redraw**,
  not in `__init__`: the plan is built at step 5, in the middle of the walk, so a fact read once
  when the screen was built is the fact from before there was a plan.

  The screen now names each dropped unit by GTIN, language and `run_plan`'s own wording, and says
  outright that nothing here can supply the name — it is filled in MyGS1 and re-exported. Building
  a plan that drops units toasts that the gate has appeared above, since a gate materialising above
  the one your hands are on should be announced rather than noticed.

  **The Gate index's Modes column is now machine-checked**, in both directions. It is prose, it
  said `all`, and only the ids and step numbers were ever compared to the code — which is how this
  shipped. A flag a cell does not name, and a cell naming a condition its gate does not carry, now
  both fail CI.

- **`delete_media` would delete any attachment it was given an id for, and orphans were never
  cleaned up.** Two halves of one gap, found by asking a question the code could not answer:
  *is there anything stopping us destroying content that was on the site before this project?*

  For pages the answer was yes, and had been all along — `_guard_gtin_match` re-reads the page
  and refuses unless its `meta.gtin` matches, on `upsert_page`, `set_status` and `delete_page`
  alike. For media there was no guard at all; the docstring said as much, reasoning that media
  carries no GTIN to check against. It carries something just as good, and the omission was not
  academic: **366 of the 406 attachments in the pilot site's media library are the client's own**,
  uploaded long before this tool existed.

  `meta.content_sha256` — written on every attachment `upload_media` creates, empty on everything
  else — is now the ownership key, and `delete_media` re-reads the attachment and refuses unless
  it is non-empty. Unreadable or empty meta counts as *not ours*, which is the conservative
  direction: at worst it declines to remove an orphan of our own, which is recoverable, rather
  than deleting a client's product photo, which is not. The one caller that legitimately deletes
  an attachment carrying no hash yet — the truncated-upload path, where the hash is written by a
  finalise call a bad upload never reaches — goes through a private unguarded delete, and holds
  proof no lookup could improve on: the id came out of its own `POST` response moments earlier.

  With that in place, **media uploaded by a row whose page write then fails is taken back down.**
  Those orphans were invisible to everything: `scripts.reconcile` compares *pages*, and a failed
  row records no state, so nothing in the tool could ever have found them. The rollback is bounded
  three ways — it touches only ids from *this* row, only ones `upload_media` reports it actually
  created (a deduped id belongs to an earlier run and is very likely carried by a live page), and
  only while no page has been written. That last boundary is exact: `verify_url` fails *after* the
  page exists and already references the media, and rolling back there would turn a failed row
  into a live page with broken media. There is deliberately **no sweep** for orphans from earlier
  runs — an attachment has no ownership key beyond the content hash, so identifying one would mean
  inferring it, and inference is not a thing to do with a `DELETE` against a live site.

  `upload_media` now returns a `MediaUpload` (`media_id`, `created`) rather than a bare int,
  because created-versus-reused is the distinction the rollback turns on and it cannot be
  recovered from the id.

- **A truncated upload was reported as a success, and dedup then made it permanent.** A live
  transfer was cut off mid-upload, leaving a 1.5 MB fragment of an 8 MB video in the media
  library. WordPress answered `201`, the run called it done, and the page published against a
  video that would not play — 200, QR resolving, everything looking healthy until someone
  pressed play.

  The half that made this more than a one-off: dedup is a lookup on a content-addressed slug
  folded from the SHA-256 of the *local* bytes, and `_find_media_by_slug` returned the first hit
  without checking anything about it. So the fragment was stored under the hash of the whole
  file and returned by every subsequent run as a content match. **Re-running could not repair
  it**, which is what makes this load-bearing rather than tidy-up.

  `upload_media` now compares the stored byte count against what it sent, on both paths. On the
  create path the check sits **between the upload and the finalise call** — the finalise is what
  claims the content-addressed slug, so a fragment never becomes the answer to its own hash; it
  is deleted and `MediaIntegrityError` is raised. On the dedup path a hit whose size disagrees is
  deleted and re-uploaded, which is what reaches the fragment already sitting on the site.

  The size comes from `media_details.filesize` on the create response, falling back to a `HEAD`
  on `source_url` for the attachment types and WordPress versions that do not supply it. When
  neither will say, the upload is logged as **unverified** and allowed — never silently, and
  never deleted: `delete_media` has no ownership guard, so treating "no number" as "wrong number"
  would remove a live page's media because a proxy omitted a header. A cleanup that itself fails
  is reported as part of the integrity error rather than replacing it, so the operator is told
  both what was wrong with the file and that the fragment is still there.

  An upload failure deliberately fails the row rather than degrading to `None` like the media
  *resolution* steps do under E7. E7 is media that was never available; this is media that is
  wrong, and publishing a page against it while reporting success is the worse outcome. The
  `_row_media` docstring claimed the opposite and has been corrected.

- **A failed row said only that it failed, not which call failed or what the server said.**
  A live publish stopped on a video upload that WordPress answered with a bare HTML `403`, and
  the run log — the only durable per-row record this tool keeps — recorded exactly this:

  ```json
  "error": "WordPressAPIError('WordPress API error 403')"
  ```

  A row issues a page write, an ACF write, a URL verification and up to two media uploads, and
  nothing distinguished them: learning it was `POST /wp-json/wp/v2/media` for one video took a
  re-run with the output captured to a file. The HTML naming the culprit — a security plugin
  inside WordPress, not a firewall in front of it — went to stderr and nowhere else, and there
  is no file logger, so on a scheduled run it would simply not exist. That absence is most of
  why the first diagnosis was wrong in both of its conclusions.

  Both halves were already in hand and thrown away one line apart. `_api_error` receives the
  endpoint and the label of every request (`upload media clip-a1b2c3d4e5f6`), logs them with
  the scrubbed body, and then built the exception with neither. `WordPressAPIError`,
  `GS1APIError` and `LLMAPIError` now carry a `call` and fold it, plus a scrubbed and bounded
  excerpt of the response body, into the message — which is what `repr(exc)` renders and
  therefore what reaches `runs/*.jsonl`. `RunOutcome` gains `failed_call`, and the Runs screen
  leads with it.

  Three details worth stating. The excerpt is scrubbed by the same `scrub_response_body` the
  logger uses and the existing "secrets never appear in logs" test was extended to cover this
  new path, because it ends in a *file*; `response_body` still holds the raw text. Whitespace
  is collapsed, so an HTML block page's identifying first lines fit inside the bound instead of
  being spent on indentation. And `run_execute` follows `__cause__` when reading the call back,
  because `_verify_targets` deliberately re-raises as a `RuntimeError` — the one path that
  already adds context would otherwise have been the one to lose it.

  `failed_call` is optional, the same back-compat move `StateEntry.title` makes: `ui.context`
  counts an unparseable run-log line as *unreadable* rather than raising, so a required field
  would have quietly hollowed out every log already on disk. `lib/errors.py` also gained its
  first dedicated test module, and the 500-character bound, which had been declared twice
  independently, is now defined once.

- **The operator shell could not publish pages to a production client at all.**
  `run_execute` refuses every real run against `gs1.environment: production` without
  `--i-understand-production`, and its condition does not look at `--only` — `pages` is
  treated exactly like `links`. But the production gate is deliberately absent from the
  `pages` walk, so `PublishSession` had no answer to derive the flag from and never appended
  it. Every real pages run from the shell ended at exit 2. The *reversible* half of a publish
  was the unreachable one.

  `CLAUDE.md` already described the intended behaviour — "in `pages` mode that confirmation
  is gate 0" — and nothing implemented it. `PublishSession.production_acknowledged()` now
  makes the substitution explicit: the production gate's answer where that gate is in the
  walk, gate 0's where it is not, and never anything derived from `gs1.environment`, because
  the flag records that a person confirmed rather than a fact about config.

  The existing test asserted the broken behaviour and passed, because it checked the shape of
  the argv and never that `run_execute` would accept it. It now asserts the flag is present,
  and says in its docstring why the old assertion looked reasonable.

- **The dry-run gate answered itself, and offered no way to decline.** `lib/gates.py`
  declares `Proceed` and `Cancel` for step 8.5; the screen rendered neither and set
  `answers["dry_run"] = "proceed"` when the subprocess finished. So the gate was answered by
  the run *completing* rather than by anyone approving what it printed, and Cancel was
  unreachable at the one gate whose whole purpose is to be read before the real write. It
  also never redrew, so the "still to answer" banner stayed on screen and the write button
  never appeared — the state was right and only the display was stale.

  The buttons now appear once there is output to approve, and running the dry run no longer
  answers it.

  `tests/ui/test_publish_contract.py` guards the class rather than the instance: no gate that
  declares options may write its own answer, and every *required* one must offer a way to
  answer it. Both checks are AST-only, so they run in CI where NiceGUI is absent. They also
  surfaced two more gates — `row_diff` and `post_run` — that declare options the screen never
  renders; those are informational and not required, and their declared options promise
  behaviour that does not exist (`show-full-diff` renders no fuller diff), so they are pinned
  in a named set rather than papered over with buttons that would lie.

- **Pruning the process list in more than one pass saved rows other than the ones on
  screen.** The Data screen's grid keys each row by its position when the grid was built,
  and that key never changes; a `ProcessListSheet` renumbers on every edit. `remove()` fed
  those fixed keys into `without()` on the *previous result*, so the first removal was
  correct and every one after it deleted the wrong row.

  Remove row 0, then row 3: the grid shows rows 1, 2, 4 and the file receives rows 1, 2, 3.
  The save then reports success. Since the process list *is* the scope of a run, that is a
  live page and a permanent, undeletable GS1 record for a product the operator did not
  choose — with nothing on screen to suggest anything went wrong.

  `remove()` now rebuilds from the surviving keys against the sheet as first read, via a new
  `ProcessListSheet.keeping()`, so nothing accumulates and drift is not expressible. The
  regression is covered from both sides: the new form agrees with the grid after two passes,
  and the incremental form is asserted to disagree, so the test cannot quietly stop testing
  anything. `without()` keeps its behaviour and gains a docstring saying what it is for.

  Found while rehearsing a from-scratch operator install, one selection away from being
  invisible: pruning 37 rows in a single pass — which is what the operator did — takes the
  correct path.

- **Both uploads in the operator shell wrote nothing, silently.** NiceGUI 2 handed the
  handler `event.content`, read synchronously; NiceGUI 3 replaced it with `event.file`,
  whose read methods are awaitable. The `ui` extra allowed `nicegui>=2.0`, so a fresh
  install resolved 3.x against code written for 2.x and broke the export upload on the
  Data screen and the copy-cache import on the Content screen at the same time.

  The shape of the failure was the problem. The handler raised inside NiceGUI, which
  logged it to the terminal; the browser showed the file at 100% with a checkmark and no
  error; nothing reached disk. An operator saw a successful upload followed by a parse
  insisting the file did not exist. Reporting success while doing nothing is the outcome
  refused everywhere else in this project — `ui/session.py` raises rather than build a
  command past an unanswered gate, and an empty plan is refused rather than run — so it
  should not have been reachable here.

  Both handlers are now `async` and `await event.file.save(path)`. The extra is bounded to
  `nicegui>=3.0,<4`: an unbounded major range on the one dependency the screens are written
  against buys nothing the lockfile does not already give. `tests/ui/test_upload_contract.py`
  asserts both sides — an **AST** check that every handler taking an upload event is `async`
  and never reads the removed `.content` (no NiceGUI needed, so it runs in CI), and a live
  check that the installed NiceGUI still offers `file` and an awaitable `save` (skipped
  where the extra is absent).

  Found on the first from-scratch operator install, which is the argument for doing one.
  Note also that CI does not exercise the screens at all: it installs `.[dev]`, which is
  what keeps `lib` provably free of a UI dependency, and leaves `ui/pages/` uncovered.

### Changed
- **The run log is written as the run goes, not once at the end.** `run_execute` used to
  collect every `RunOutcome` in memory and write `output/{client}/runs/{ts}.jsonl` after
  the last GTIN finished, so anything that killed the process — `^C`, a dropped terminal,
  an error escaping the client setup — discarded the *entire* record, including rows that
  had already published live pages, registered permanent GS1 records, and been committed
  to `state.json`. Each outcome is now appended and flushed the moment it is final, which
  is when its GTIN completes (a row's outcome stays mutable until then: one language
  failing rewrites every row of that GTIN to `error`).

  Two things follow. A crashed run leaves an account of what it managed to do. And the
  file is tailable, which is the only progress channel this script has — a real run logs
  at `WARNING`, so a clean one emits nothing at all until its closing line. The log path
  is therefore now printed to stderr at the **start** of the run as well as at the end;
  nothing outside the process could previously work it out, since the name comes from a
  timestamp the process picks.

  Rows land in completion order rather than plan order. For every plan this tool writes
  those are the same thing — `diff_against_state` builds rows grouped by GTIN — but a
  hand-shuffled plan will now log grouped by GTIN.

  Same-second runs no longer share a file: the name is a timestamp to the second, so the
  second run gets `{ts}-1.jsonl`, claimed with an exclusive create so two processes cannot
  both take it. Concurrent runs remain unsupported (**E20**) — `state.json` still races —
  this only stops one run's log from being scrambled by another's.

- **BREAKING — `website_status` is now `process_list`, and every GTIN in the file is
  processed.** The old control file carried "already on website" / "already in GS1"
  columns read by *presence*: any non-blank cell meant `True`. That is correct only for
  files marking rows with `X`. A client whose file said `no` got the opposite of the word,
  silently, and in both directions — a wrong "on website" emptied the plan and the run
  reported success having published nothing, while a wrong "in GS1" made a product
  eligible and pointed the pipeline at a GTIN with no resolver record. Neither raised.

  The tool now interprets nothing. The file is a list of GTINs; being on it is the whole
  meaning; the operator prepares it by deleting the rows that should not run, applying
  whatever rule their business uses. Only the GTIN column is configured (`gtin_column`,
  relabelable), and every other column is ignored, so operators can keep working notes
  beside the barcodes.

  **Migration:** rename the `website_status:` block to `process_list:`, keep `path` and
  `gtin_column`, drop `on_website_column` / `in_gs1_column` / `site_link_column`, and
  **prune the file to only the rows that should run**. A stale `website_status:` key is
  rejected at config load (`additionalProperties: false`), so the gate cannot silently
  disappear. `lib/website_status.py` → `lib/process_list.py`; `WebsiteStatusConfig` →
  `ProcessListConfig`; `WebsiteStatusError` → `ProcessListError`.

  A process list that parses to **zero** GTINs is now an error rather than an empty run,
  for the same reason: an empty plan and a successful-looking no-op is the failure mode
  this project keeps designing against.

### Added
- **Three publish flows.** `/gs1-pages` (WordPress pages only, reversible), `/gs1-links`
  (Digital Links only, aimed at pages that already exist) and `/gs1-publish` (both) —
  three thin skills in `.claude/skills/` over one shared gate sequence. Natural-language
  requests for a single leg route through `flow-orchestrator`, which classifies the mode
  and confirms it.
- `scripts/run_execute.py --only {pages,links}`. Omitting the flag does both, so every
  existing invocation is unchanged. The skills supply it after the intent gate; operators
  do not type it.
- **A target-URL precondition for `--only links`, in code rather than skill prose.** Each
  target is resolved from `state.json`, else a slug lookup, else the plan row's
  `target_url`, and must serve 2xx/3xx before the resolver is written; a GTIN with any
  unverifiable target gets no GS1 write. A GS1 record can never be deleted, so a permanent
  QR target on a 404 is unrecoverable — and instructions in a skill can be skipped. The
  same code path now also verifies languages `_known_pages` rebuilds from state on the
  both-flow, which were previously trusted unchecked.
- **Gate 0** in `flow-orchestrator`: mode, an export-file cross-check against
  `clients.yml` `export.path`, product count, environment, and the permanence warning for
  anything that writes to GS1. In `pages` mode it also stands in for the production
  environment gate, since nothing irreversible follows.
- `docs/setup.md` gains a "Which flow do you need?" section; `docs/troubleshooting.md`
  gains the links-refusal and pages-leave-rows-CHANGED entries.

### Changed
- `lib/state.py` classifies an entry with an empty `gs1_link_set_hash` as CHANGED, with a
  `gs1_link` diff row. That is what a `--only pages` run leaves behind; its content hash
  still matches, so without this a follow-up `/gs1-links` would find every row UNCHANGED
  and publish nothing while reporting success. HELD still outranks it. Every state file
  written before `--only` existed carries a real digest, so nothing already live
  re-classifies.
- `run_execute` commits a GTIN's state once every selected leg has succeeded rather than
  inside each leg, preserving all-or-nothing semantics across the split.
- The production-guard refusal names only what the selected leg actually does — `--only
  pages` no longer claims it would register permanent GS1 records.
- The `wordpress-product-page`, `gs1-digital-link` and `qr-render` skills now document the
  Python API in `lib/` instead of MCP tool calls, which was never how the orchestrated path
  worked. The MCP servers stay in `mcps/` and stay in CI, but they expose a strict subset:
  the GS1 server has 3 of 7 methods, and the WordPress server has neither
  `link_translations` nor the take-down path, so neither a multilingual publish nor
  `run_unpublish` could go through them. There is no `.mcp.json`.
- `docs/setup.md`'s `ffmpeg` prerequisite row says **video only** — image conversion is
  Pillow, already a dependency. The row was correct but linked to a section covering both.

## [0.1.0] - 2026-07-30

First working release. The tool turns a GS1 Data Source (GDSN) export into GS1 Digital Link QR
codes and multilingual WordPress product pages, and it has been proven end to end on a live
pilot: 10 GTINs published to a production site in Dutch and French, each with an enabled GS1
production record whose QR resolves to the right page, and one confirmed scanning from a printed
label. `0.0.1` was a repository skeleton; everything below is what filled it in.

### Added
- `lib/errors.py` — typed exception hierarchy (`OrchestratorError` base plus
  `GS1APIError`, `ConfigError`, `MissingCredentialError`, and others) per
  `docs/IMPLEMENTATION_SPEC.md` §4.1.
- `lib/logging_setup.py` — `scrub_response_body` and `scrub_headers` that redact
  secrets and `meta.*` from log output (§5.2).
- `lib/gs1_dl_client.py` — synchronous GS1 NL Digital Link API v2 client
  (`upsert`, `upsert_bulk`, `get`, `set_enabled`, `validate_draft`) with the §5.1
  retry policy, structured 400 `ErrorResult[]` parsing, and token-scrubbed
  logging. Path-case anomalies (capital-L `digitalLink` for GET/PATCH; no `/v2/`
  in ValidateDraft) preserved.
- `mcps/gs1-nl/` — TypeScript MCP server exposing three tools
  (`gs1_digital_link_upsert`, `gs1_digital_link_upsert_bulk`,
  `gs1_digital_link_get`) over stdio, resolving client config from `clients.yml`
  (§9.1); mirrors the Python client's hosts, paths, auth, and retry policy.
- Tests: `pytest`/`pytest-httpx` for the Python client (idempotency, retry,
  error parsing, token scrubbing) and `vitest` for the MCP client, config, and
  tools (including end-to-end tool calls over an in-memory transport). A skipped
  fixture-backed test slot awaits captured GS1 responses (§13.2).
- CI: a Node job builds and tests the `mcps/gs1-nl` workspace.

- `gs1_dl_client.safe_upsert()` + `OverwriteError` — a GET-before-write guard that
  refuses to overwrite an existing Digital Link unless `overwrite=True` and returns
  the prior snapshot for rollback (§5.4). Prevents silently clobbering a live
  resolver target on production runs.

- **Phase 3 — Excel parser + records schema.**
  - `lib/records.py` — canonical `ProductRecord`/`LocalisedText` plus `Plan`,
    `PlanRow`, `ConfirmedPlan`, `RunOutcome`, `StateEntry`, `State` (§2), and the
    flat-export `parse_excel_row` (§4.9).
  - `lib/gdsn.py` — reader for GS1 Data Source / GDSN datapool exports (multi-sheet,
    7 header rows, `Gtin` + `TargetMarketCountryCode` composite key, `LanguageCode`/
    `Value` pairs). Joins sheets by GTIN into `ProductRecord`s via a per-client
    attribute map. A spec extension over §2/§3's flat single-sheet assumption.
  - `lib/config.py` — `clients.yml` loader (`load_clients`/`get_client`) with
    jsonschema validation, `defaults` inheritance, lazy secrets, and the
    `GS1Config.resolve()` bridge to the Phase-2 client shape (§2.4, §4.2). Extended
    `ExportConfig` with `format`, `market_language`, `gdsn_map`, `gdsn_extras`.
  - `scripts/parse_export.py` — GDSN- and flat-aware CLI producing
    `output/{client_id}/data/products.json` (§8.1).
  - `scripts/inspect_export.py` — onboarding utility that lists worksheet attributes
    and suggests a `gdsn_map` (§8.5).
  - `schema/clients.schema.json` — `export` block extended for the GDSN format.
  - Pilot: Democlient's real GDSN export parses to 127 products (nl + fr) with zero
    warnings.

- **Phase 4 — WordPress client + MCP.**
  - `lib/wp_client.py` — synchronous WordPress REST API v2 client (§4.4): HTTP Basic
    auth with a lazily-resolved application password, the §5.1 retry policy (429/5xx
    with independent budgets; a `401` is terminal), idempotent `upsert_page`
    (3-step lookup id → slug → `meta.gtin`, §6.1), SHA-256-deduped `upload_media`
    (§6.2), `find_by_slug`, `verify_url`, `download_image`, `detect_multilingual_plugin`,
    and token-scrubbed logging. Edge cases E7 (image 404 → featured media skipped),
    E8 (mismatched `meta.gtin` → `GtinMismatchError`, skip row), E11 (non-GTIN slug
    collision → `WordPressAPIError`, human intervention).
  - `lib/multilingual.py` — `MultilingualAdapter` strategy with `PolylangAdapter`
    (translation linking via `/wp-json/pll/v1/`), `NoOpAdapter`, and a `WPMLAdapter`
    stub that raises `NotImplementedError` (WPML lands in v0.2) (§4.5).
  - `lib/errors.py` — added `GtinMismatchError` (the WordPress sibling of
    `OverwriteError`) so E8 is distinguishable from E11.
  - `mcps/wordpress/` — TypeScript MCP server exposing five tools (`wp_upsert_page`,
    `wp_upload_media`, `wp_find_by_slug`, `wp_verify_url`, `wp_detect_multilingual`)
    over stdio, resolving client config from `clients.yml` (§9.2); mirrors the Python
    client's auth, retry, idempotency, and E8/E11 semantics. README documents the
    adopt-vs-fork survey (§8.2): no off-the-shelf WordPress MCP provides per-client
    credentials, GTIN-keyed idempotency, or Polylang linking, so the client forks the
    in-repo `gs1-nl` pattern.
  - Tests: `pytest`/`pytest-httpx` for the Python client and adapters (detection,
    §6.1/§6.2 idempotency, E7/E8/E11, retry, secret scrubbing) and `vitest` for the
    MCP client, config, and tool wiring. A `staging`-marked
    `tests/integration/test_wp_staging.py` holds the three live-staging DoD checks
    (Polylang detection, §6.1/§6.2 idempotency, published-page exit gate), skipped
    unless the staging env is configured.
  - CI: a Node job builds and tests the `mcps/wordpress` workspace.

- **Phase 5 — QR + templates.**
  - `lib/templates.py` — `TemplateEngine(client_id, template_config)` rendering a
    `ProductRecord` into a localised HTML fragment via Mustache/`pystache` (§4.6, §3.4).
    Client-override-first, `_default`-fallback resolution (missing template →
    `TemplateError`); the §3.4 variable vocabulary with per-language text resolution;
    edge E12 (unknown `{{extras.*}}` key → empty render + one WARNING) and E13 (data
    containing `{{`/`}}` or HTML is escaped and never re-parsed).
  - `templates/_default/product.{nl,en,fr}.html` — default product templates; and
    `templates/democlient/product.{nl,fr}.html` — the pilot's first templates, surfacing
    the Democlient `functional_name` extra (§6.5, §5.5).
  - `lib/qr.py` — `render_qr(uri, output_dir, gtin, formats, size_mm, ecc, dpi=300)`
    writing SVG/PNG/EPS Digital Link QR files (§4.7). Applies the uppercase-domain
    optimisation (scheme + host uppercased, path preserved) for alphanumeric-mode symbols;
    the SVG is emitted from the QR module matrix for exact millimetre sizing and
    byte-identical determinism (§6.4); PNG/EPS via Pillow.
  - `mcps/qr-render/` — self-contained TypeScript MCP exposing one tool (`qr_render`)
    over stdio (§9.3). Uses npm `qrcode` for PNG and emits SVG/EPS from the module matrix
    (npm `qrcode` has no EPS writer), mirroring `lib/qr.py`'s uppercase-domain transform
    and output shape.
  - Tests: `pytest` for the template engine (resolution order, variables, E12/E13,
    `TemplateError`) and QR renderer (§6.4 byte-determinism, formats/ordering, uppercase
    transform, ECC mapping, physical sizing); `vitest` for the MCP renderer and
    end-to-end tool wiring.
  - CI: a Node job builds and tests the `mcps/qr-render` workspace.
  - Manual print+scan gate (§8.2 exit gate): 20 mm QR scanned successfully on
    Android (2026-07-12). iOS scan still pending before the gate is fully met.

- **Phase 6 — lib, scripts, state.**
  - `lib/state.py` — per-client run state over the `State`/`StateEntry` models (§4.8):
    `load_state` (empty when absent), `save_state` (atomic write-to-temp-then-`os.replace`,
    so a crash mid-write leaves the prior `state.json` intact, never a partial one), and
    `compute_content_hash` (deterministic SHA-256 over canonical JSON of product +
    language + target URL). `diff_against_state` is deferred to Phase 7, where `run_plan`
    supplies the slug/title inputs a `PlanRow` needs.
  - `scripts/run_execute.py` — deterministic, resumable execution of a confirmed plan
    (§8.3): per `(GTIN, language)` row it renders the template → upserts the WordPress page
    → verifies the URL returns 200 → sets the GS1 resolver target via `safe_upsert`
    (GET-before-write, `overwrite=True`; §5.4) → renders the QR. One `RunOutcome` per row is
    appended to `output/{client_id}/runs/{ts}.jsonl` regardless of success; successful rows
    update `output/{client_id}/state.json`. Exit codes `0`/`1`/`2`. `--dry-run` (§5.4 Level
    B) previews intended mutations without performing them (no HTTP writes, no QR, no state).
  - Tests: `pytest` for `lib/state.py` (round-trip, content-hash determinism, `StateError`,
    and the §12 kill-mid-write atomicity check — including a SIGKILL-during-write subprocess
    test) and `scripts/run_execute.py` (happy path, §6.5 double-run idempotency, verify-failure
    error path, `--dry-run` no-mutation, `--confirmed` subset, config-error exit code) with
    the WP/GS1 clients faked. A `staging`-marked integration test drives `run_execute`
    end-to-end for one GTIN against real WordPress staging + the GS1 **production**
    environment, then re-runs to assert §6.5; skipped until that infrastructure is configured.
  - DoD note: the live end-to-end exit gate and live §6.5 check run via the staging test. The
    GS1 sandbox account has no Digital Link contract, so the run targets GS1 production (a
    disposable/pilot GTIN, protected by the `safe_upsert` guard and `--dry-run`). The other two
    Phase 6 DoD items (§6.5 idempotency, state-file kill-mid-write atomicity) are met and
    covered by passing tests.
  - **WordPress access unblocked (supersedes the earlier deferral).** This item was previously
    recorded as blocked: Application Passwords were disabled by Wordfence on production
    `www.democlient.nl` and no staging site existed. That has since been fixed and verified —
    the `automation-bot` user authenticates against the live REST API with the **editor** role
    (`edit_posts`, `publish_posts`, `upload_files`, `edit_others_posts`, `unfiltered_html`), and
    the `democlient` custom post type is registered and REST-exposed (`rest_base: democlient`).
    The live `run_execute` end-to-end run for one GTIN is therefore **runnable, not blocked** —
    it simply has not been run yet, and it writes to a live WooCommerce store and the GS1
    production resolver, so it needs a deliberate go-ahead and a disposable GTIN.

- **Phase 7 — Re-run & change detection.**
  - `lib/state.py` `diff_against_state(products, state, languages, wordpress)` (§4.8, §8.2):
    per `(GTIN, language)` it builds the slug, resolver target URL, and title from the
    WordPress patterns and classifies against prior state by content hash — NEW (no entry),
    UNCHANGED (equal hash), or CHANGED. A CHANGED row carries a field-level diff of `title`
    and/or `target_url` — the two fields `StateEntry` records — in the order §10.6.2 presents
    them. Fields state does not retain are never fabricated. A language with no `product_name`
    for a product is omitted with a warning (edge E18). Takes the whole `WordPressConfig`
    rather than §4.8's bare `target_url_pattern`, which alone cannot build a `PlanRow`.
  - **`StateEntry.title`** (§2.3, new field): the page title as last written, persisted by
    `run_execute` on every successful row. Without it a CHANGED row had nothing to show —
    `slug_pattern` is GTIN-derived, so renaming a product changes the content hash without
    moving the URL, and §10.6.2's `Changes:` list rendered empty in exactly the scenario the
    phase exit gate names (*"change one product name, re-run, confirm prompt appears"*).
    `content_hash` proves *that* a product changed but, being a digest, can never say *what*.
    Optional (`str | None = None`) so state files predating the field still load; `None` means
    "not recorded" and the title row is omitted rather than guessed.
  - `scripts/run_plan.py` (§8.2): loads config/state/products, classifies with
    `diff_against_state`, writes `output/{client_id}/plan.json`, and prints
    `N new, M unchanged, K changed` to stderr. Exit `0`/`2` (no per-row error class).
  - **E19 (corrupt state file) now recovers instead of aborting**, as §7 always specified.
    `load_state` quarantines the bad file to `state.json.corrupt.{ts}` — preserved, never
    deleted, since it is the only evidence of what went wrong — logs an ERROR, and returns an
    empty state. This is safe because every write path is idempotent (§6.1–§6.5): without a
    known page id `upsert_page` still matches the live page by slug then `meta.gtin` and
    updates it in place, `safe_upsert` reads before it writes, and QR renders are
    byte-deterministic. A reset costs redundant work, not corruption. An *unreadable* file
    (permissions, I/O fault) is an environmental fault and still raises `StateError` → exit 2.
  - **The reset is surfaced, not just logged** (an addition to §7's E19, which stopped at
    "log ERROR"). A reset reclassifies every row as NEW, silently turning an incremental
    re-run into a full rewrite of live pages and resolver targets — and the operator is
    reading the chat, not stderr. So `State.reset_from_corrupt` (load-scoped,
    `Field(exclude=True)`, never persisted) carries the fact to `run_plan`, which leads its
    summary with a warning, and to the flow-orchestrator, which surfaces it **above** the
    §10.6.1 counts. The existing confirmation gate is what makes the reset safe in practice;
    it only works if the operator is told.
  - `skills/flow-orchestrator/SKILL.md` (§10.5, §10.6): presents the plan, collects
    confirmation, writes `plan.confirmed.json`, enforces the mandatory production-env gate,
    and invokes `run_execute` — with the §10.6 chat blocks embedded verbatim.
  - **Website-status control-file gate (extension beyond the spec).** A deliberate,
    user-approved addition for the pilot's *create-only* workflow: an operator-maintained
    file (`input/{client_id}/website_status.xlsx`), separate from the datasource export,
    gates which products are candidates — eligible only when already registered in GS1 and
    not yet on the website. `lib/website_status.py` loads it; `WebsiteStatusConfig` +
    `WebsiteStatusError` + a `websiteStatus` schema block wire it into `clients.yml`;
    `run_plan.py` applies the gate and reports exclusions. Consequence: in the pilot every
    planned row is NEW, so the change-detection/diff path is exercised only by tests, dormant
    at runtime until product updates occur.
  - Tests: `diff_against_state` edge cases (NEW/UNCHANGED/CHANGED; title-only, URL-only, and
    combined diffs; body-only change with no showable diff; a pre-`title` state entry omitting
    the title row; E18; multi-language; missing patterns) plus the legacy state-file load in
    `tests/lib/test_state.py`; control-file parsing and eligibility in
    `tests/lib/test_website_status.py`; E19 recovery (quarantine + reset flag, schema-violation
    files, the raise an unreadable file still gets, and the reset flag never being persisted)
    in `tests/lib/test_state.py`; `run_plan` counts, gate filtering, default path, exit-2
    paths, and the corrupt-state warning reaching stderr in `tests/scripts/test_run_plan.py`;
    title persistence in `tests/scripts/test_run_execute.py`.
  - DoD note: change classification and the §10.6 chat format are met and test-covered. The
    third item — the full re-run flow in a fresh Cowork session — **has moved to Phase 8**
    (§12), whose exit gate is the same test done properly: only `flow-orchestrator` has a
    SKILL.md today, and step 1 of the flow delegates parsing to the not-yet-written
    `gs1-export-parser`, so a Cowork test now would exercise one-fifth of the surface it is
    meant to validate.
  - The plan half now runs on **real operator data**, not a fixture: both `products.xlsx` and
    `website_status.xlsx` are in `input/democlient/`. `parse_export` reads the 127-product GDSN
    export with zero warnings and `run_plan` gates it to 73 rows (37 nl + 36 fr; one fr row
    skipped by E18 for a missing `product_name.fr`), excluding 90 products — 61 already on the
    website, 12 not yet in GS1, 17 absent from the control file.
- **Phase 7.5 — GPC brick → category mapping.**
  - `lib/config.py` `CategoryConfig` + `schema/clients.schema.json` `$defs.categories`: a client
    `categories:` block with `terms` (the closed allowed set), `brick_category_map` (brick → term),
    and `overrides` (GTIN → term). The loader rejects any map/override value outside `terms`. This is
    client-owned, signed-off data, so it lives in config, not code.
  - `lib/categories.py`: `resolve_category` (precedence override > brick map > none; an unmapped
    brick, out-of-set term, or missing brick reports a `SourceIssue` and never guesses),
    `distinct_bricks`, `coverage_report`/`CoverageReport`, plus the operator-input half —
    `load_diy_datamodel` (columns as parameters, since the GS1 DIY sector datamodel's format is the
    operator's) and `draft_brick_map`/`BrickMapDraft`.
  - `scripts/build_brick_map.py`: read-only. Default mode prints a `categories:` review skeleton
    (every export brick present, term UNSET, annotated from the datamodel); `--check` is the coverage
    gate, exiting non-zero while any export brick is unmapped.
  - `scripts/run_plan.py`: assigns `product.category` after the website-status gate and **before**
    hashing, so a category change reclassifies the row as CHANGED. Unmapped bricks warn and leave the
    category unset; findings go to `output/{client}/data/category_issues.json`; the summary reports the
    count. No-op when the client has no `categories` config.
  - Tests: `tests/lib/test_categories.py`, `tests/lib/test_config.py`,
    `tests/scripts/test_build_brick_map.py`, `tests/scripts/test_run_plan.py`.
  - DoD note (§12): **all five items met (2026-07-18).** The operator supplied the GS1 DIY datamodel
    (`GS1 Data Source Datamodel 3.1.36.xlsx`, sheet `Bricks` / `NL Brick Title`) covering all 73
    export bricks; the client signed off the 73-brick map + one override (`10003865` Tuin
    Handgereedschap → `tuin`, with the Notenkraker `08713195003948` → `keuken`). `build_brick_map
    democlient --check` is green and `run_plan` assigns a category to all 73 planned rows
    (`category_issues.json` empty). The signed-off map lives in the gitignored `clients.yml`; the
    reviewed source is `output/democlient/data/categories.proposed.yml`. Open decision resolved:
    missing WordPress terms must **pre-exist** (`require_terms_exist`), enforced later at the
    not-yet-built term-assignment step.
- **Page adapter — `net_content` unit decoding.** `reference/measurement_units.json` (the GS1 DIY
  datamodel's `MeasurementUnitCode_GDSN` picklist, 129 codes → nl/en/fr) + `lib/units.py`
  (`decode_net_content`) turn the raw code the feed carries (`"5 H87"`) into words per language
  (`H87` → *Stuk* / *Piece* / *Pièce*), decoded at render time in `templates._build_context`.
  net_content stays language-agnostic on the record; the decoder is reusable by the future
  Technische-details generator. All 125 pilot products (all H87) now render words. Tests in
  `tests/lib/test_units.py` and `tests/lib/test_templates.py`.
- **Content generator.** `lib/generator.py` turns feed attributes into page copy through a
  deterministic core (cache, contract, merge) with two interchangeable producers behind one
  seam: an **in-session** producer (the `content-generator` skill writes copy in the chat,
  no API key) and a **headless API** backend (`run_generate --backend api` via `lib/llm.py`).
  Routing is per product — `verbatim` / `tighten` / `generate` — driven by attribute 1067
  (the ranked USP source, captured multivalue by `lib/gdsn.py`); `ProductRecord` gains
  `generated_tagline` and `generated_description`, `run_plan` merges the cache before
  classification, and `acf_map` is wired to the generated fields. Producers may declare a
  `generation_inference` — a claim written beyond the literal feed text — which flows to
  `generated_issues.json` for a human to verify before publishing. Spec:
  `docs/clients/democlient-generator-spec.md`; voice: `prompts/democlient/generation.v1.md`.
- **Phase 8 — Skills.** All six `skills/*/SKILL.md` finalised: `flow-orchestrator`,
  `content-generator`, `gs1-export-parser`, `gs1-digital-link`, `qr-render`, and
  `wordpress-product-page`. Each carries Agent-Skill YAML frontmatter (name + description with
  trigger phrases) so a Skills-aware surface can discover it. `flow-orchestrator` gained a
  two-gate review flow: generate copy → **review gate 1** → plan → **review gate 2** → execute,
  draft-first.
- **Phase 9 — Live pilot.** The first real GTIN (`08713195007717`) published live in nl+fr and
  validated end to end: WordPress pages render ACF, the GS1 production record is enabled, and
  `GET id.gs1.org/01/{gtin}` → 307 → page → 200. Scaled to **10 live GTINs**, every QR
  resolving, no manual corrections, and a printed QR confirmed scanning from a phone.
  `docs/clients/democlient-live-log.md` is the committed audit trail of what is live (the
  machine source, `output/{client}/state.json`, is gitignored).
- **Phase 9.5 — Media.** Images and video now publish with the page. `lib/media.py` decodes
  TIFF/PNG/JPEG, flattens alpha, downscales and writes a deterministic baseline JPEG — 93 of
  127 pilot products ship multi-MB TIFF print masters that WordPress rejects outright.
  `lib/media_video.py` resolves videos from per-language folders through a client-confirmed
  name→GTIN mapping and transcodes to H.264/MP4 with ffmpeg, because the source `.mpg`/`.mpeg`
  files are MPEG-1/2 and will not play in an HTML5 `<video>`. `MediaConfig` carries the block;
  `scripts/build_video_map.py` drafts the mapping and `--check` gates coverage. Uploads are
  **content-addressed** (`{base}-{sha12}` slug), so re-runs dedupe on identical bytes without a
  meta read-back. Every media failure still degrades to a published page (E7).
- **Phase 9.8 — Operator flow validated.** `flow-orchestrator` driven end to end in a Claude
  Code session with the operator answering every gate: language select → review gate 1 →
  plan-review gate 2 → per-row diff gate → production environment confirmation → execute →
  post-execute summary. Since the pilot was exhausted (0 actionable rows), a reversible dry-run
  harness supplied one CHANGED and one NEW row; `--dry-run` wrote nothing and the harness was
  torn down with `state.json` verified byte-identical.
- **Phase 10 — Operator documentation.** Seven documents in `docs/`, each derived from the code
  at HEAD rather than from the planning documents: `setup.md` (the entry point),
  `troubleshooting.md` (all 13 `lib/errors.py` classes, the E1–E22 inventory, and the traps
  already paid for live), `gs1-nl-onboarding.md`, `wordpress-onboarding.md`,
  `data-source-export-schema.md`, `template-variables.md`, and `costs.md`. Plus
  `OPEN_DECISIONS.md`, which records identified-but-unmade decisions with evidence and a
  recommendation. `setup.md` was proven by executing it verbatim from a fresh clone in a clean
  venv — which is how the `inspect_export --help` crash below was found.
- **Data-quality report.** `python -m scripts.report_quality {client}` folds four issue files
  into one actionable worklist at `output/{client}/data-quality-report.md`, grouped by owner and
  action (blocks-publish / review / fix-in-MyGS1). `lib/quality_report.py` is a pure,
  deterministic renderer. Sections cover severity-ranked blank fields (§1b separates a blank
  title or hero image, which block publishing, from a blank `net_content`, which only degrades a
  detail line), per-market value comparison (§3b, which surfaced real content disagreements
  rather than cosmetic ones), possible wrong-language values (§3c — flags exactly one real slip
  across all 127 pilot products with no false positives), generation inferences, and free-text
  Observations captured during in-session review.
- **`scripts/run_unpublish.py`** — retract a published product: draft the WordPress pages and
  disable the GS1 record, with HELD classification so it cannot silently republish.
- **`scripts/build_brick_map.py`** — draft and check the GPC brick → category mapping.
- **`.env` is the single source of truth for credentials** (OD-1). `lib/env.py` `load_env()`
  wraps `load_dotenv(override=False)`, called from each script's `if __name__ == "__main__":`
  block — deliberately not from `main()`, which the tests call directly. See *Security* below.

### Fixed
- **`inspect_export` crashed on `--help` and on any non-workbook path.** The script takes a bare
  positional path with no argparse, so `--help` was read as a filename and reached openpyxl,
  which raises `InvalidFileException` — not an `OSError`, so the existing handler missed it and
  the script died with an unhandled traceback. Now prints usage and exits 0 for `-h`/`--help`,
  and reports "cannot read export" (exit 1) for anything unreadable.
- **Video mapping never matched.** `mapping.yml` is keyed with 13-digit GTINs while the pipeline
  carries 14-digit ones, so `VideoMap.resolve` silently returned `None` for every GTIN and no
  video was ever attached. Added `canon_gtin` (strip non-digits, `zfill(14)`) and normalise both
  sides.
- **Media idempotency, found live.** Two distinct failures made every run create duplicate
  attachments: the `content_sha256` meta was silently dropped unless registered in REST, so the
  hash was never readable; and a stale attachment sharing only the base slug squatted it and
  shadowed the match. Fixed by the content-addressed slug above.
- **`ruff format --check` drift** and **PLR0917** (new in ruff 0.16, which CI installs): ignored
  project-wide, matching the existing deliberate `# noqa: PLR0913`.

### Removed
- **Claude Cowork support.** Publishing from its cloud sandbox would have put live production
  WordPress and GS1 credentials on a remote host every wave, and its egress to the live services
  was never proven. **Claude Code is the operating surface**; Claude.ai and Claude Desktop are
  out of scope. `docs/COWORK_SETUP.md` deleted and every "Cowork-native" label re-pointed to
  "in-session (Claude Code)". The pipeline itself was already tool-agnostic.
- **§2b generated-copy dump** from the data-quality report — it reprinted the source text of
  every generated row, never shrank, and named no defect or action.

### Security
- **Production write guard.** A real `run_execute` (not `--dry-run`) against a client whose
  `gs1.environment` is `production` is **refused with exit 2** unless `--i-understand-production`
  is passed. Before this, a bare `run_execute --plan …` published live pages and registered
  permanent GS1 records with no confirmation — the review gates existed only in the
  `flow-orchestrator` skill, so any invocation outside it bypassed them entirely.
- **Credentials consolidated into `.env`** (OD-1, `docs/OPEN_DECISIONS.md`). They had been
  reaching the code from an `env` block in `~/.claude/settings.json` — residue from the removed
  Cowork experiment — which is injected into *every* command in *every* project on the machine.
  That block is now deleted and `.env` is `chmod 600`. The blast radius is the point: while the
  variable was ambient, a diagnostic command echoed a live WordPress application password in
  clear text into a chat transcript.
  **`load_env()` is called from each script's `__main__` block, never from `main()`.** Nine test
  modules call `main()` directly, so the other placement would load production credentials — and
  all four variables the staging guards gate on — into the pytest process on every plain
  `pytest` run, arming tests that write to live WordPress and the GS1 production resolver.
  `tests/lib/test_env.py` enforces both halves of the invariant with an AST check.
- **Pilot allowlist.** `media.restrict_to_mapped_gtins` hard-blocks `run_execute` from writing
  any GTIN without a client-confirmed video in every language, even when passed explicitly via
  `--plan`.
- **E21 / E22 publish holds.** `run_plan` skips any (GTIN, language) with no generated tagline,
  so a product held for blank source copy can never publish as a silently-blank page; the opt-in
  `media.require_hero_image` does the same for a blank source `image_url`. Scoped to the source
  field only — a runtime image fetch failure still degrades gracefully and publishes (E7).

### Changed
- **GS1 GET/PATCH path corrected** (confirmed against the live API): the path segment
  is the GTIN application identifier `01`, not the string `Gtin`
  (`/digitalLink/01/{gtin14}`). Using `Gtin` returned `404` for every GTIN, so `get()`
  and `set_enabled()` never worked before. Not-found is a `400` with body
  `"No valid contract found for Gtin with id: …"` (not 404) → mapped to `None`.
  `DigitalLinkRecord` gains `useGs1Elabel` / `isElabelSupported`; docs (§4.2/§4.3/§5.1/
  §9.1/§13.2) updated. MyGS1-UI Digital Link activations are visible via the API v2.
- **GS1 auth model corrected to OAuth2 client-credentials** (empirically confirmed
  in Phase 2, replacing the spec's assumed static token / `auth_scheme` switch).
  Both clients now mint a short-lived JWT from `client_id`/`client_secret` via
  `POST /authorization/token`, cache it until near expiry, refresh on `401`, and
  send it as a Bearer token. `clients.yml`, its schema, and `.env.example` now
  carry per-environment `client_id_env_*`/`client_secret_env_*` and
  `account_number_*` (the account differs per environment). Docs updated
  (PROJECT_HANDOVER §4.1–4.2, IMPLEMENTATION_SPEC §4.3, §13.2).
- **Specification corrected to match what was built.** Phase 10 audited the documents against
  the modules and found three divergences, each verified in the code first:
  - **`scripts/verify_run.py` does not exist and is not planned.** §8.4 specified it;
    post-run verification lives inside `run_execute` via `wp_client.verify_url`. §8.4 is marked
    superseded and a new **§8.4a** documents the five scripts that were built instead
    (`run_generate`, `run_unpublish`, `build_brick_map`, `build_video_map`, `report_quality`).
  - **`WPMLAdapter` is implemented and in production.** §4.5 called it a stub raising
    `NotImplementedError`, slated for v0.2, and `PREPARATION.md` §3.18 said to install Polylang.
    The pilot has been publishing live nl+fr pages through WPML plus a site-side helper route.
    Polylang must **not** be installed on the pilot site.
  - **`lib/errors.py` has 13 exception classes**, not the 8 listed in §4.1.
- **`README.md`** no longer describes the project as "Phase 1 (repository skeleton). Not yet
  functional"; it now carries a quickstart, a status block, and a safety section.
- **`product_name` maps to GDSN attribute 3301**, not 3318 (which carries material and colour
  noise) or 3297 (an internal logistics string) — verified against the live site.
- **`market_priority` replaced the 1:1 `market_language` mapping**, which cost coverage and
  could not resolve products whose markets disagree. Every market row carries every language, so
  the parser walks the priority order and takes the first non-blank value per
  product/field/language.

## [0.0.1] - 2026-07-09

### Added
- Repository skeleton per `docs/PROJECT_HANDOVER.md` §7: source tree (`lib/`,
  `scripts/`, `mcps/`, `skills/`, `templates/`, `tests/`).
- MIT `LICENSE`, baseline `README.md`, `CONTRIBUTING.md`, and this changelog.
- `.gitignore` covering secrets, per-client config, and build artifacts.
- `clients.example.yml` and `.env.example` configuration templates.
- `schema/clients.schema.json` — JSON Schema for `clients.yml`.
- `pyproject.toml` (Python tooling: ruff, mypy, pytest) and root `package.json`
  (npm workspaces over `mcps/*`).
- GitHub Actions CI: `ruff check`, `ruff format --check`, `mypy --strict lib`,
  and `pytest` on push and pull request.

[Unreleased]: https://github.com/NextGenDataLead/gs1-product-link/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/NextGenDataLead/gs1-product-link/releases/tag/v0.1.0
<!-- 0.0.1 was never tagged; it links to the commit that set the version. -->
[0.0.1]: https://github.com/NextGenDataLead/gs1-product-link/commit/728ae92
