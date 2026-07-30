# Open decisions

Decisions that are **identified, analysed, and not yet made**. Each entry states the origin, the
evidence, the options with honest trade-offs, and a recommendation — so the decision can be taken
later without redoing the investigation.

Resolved decisions move into `IMPLEMENTATION_SPEC.md` or the relevant `docs/` page and are struck
through here with the outcome.

---

## OD-1 — Where credentials live: Claude Code `settings.json` vs `.env`

**Status:** open · **Raised:** 2026-07-30 (Phase 10 doc review) · **Owner:** operator ·
**Blocks:** nothing, but touches the credential path, so resolve it before Phase 11 release.

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
| `NOVIPLAST_WP_APP_PASS` | ✅ | ✅ **duplicated** |
| `NOVIPLAST_GS1_CLIENT_ID` | ❌ | ✅ **only here** |
| `NOVIPLAST_GS1_CLIENT_SECRET` | ❌ | ✅ **only here** |
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
> `echo "$NOVIPLAST_WP_APP_PASS"` to test whether the variable was set, and **printed the live
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
3. Move `NOVIPLAST_GS1_CLIENT_ID` / `NOVIPLAST_GS1_CLIENT_SECRET` into `.env`.
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

- [ ] **Rotate `NOVIPLAST_WP_APP_PASS`** — it was printed in clear text in a chat transcript on
      2026-07-30. WP Admin → Users → `automation-bot` → Application Passwords → revoke, reissue, and
      update **both** stores until the split is resolved. GS1 secrets were **not** exposed.
- [ ] **`chmod 600 .env`** — currently `0644`, world-readable on the machine. Worth doing regardless.
- [ ] **Consider branch protection on `main`.** The repository is **public**; the only collaborator is
      the owner, so outsiders cannot push — but `main` has **no protection**, so anything holding the
      owner's token (including an assistant session) can push directly or force-push over history.
      Requiring a PR plus the `Lint, type-check, and test` check would close that. Owner's call.

---

## Resolved

_(none yet)_
