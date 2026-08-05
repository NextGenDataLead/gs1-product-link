# Open decisions

Decisions that were **identified and analysed** rather than made in passing. Each entry states the
origin, the evidence, the options with honest trade-offs, and a recommendation — so the decision can
be taken later without redoing the investigation, and so a decision already taken is not silently
re-litigated.

Once taken, an entry is **struck through with its outcome** and listed under
[Resolved](#resolved); the analysis stays in place. **There are currently no open decisions** — both
entries below are decided.

---

## ~~OD-1 — Where credentials live: Claude Code `settings.json` vs `.env`~~ — RESOLVED

**Status:** **resolved 2026-07-30 — option B adopted** · **Raised:** 2026-07-30 (Phase 10 doc review) ·
**Owner:** operator

> **Outcome.** `.env` at the repository root is now the single source of truth. `python-dotenv` is a
> dependency; `lib/env.py` exposes `load_env()` (`override=False`); each of the nine `scripts/*.py`
> calls it from its `if __name__ == "__main__":` block. The `env` block was deleted from
> `~/.claude/settings.json` (backup: `~/.claude/settings.json.bak-od1-20260730`, which still contains
> the pre-rotation secrets — delete it once rotation is done). `.env` is now `chmod 600`.
>
> **Two corrections to the evidence below, found while implementing it:**
>
> 1. **The divergence table was wrong.** `.env` already contained `DEMOCLIENT_GS1_CLIENT_ID` and
>    `DEMOCLIENT_GS1_CLIENT_SECRET`, populated — plus the sandbox pair and `GS1_PROD_ACCOUNT`. The
>    `settings.json` block held only **three** keys, all three also in `.env`, and a SHA-256 comparison
>    confirmed all three values were **identical**. So nothing had to be moved and no value was at risk
>    of being lost; `.env` was already a strict superset. The claim that "a script run from a plain
>    terminal cannot reach GS1 at all" was therefore false — the file was complete, just unread.
> 2. **The recommended call site was wrong, and dangerously so.** The sketch said to call `load_env()`
>    from each script's `main()`. But nine test modules under `tests/scripts/` call `main()` directly,
>    so that placement loads `.env` — production credentials and all four staging-guard variables —
>    into the pytest process on every plain `pytest` run. It reproduces the exact hazard the
>    "never in `conftest.py`" rule exists to prevent, by a route the rule did not name. The correct
>    site is the `if __name__ == "__main__":` block: `python -m scripts.x` loads `.env`, `main()` under
>    test does not.
>
> **Verified:** full suite passes in a `env -i` clean environment (522 passed, 2 skipped, 5 deselected);
> after running all 116 `tests/scripts` tests there, none of `DEMOCLIENT_WP_APP_PASS`,
> `DEMOCLIENT_GS1_CLIENT_ID`, `WP_STAGING_URL`, `STAGING_GTIN` is present in the process environment;
> and `runpy` of `scripts.run_plan` as `__main__` in that same clean environment *does* populate them.
> The MCP caveat below was checked and is moot: global and project `mcpServers` are both empty and
> there is no `.mcp.json`, so no server depended on the injected block.

*The original analysis is preserved below.*

### Origin

While writing `docs/setup.md` (Phase 10) the doc claimed *"Nothing auto-loads `.env` — run
`set -a; source .env; set +a`"*. The operator challenged it: the Python scripts appear to get
credentials automatically. Investigating showed **both statements were partly right, and the doc's
explanation was wrong**:

- **Nothing in the code loads `.env`.** There is no `python-dotenv` dependency and no `load_dotenv()`
  call anywhere. Secrets are read with a bare `os.environ[...]` lookup — `lib/wp_client.py:993`,
  `lib/gs1_dl_client.py:648`, `lib/llm.py:212`.
- **But the credentials are present anyway**, because `~/.claude/settings.json` carries an `env` block
  that Claude Code injects into every command it runs. That is why runs from chat "just work" with
  nothing sourced.

That `env` block is **residue from the abandoned Claude Cowork experiment** — Cowork needed
credentials supplied through Claude's own configuration rather than a local file. Cowork was
subsequently removed (see `docs/setup.md` → *How you run this*), but the credential block stayed
behind and became, silently, the real source of truth.

### Evidence gathered (2026-07-30)

The two stores have **diverged**:

| Variable | `.env` | `~/.claude/settings.json` |
|---|---|---|
| `DEMOCLIENT_WP_APP_PASS` | ✅ | ✅ **duplicated** |
| `DEMOCLIENT_GS1_CLIENT_ID` | ❌ | ✅ **only here** |
| `DEMOCLIENT_GS1_CLIENT_SECRET` | ❌ | ✅ **only here** |
| `WP_STAGING_URL` / `WP_STAGING_USER` / `STAGING_GTIN` | ✅ **only here** | ❌ |

File modes at the time of investigation: `~/.claude/settings.json` = `0600`; **`.env` = `0644`
(world-readable on the machine)**.

Two live consequences of the split:

1. **Rotating the WordPress application password requires editing two files.** Update one and you get
   a `401` whose cause is invisible.
2. **A script run from a plain terminal cannot reach GS1 at all** — those secrets exist only in
   `settings.json`. Only Claude Code currently has the complete credential set.

### The constraint that shapes any fix

`tests/integration/test_wp_staging.py:79` gates the live-writing staging tests on:

```python
_STAGING_READY = bool(_URL and _USER and os.environ.get(_APP_PASS_ENV) and _GTIN)
```

**`.env` contains all four of those variables.** So if `.env` were auto-loaded anywhere in the test
path, that guard would arm itself — which is precisely the failure `addopts = "-m 'not staging'"` was
added to prevent after a sourced shell once let a bare `pytest` reach production.

> **Therefore: `.env` may be loaded in `scripts/*.py` entry points ONLY.** Never in `lib/` (a library
> must not have import side effects) and **never in `conftest.py` or the tests**. The staging tests
> must keep requiring deliberate, explicit sourcing.

### Options

**A. Status quo — `settings.json` `env` block.**
Nothing to do. Keeps working for Claude Code runs. Leaves the split-brain, the two-file rotation
hazard, and the broadest exposure.

**B. `.env` as single source of truth, loaded by script entry points. ← recommended**
Add `python-dotenv`; call a shared loader from each script's `main()`; move the GS1 secrets into
`.env`; delete the `env` block from `settings.json`; `chmod 600 .env`.

**C. OS keychain / `1Password CLI` / `pass`.**
The only option that is *materially* more secure — encrypted at rest, access auditable. Costs
friction and another dependency. Disproportionate for a single-operator tool publishing to one site;
becomes the right answer if this ever runs unattended, on a shared machine, or for multiple clients
with separate credentials.

### Honest assessment

**On security the gain from B is modest, not dramatic.** Both `.env` and `settings.json` are
plaintext on disk; neither encrypts anything. The real difference is **blast radius**: the
`settings.json` `env` block is injected into *every command Claude Code runs, in every project on the
machine*, so production credentials sit in the environment of unrelated builds, `npm postinstall`
scripts, and crash dumps. With `.env` loaded explicitly, the secret enters only the processes that
need it.

> This is not theoretical. On 2026-07-30 an assistant diagnostic ran
> `echo "$DEMOCLIENT_WP_APP_PASS"` to test whether the variable was set, and **printed the live
> WordPress application password in clear text into a chat transcript**. It was ambient in a shell
> that had no need for it. Under option B that variable would not have been in that process at all.
> **Action taken:** rotation recommended to the operator — see *Follow-up actions* below.

**On reliability the gain from B is clear**, and it is the stronger argument:

- `.env` is per-project and travels with the checkout; `settings.json` is per-machine and invisible to
  the repo. A second operator or machine has nothing to follow.
- `.env.example` already documents the full contract — **the repo already behaves as though `.env` is
  the source of truth.** `settings.json` is undocumented drift.
- It ends the divergence above.
- `python-dotenv`'s default `override=False` means real environment variables still win, so CI and
  production overrides keep working.

### Recommendation

**Adopt option B, primarily for reliability rather than security.** Implementation sketch:

1. Add `python-dotenv` to `pyproject.toml` dependencies.
2. Add one small helper (e.g. `lib/env.py` with `load_env()` wrapping `load_dotenv(override=False)`)
   and call it at the top of each `main()` in `scripts/*.py`. **Do not** call it from `lib/` module
   import or from `conftest.py`.
3. Move `DEMOCLIENT_GS1_CLIENT_ID` / `DEMOCLIENT_GS1_CLIENT_SECRET` into `.env`.
4. `chmod 600 .env`.
5. **Verify the staging guard still requires explicit sourcing** — run `pytest -m staging --collect-only`
   with a clean environment and confirm the tests still report as skipped.
6. Delete the `env` block from `~/.claude/settings.json` — **but first** check the caveat below.
7. Update `.env.example`, `docs/setup.md` → *Secrets*, `docs/troubleshooting.md` →
   *MissingCredentialError*, and `docs/gs1-nl-onboarding.md`.

**Caveat to check before step 6.** The MCP servers `mcps/gs1-nl` and `mcps/wordpress` resolve
credentials from `process.env` (`mcps/*/src/config.ts`). They inherit whatever launches them, so if
Claude Code stops injecting the variables those servers lose their credentials. At the time of
writing there is **no `.mcp.json` in the repo and those servers are not registered in the session**,
so this is probably moot — but confirm rather than assume. If they are wired up later, they need their
own `.env` loading (`dotenv` is available for Node too) or an explicit env pass-through in the MCP
launch config.

### Follow-up actions (independent of which option is chosen)

- [x] **Rotate `DEMOCLIENT_WP_APP_PASS`** — **done 2026-07-30.** It had been printed in clear text in a
      chat transcript that day. A new application password was issued for `automation-bot` and `.env`
      updated; the old one was revoked. Verified both ways: the new credential returns `200` from
      `GET /wp-json/wp/v2/users/me?context=edit` as `automation-bot` with role `editor`, and the old
      one now returns `401`. The `~/.claude/settings.json` backup holding the old value was deleted.
      GS1 secrets were never exposed and were not rotated.
- [x] **`chmod 600 .env`** — done 2026-07-30 (was `0644`).
- [x] **Branch protection on `main`** — enabled 2026-07-30.

---

## ~~OD-2 — Publish the three MCP servers to npm, or keep them private?~~ — DECIDED

**Status:** **decided 2026-07-31 — option A, keep them private** · **Raised:** 2026-07-30 (Phase 11
release) · **Owner:** operator

> **Outcome.** The three servers stay `"private": true`. Nothing is published to npm and no registry
> entry is submitted, so the §12 Phase 11 box *"MCP registry entry submitted"* stays unticked **by
> choice, permanently** — it is not outstanding work.
>
> The `server.json` files remain committed at `mcps/*/server.json` and valid against the
> `2025-12-11` schema, so the decision is cheap to reverse: publishing to npm (with a matching
> `mcpName` in each `package.json`) and submitting is all that would be left.
>
> **Revisit only if** someone actually wants to consume one of these servers. `qr-render` is the
> candidate — it takes no credentials and no `clients.yml`, so it is the only one genuinely useful
> standing alone.

*The original analysis is preserved below.*

### Origin

Phase 11's DoD includes submitting an MCP registry entry. Preparing it surfaced a prerequisite the
DoD does not mention: **all three packages are `"private": true`** (`mcps/gs1-nl`,
`mcps/wordpress`, `mcps/qr-render`) and none has ever been published.

### What submission actually requires

Confirmed against the registry documentation on 2026-07-30:

1. A `server.json` per server against the current schema — **written and committed** at
   `mcps/*/server.json`, names `io.github.NextGenDataLead/{gs1-nl,wordpress,qr-render}`.
2. **The npm package must be published and publicly resolvable**, because ownership is verified by
   reading the *published* `package.json`.
3. That published `package.json` must carry an **`mcpName`** field exactly matching the `name` in
   `server.json`. It is not there yet, and adding it only matters at publish time.

So the entry cannot be submitted while the packages are private. The committed `server.json` files
are drafts in their final location — they do nothing on their own, since publishing is an explicit
`mcp-publisher` invocation.

### Options

**A. Keep them private. ← recommended for now**
The servers exist to serve this repository's own pipeline, which invokes the Python library
directly; nothing outside consumes them. Neither is registered in any MCP client today (global and
project `mcpServers` are both empty, there is no `.mcp.json`). Publishing creates a permanent
public artifact and an implied support surface for code whose only proven use is one pilot.

**B. Publish all three and submit.**
Makes them installable by anyone and completes the DoD box. Costs: an npm org or scope, a public
name that is awkward to withdraw, and a versioning commitment. `qr-render` is the most plausible
standalone (it has no credentials and no config); `wordpress` and `gs1-nl` both resolve config from
a `clients.yml` shaped for this project, so they are less useful in isolation than they look.

**C. Publish `qr-render` only.**
The one genuinely general-purpose server, with the smallest surface and no credential handling.

### Recommendation

**Option A for v0.1.0.** Ship the release without the registry entry, with the box explicitly
deferred and the reason recorded here rather than left looking unfinished. Revisit if someone
actually wants to consume a server, or if the `clients.yml` coupling in `gs1-nl` / `wordpress` is
ever loosened into plain configuration.

*Adopted 2026-07-31 — see the outcome note at the top of this entry.*

---

## Resolved

- **OD-1 — where credentials live** → **option B, 2026-07-30.** `.env` is the single source of truth,
  loaded by `lib/env.py` `load_env()` from each script's `__main__` block; the `~/.claude/settings.json`
  `env` block is gone. Full write-up above, including two corrections to its own original evidence.
- **OD-2 — publish the MCP servers?** → **option A, 2026-07-31: keep them private.** Nothing is
  published to npm and no registry entry is submitted; the §12 Phase 11 box stays unticked by choice,
  not as outstanding work. `server.json` files stay committed, so it is cheap to reverse.

**No open decisions.**
