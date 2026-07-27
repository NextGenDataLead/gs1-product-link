# Running the pilot in Claude Cowork

This doc has **two parts, for two different people**:

- **Part 1 — one-time technical setup.** For the technical owner. Wires up skills,
  credentials, and inputs, and proves the sandbox can publish. Done once per machine/client.
  **Already done for the Noviplast pilot — skip to Part 2.**
- **Part 2 — running the pilot from the chat.** For the operator. No terminal, no config
  files — you open a Cowork chat and talk to Claude in plain language.

> **Why Cowork?** Phase 9.8's DoD requires the operator chat UX (language select → copy
> review → plan review → production confirmation → execute → summary → retry) to be exercised
> from a real Cowork session. The pipeline mechanics are already proven live — Cowork is the
> thing under test.

## How Cowork runs this repo (context for both parts)

Cowork runs in the **Claude Desktop app**. When you chat, Claude executes code **in an
isolated sandbox (a Linux VM) on Anthropic's servers**. Three things follow from that, and
they drive the whole of Part 1:

- **Files reach the sandbox only through a "connected folder".** The sandbox can't see your
  Mac by default. You explicitly connect the repo folder (Customize → Connected Folders); it
  is then mounted into the sandbox. Your files stay on your machine — **nothing is uploaded to
  Anthropic** — and `clients.yml`, `.env`, and `input/` become readable there. (The bridge has
  occasionally served *truncated/stale* reads without erroring, so a big file is worth a
  line-count sanity check.)
- **Secrets go in via Claude's `env` setting, not `source .env`.** In the sandbox,
  environment variables **don't persist between separate shell commands** — each command
  starts fresh — so the `source .env` trick doesn't hold. The reliable channel is the `env`
  key in `~/.claude/settings.json`, applied at session start and kept for the whole session
  (see 1b).
- **Network egress is restricted.** The sandbox routes outbound traffic through a mandatory
  allow-list proxy it can't bypass, so reaching `www.noviplast.nl` and the GS1 API is
  **likely blocked unless allow-listed** — that's what 1c tests, and why the Claude Code
  fallback exists.

The operator never does any of this — **Claude does the work in the sandbox** in response to
the chat. The terminal commands in Part 1 are for the technical owner setting things up.

---

# Part 1 — One-time technical setup

*Audience: the technical owner. Already done for the Noviplast pilot.*

**Prerequisites:** Claude Desktop signed in on a paid plan (Pro/Max/Team/Enterprise) with code
execution, and this repo checked out with its gitignored runtime files present.

### 1a. Upload the six skills to the Skills library

Cowork discovers skills from the account's **personal skill library** (shared with claude.ai),
not from the repo folder. Zip each folder and upload it once:

```bash
cd skills
for s in flow-orchestrator content-generator gs1-export-parser \
         wordpress-product-page gs1-digital-link qr-render; do
  zip -qr "$s.zip" "$s"
done
```

Each zip contains `<name>/SKILL.md`. Then in Claude: **Customize → Skills → Upload skill**,
and upload all six `.zip` files. (If
the upload rejects the layout, re-zip with `SKILL.md` at the zip root:
`cd skills/<name> && zip -qr ../<name>.zip .`.) Skills uploaded here appear in both Claude chat
and Cowork. Verify by typing `/` in a Cowork chat — the six should be listed.

### 1b. Connect the folder, then provide config, secrets, and inputs

**First, connect the repo folder.** Claude Desktop → **Customize → Connected Folders** → add
this repo's folder. That mounts it (with `clients.yml`, `.env`, `input/`) into the sandbox.

**Config and inputs** (files in the connected folder — all pre-filled for the pilot):

- **`clients.yml`** — the master config: site URLs, GS1 accounts, field maps, input paths, and
  the **names** of the env vars that carry each secret (e.g. `app_password_env:
  NOVIPLAST_WP_APP_PASS`) — never the secret values themselves. Must sit at the **repo root**;
  the scripts resolve it from there, with no env-var or flag override.
- **The product export** — the GDSN workbook at the path in `clients.yml` `export.path`
  (`input/noviplast/products.xlsx`).
- **The video files** — `input/noviplast/videos/mapping.yml` plus the folders in `clients.yml`
  `media.video_folders`. Because `media.restrict_to_mapped_gtins: true`, only GTINs with a
  confirmed video in every language are eligible. Video is optional; the page builds without
  it (`ffmpeg` only needed to transcode).

**Secrets** — provide the *values* for the names `clients.yml` references, via the `env` key in
a Claude settings file (applied at session start, kept for the whole session — unlike
`source .env`, which doesn't survive between the sandbox's shell commands). The pilot runs
`environment: production`, so it needs exactly **three**:

- `NOVIPLAST_WP_APP_PASS` — WordPress application password.
- `NOVIPLAST_GS1_CLIENT_ID` / `NOVIPLAST_GS1_CLIENT_SECRET` — GS1 **production** OAuth pair.

(`..._GS1_CLIENT_SANDBOX_ID`/`_SECRET` are only for test-env runs; `ANTHROPIC_API_KEY` isn't
needed — copy is written in-session.)

**Which settings file — this matters for security:**

- `~/.claude/settings.json` (home dir) — **safe**, outside any repo, verified to work in
  Cowork. Downside: global, so the keys load into every Claude session on the machine.
- `.claude/settings.local.json` (in the repo) — **safe and gitignored**; scopes the keys to
  this project.
- `.claude/settings.json` (in the repo) — ⚠️ **NOT gitignored**. Never put secrets here.

The values already live in the repo's gitignored `.env`, so you never need to retype or paste
a secret. Two ways to get them into the `env` key:

**Easiest — ask Claude to do it** (in this repo via Claude Code, or in the connected Cowork
chat):

> "Back up `~/.claude/settings.json`, `chmod 600` it, then merge `NOVIPLAST_WP_APP_PASS`,
> `NOVIPLAST_GS1_CLIENT_ID`, and `NOVIPLAST_GS1_CLIENT_SECRET` from `.env` into its `env` key.
> Print only the key names, never the values."

It reads the values straight from `.env` and writes them into the file — nothing is printed to
the terminal or the chat.

**Or do it yourself** — paste this as a **single line** (multi-line/heredoc snippets get
mangled by some terminals' paste, which corrupts the command):

```bash
cp ~/.claude/settings.json ~/.claude/settings.json.bak && chmod 600 ~/.claude/settings.json ~/.claude/settings.json.bak && set -a && source .env && set +a && python3 -c "import json,os;p=os.path.expanduser('~/.claude/settings.json');d=json.load(open(p));d.setdefault('env',{}).update({k:os.environ[k] for k in ('NOVIPLAST_WP_APP_PASS','NOVIPLAST_GS1_CLIENT_ID','NOVIPLAST_GS1_CLIENT_SECRET')});json.dump(d,open(p,'w'),indent=2);print('env keys:',list(d['env'].keys()))" && python3 -m json.tool ~/.claude/settings.json >/dev/null && echo "JSON valid"
```

Run it **from the repo root** (that's where `.env` is). Either way, secrets sit in plaintext
at rest (as in `.env`) — protected by file permissions, not
encrypted. If a key is ever exposed, rotate it (WP: regenerate the app password; GS1: reissue
the client secret in MyGS1). The repo's `.env` keeps the same values for the Claude Code
fallback path.

### 1c. Prove the sandbox can publish (make-or-break)

In a Cowork chat with the folder connected, have Claude install deps
(`pip install -e ".[dev]"`, per [`../README.md`](../README.md)) and run the connectivity
check:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://www.noviplast.nl/wp-json
curl -sSL -o /dev/null -w '%{http_code}\n' https://id.gs1.org/01/08713195007717
```

Expect a WordPress REST response and a `200` after the resolver's 307 (use `GET`/`-sSL`, never
`HEAD`). **If either is blocked,
the sandbox can't publish** — use the Fallback at the bottom and let the operator exercise the
chat UX only up to the execute gate.

Once this passes, hand off to the operator.

---

# Part 2 — Running the pilot from the chat

*Audience: the operator. This is all chat — no terminal, no config files.*

1. Open a **Cowork chat** with the project folder connected (the technical owner set this up
   in Part 1 via Customize → Connected Folders).
2. Type: **`run for noviplast`**
3. Claude does everything in the sandbox and **walks you through one step at a time** — it
   shows a prompt, you reply in plain language, it proceeds. Nothing goes live until you
   confirm at the production step. The steps:
   - **Pick languages** — reply `all` (the GS1 record links nl + fr together).
   - **Review the copy** — Claude shows the tagline + product bullets it wrote; reply to
     approve or ask it to regenerate a product.
   - **Review the plan** — Claude lists what it will publish; approve, or narrow it to a
     specific wave of GTINs.
   - **Confirm going live** — the one point of no return; reply `confirm` to publish.
   - **Publish → summary** — if anything errored, reply to retry it.
4. After each wave, **verify** and pause for go-ahead before the next.

You don't need to remember the exact wording — Claude prompts you. If you want the reference:

- The exact gate prompts and replies:
  [`../skills/flow-orchestrator/SKILL.md`](../skills/flow-orchestrator/SKILL.md).
- Which GTINs are in each wave, the per-wave go-ahead, and the physical phone-scan of a
  printed QR: [`clients/noviplast-pilot-handoff.md`](clients/noviplast-pilot-handoff.md)
  Step 3.

**Verifying a published product:** the pipeline can fail silently — a blank page still returns
200 — so open the public page and check the copy is actually there, and test the QR/resolver
with `GET`, never `HEAD`. Ask Claude to do this for you in the chat. The full list of these
gotchas is in [`clients/noviplast-pilot-handoff.md`](clients/noviplast-pilot-handoff.md)
§"Load-bearing invariants"; what's already live is tracked in
[`clients/noviplast-live-log.md`](clients/noviplast-live-log.md).

---

## Fallback — sandbox can't reach WP/GS1

If Part 1c fails, publish from **Claude Code** (the proven path) instead:

```bash
set -a; source .env; set +a
python -m scripts.run_execute noviplast --plan <wave-slice.json> --i-understand-production
```

(`--i-understand-production` is required for a live production run — a bare `run_execute`
against production is refused. Omit it and add `--dry-run` to preview.) Still use Cowork to
walk the operator gates up to (but not through) execute, so the UX is exercised.
