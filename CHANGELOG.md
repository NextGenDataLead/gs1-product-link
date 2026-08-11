# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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

### Fixed
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
