# Roadmap — phases × page-adapter track

One-screen overview tying the two planning axes together. **Not** the source of truth for phase
Definition-of-Done — that stays in [`IMPLEMENTATION_SPEC.md §12`](IMPLEMENTATION_SPEC.md) (the `[x]`
checkboxes). This file gives the big picture and tracks the generator commit-by-commit, which §12
does not. Last updated 2026-08-08.

**New here?** Read [`../README.md`](../README.md) for what the tool does, then
[`setup.md`](setup.md) to run it. This file is for tracking build status.

## Two axes

- **Numbered phases (1–11)** — the horizontal framework build: the reusable tool (GS1 client, WP
  client, parser, state, plan/execute, skills, release). DoD boxes live in §12.
- **Page-adapter track (Democlient pilot)** — a vertical, client-specific slice that **cross-cuts
  Phases 6–9** and does not fit one numbered gate (§12 says so explicitly). Its last critical-path
  item, the **content generator, is now complete** (all 9 commits). Detail in
  [`clients/democlient-page-adapter.md`](clients/democlient-page-adapter.md) and the
  [generator SPEC](clients/democlient-generator-spec.md).

## Phase status (summary — authoritative boxes in §12)

| Phase | What | Status |
|---|---|---|
| 1 | Repo skeleton | Built — ruff/mypy/pytest/CI green |
| 2 | GS1 Digital Link client + MCP | Built; live DoD **gated** (sandbox has no DL contract — 21011) |
| 3 | Excel/GDSN parser + records | Built — 127 products nl+fr, round-trip |
| 4 | WordPress client + MCP | Built & merged; staging DoD **deferred** (no staging WP provisioned) |
| 5 | QR + templates | Built; physical phone scan of a printed sample **confirmed** 2026-07-28 (via Phase 9) |
| 6 | lib, scripts, state | Built; end-to-end-on-staging DoD **gated** (no staging WP — proven on live instead, Phase 9) |
| 7 | Re-run + change detection | **Done** (§12 [x]) |
| — | **Page-adapter track** | Done — **generator complete** (all 9 commits, see below) |
| 7.5 | GPC brick → category | **Done** (§12 [x], 2026-07-18) |
| 8 | Skills | **Done** (§12 all 4 [x]) — all 6 SKILL.md finalised; chat flow validated on real files; the execute leg proven in Phase 9 and the full plan → diff → confirm → execute loop walked in Phase 9.8, which ticked the last box |
| 9 | Pilot end-to-end (≥10 live, QR scans, no manual fixes) | **Done** (§12 all 3 [x], 2026-07-28). 10 GTINs live nl+fr; all resolve via GET → 307 → 200; printed-QR phone scan confirmed; both waves ran 0-error. fr-QR strategy decided: keep as-is (bare QR → nl default, fr via the site switcher). Audit trail in `docs/clients/{client_id}-live-log.md` (local-only, gitignored) |
| 9.5 | Media (images + video) | **Code merged (PR #7) + proven live (2026-07-20).** Image+video render on pilot 1449/1450; media idempotent (content-addressed slug). **Open:** the drafted name→GTIN mapping (166 files) needs **client sign-off** (§12 boxes 1/3) |
| 9.8 | Operator flow (Claude Code) | **Done** (§12 all 4 [x], PR #29 `071f8fe`, 2026-07-30). `flow-orchestrator` driven end-to-end in a fresh Claude Code session with the operator answering every gate, via a reversible dry-run harness (nothing written; `state.json` verified byte-identical after teardown). Ticked the open **Phase 8 box #4** |
| 10 | Docs | **Done** (§12 all 3 [x], 2026-07-30). Seven `docs/*.md` written **from the code at HEAD**; README status corrected; drift fixed (§4.1, §4.5, §8, PREPARATION §3.18). `setup.md` proven by **executing it verbatim from a fresh clone** — which surfaced and got a real `inspect_export --help` crash fixed |
| 11 | Release | **Done (2026-07-30) — 4 of 5 boxes.** `v0.1.0` tagged and released; `CHANGELOG.md` reconstructed. The **MCP registry entry is unticked by choice**, not outstanding ([OD-2](OPEN_DECISIONS.md#resolved)); the announcement is drafted and unpublished. See [the critical path](#the-critical-path) below |

"Gated"/"deferred" = code is written, the DoD step needs a live environment (staging WP, a real DL
contract, a printed QR) not yet available.

## Page-adapter track — done vs open

Done (§12 page-adapter block): field mapping resolved with the client (title 3301, 1083 unwired as a
generator input); ranked `market_priority`; source-data report; unpublish lifecycle; `net_content`
H87→word decoding; **the content generator (all 9 commits, below).** **Open:** only the deferred
brand-typo report — everything on the page-adapter critical path is done.

## Generator — commit tracker

Merged to `main` (the `democlient-page-adapter` branch is history).
SPEC: [generator SPEC](clients/democlient-generator-spec.md).
Suite green at HEAD — 550 passed, 2 skipped, 5 deselected (staging); ruff + `mypy --strict` clean.

| # | Commit | State |
|---|---|---|
| 1 | Parser inputs → `gdsn_extras` (variation, dims, material) | ✅ `3b2ffb5` |
| 2 | `generated_tagline`/`generated_description` record fields | ✅ `d5e8b0f` |
| 3 | `lib/generator.py` deterministic core (cache, contract, merge) | ✅ `43b3256` |
| — | Capture all 1067 slots (multivalue) | ✅ `babd01b` |
| — | 1067 routing: verbatim / tighten / generate + adjusted report | ✅ `3fff444` |
| 4 | `scripts/run_generate.py` spine + `LLMClient` seam + `--emit`/`--ingest` | ✅ `a61c1fd` |
| 5 | In-session producer (generation skill + voice) | ✅ `bf31dd9` |
| 6 | API backend (`lib/llm.py`, Sonnet 5, `--backend api`) | ✅ `c6a91e4` |
| 7 | `run_plan` merge (before `diff_against_state`; E18 backstop; `generated_issues.json`) | ✅ `6316ad4` |
| 8 | Wire `acf_map` (title/tagline/description → generated fields) | ✅ `3b44ba7` |
| 9 | Docs + flow-orchestrator gate | ✅ `2999201` |

**Generator COMPLETE (all 9 commits, 2026-07-19).** Copy producer is **both** in-session (no API
key) and a headless API backend, sharing one cache/contract seam. It went on to feed the Phase 9 live
pilot, which is now also complete.

### How the generator commits touch the phases
- Commit 5 (in-session generation skill) ticks a **Phase 8 (Skills)** box.
- Commit 7 sits in the **Phase 6/7** plan + change-classification machinery.
- Commit 8 is the **Phase 6** execute/write path for this client.
- Commit 9 fed **Phase 10 (Docs)** — now superseded by the full `docs/` set (see
  [`setup.md`](setup.md) and [`costs.md`](costs.md) for the operator-facing generator docs).
- Finishing the generator unblocked **Phase 9**, which has since completed.

## The critical path

Everything through Phase 9.8 is **done**:

~~`generator (commits 4–9)`~~ → ~~verify WPML helper + a real published ACF page live~~ →
~~**Phase 9** execute + resolution on the first real GTIN~~ → ~~**Phase 9.5 media**~~ (code merged and
proven live; only the client sign-off on the video mapping is open) → ~~**finish Phase 9**~~ (10 live,
QR scans confirmed, fr-QR strategy decided) → ~~**Phase 9.8 operator flow**~~ (`flow-orchestrator`
driven end-to-end under Claude Code through every gate).

~~**Phase 10 docs**~~ **done** (2026-07-30) → ~~**Phase 11 release**~~ **done (2026-07-30) — every
numbered phase is now complete.**

Phase 11 shipped `v0.1.0`: version bumped to `0.1.0` across `pyproject.toml`, `package.json`, the
three `mcps/*/package.json` and the lockfile; `CHANGELOG.md` reconstructed from 92 unrecorded
commits and promoted to `[0.1.0]`; the `v0.1.0` tag and GitHub release published; the announcement
drafted at `docs/announcement-v0.1.0.md` (**not** published). The **MCP registry entry will not be
submitted** — decided 2026-07-31 ([OD-2](OPEN_DECISIONS.md#resolved)): the three servers stay
private, so that DoD box is unticked by choice rather than left as outstanding work. The
`server.json` files are committed and schema-valid, so it is cheap to reverse.

Also resolved alongside the release: [OD-1](OPEN_DECISIONS.md) — `.env` is now the single source of
truth for credentials, and the ambient `~/.claude/settings.json` `env` block is gone.

## Post-v0.1.0: the operator shell (a third axis)

A separate, self-contained track — making the tool operable by someone who is not an engineer. It
does not extend the numbered phases and has no DoD boxes in §12; it has its own four phases. The
plan behind it is not in the repo (it is a working document); what matters here is the state.

| Phase | What | Status |
|---|---|---|
| 1 | Observability + preflight — incremental run log, `Plan.skipped`, `plan.summary.json`, `lib/preflight.py` + `scripts/doctor.py` | **Built** |
| 2 | `lib/gates.py` (the gate contract, drift-checked against `SKILL.md`) + the `ui/` shell | **Built** |
| 3 | Guided config forms over `.env` and the operator half of `clients.yml`, in `ui/pages/setup.py` | **Built** |
| 4 | Packaging — `install.command` / `start.command` via `uv`, and committing `uv.lock` | **Built** |

Two decisions inside it are settled and should not be reopened:

- **The operator's machine is LLM-free.** No `ANTHROPIC_API_KEY`, no Anthropic egress, never runs
  `run_generate`. Content generation stays on the maintainer's machine and `generated_cache.json`
  is handed over as a file. This removes a class of IT objection and a per-token cost, at the price
  of one file changing hands per batch.
- **`claude -p` is ruled out on the publish path, permanently.** It cannot hold a gate — there is no
  streaming *input* mode and no permission callback to a parent, so it hangs or aborts. And skills
  load headless, so `claude -p "/gs1-publish {client}"` would run the entire gated sequence with
  every gate answered by the model or skipped.

Phase 3 as built: the ~15 fields are hand-written rather than generated from
`schema/clients.schema.json`, which is strong for *validation* and weak for *generation* — no
`default` anywhere, no `title` on any property, descriptions missing exactly where a per-client form
needs them, and the `defaults`-block merge not expressible in it. So the schema validates the
candidate (via `lib/preflight.check_config`) and renders nothing. `gdsn_map`, `acf_map`,
`brick_category_map` and `generator` stay read-only: the first three need a field walk against the
live site, and `generator` carries the E21 guard. `ui/config_edit.py` edits `clients.yml` as text
rather than round-tripping it, because most of that file is comments and several of them are the
only record of why a value is what it is. See [`ui-operator-shell.md`](ui-operator-shell.md).

Phase 4 as built: two double-clicks (`install.command` / `install.bat`, then `start.command` /
`start.bat`) that fetch `uv`, have it fetch CPython 3.11, and build `.venv` **from the now-committed
`uv.lock`** with `--locked` — so the operator's machine cannot resolve its way to a different set of
versions, and there is one hashed artifact for IT to vet. That also closes the reproducibility gap
this track inherited (CI on 3.11, the development venv drifted to 3.14.5, `requires-python` allowing
both). Drift is checked from both sides: CI runs `uv lock --check`, and `tests/test_packaging.py`
compares the lock against `pyproject.toml` offline, because a missing re-lock is invisible on the
maintainer's machine and fatal on the operator's. There is no `.python-version` file on purpose —
pyenv reads it too, and it would break `python` in this directory for anyone lacking that version.
Not verified: the two `.bat` files have never been run on Windows. See
[`operator-install.md`](operator-install.md).

### The first install rehearsal (2026-08-09/10), and what it cost

The four phases were built and merged before anyone had installed the result the way an operator
would. Doing that once — a clone on a separate machine, the gitignored files handed over by hand,
`install.command` double-clicked, then a real publish — **found 25 defects**, three of them serious
enough to have made the shell unusable for its main job:

| Fixed | What was wrong |
|---|---|
| [#52](https://github.com/NextGenDataLead/gs1-product-link/pull/52) | **Both uploads wrote nothing, silently.** NiceGUI 3 replaced the upload event's `content` with an awaitable `file`, and the extra allowed `nicegui>=2.0`. The browser showed 100% and a checkmark; nothing reached disk. |
| [#53](https://github.com/NextGenDataLead/gs1-product-link/pull/53) | **Pruning the process list twice saved rows other than the ones on screen** — a live page and a permanent GS1 record for a product nobody chose, reported as success. |
| [#54](https://github.com/NextGenDataLead/gs1-product-link/pull/54) | **`pages` mode could never run against a production client.** `run_execute` demands `--i-understand-production` in every mode; the production gate is absent from the `pages` walk, so the flag could not be set. The *reversible* half of a publish was the unreachable one. |

The rest are filed: **[#51](https://github.com/NextGenDataLead/gs1-product-link/issues/51)** (no UI
for the video mapping), **[#55](https://github.com/NextGenDataLead/gs1-product-link/issues/55)** (the
handover names two files and needs five, including `state.json` — without it every published GTIN
re-classifies as NEW), **[#56](https://github.com/NextGenDataLead/gs1-product-link/issues/56)**
(screens showing the catalogue where they mean the batch),
**[#57](https://github.com/NextGenDataLead/gs1-product-link/issues/57)** (a count that prints
"284 of 0", and a hand-editable file that answers a syntax error with a stack trace),
**[#58](https://github.com/NextGenDataLead/gs1-product-link/issues/58)** (preflight feedback, screen
order, and nothing reconciling live pages against `state.json`),
**[#59](https://github.com/NextGenDataLead/gs1-product-link/issues/59)** (CI never exercises the
screens; a flaky state test), **[#60](https://github.com/NextGenDataLead/gs1-product-link/issues/60)**
(media uploads: a deterministic `403` from the site's firewall, and a truncated upload treated as
success).

**#59 is the one that explains the other three.** CI installs `.[dev]` — which is exactly what keeps
`lib` provably free of a UI dependency — and therefore never touches `ui/pages/`. Three production
bugs shipped without a single test going red, and one of them had a test that *asserted the broken
behaviour* and passed, because it checked the shape of an argv and never that the command would be
accepted. Each fix added an AST-based contract test that runs without NiceGUI, which helps; AST
checks can only assert the shape of the source, never that a screen works.

**The publish itself is half-finished and live.** One product published in Dutch and failed in
French: its video upload is refused by a rule in front of WordPress with a bare `403 Forbidden`,
ruled out by probe as size, name, language, credential, rate or sequence. The Dutch page is live and
correct; its row was recorded as an error, so `state.json` does not know it exists. That divergence
is the tool's own doing and nothing detects it — see #58 and #60 before resuming.

**Still open and not ours:** the client's sign-off on the video mapping (~140 unmapped rows). The
exposed WordPress application password was **rotated on 2026-07-30** and the old one revoked.

Two standing invariants, both learned the hard way and neither closed by a phase:

- **The ACF pipeline fails silently.** Verify each page by fetching its rendered HTML against the live
  site — a 200 proves the post exists, not that its fields landed. Green tests prove nothing here.
- **Test resolution with GET, never HEAD.** `id.gs1.org` 404s to HEAD and 307s to GET.
