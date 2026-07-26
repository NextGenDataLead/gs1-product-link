# Running the pilot in Claude Cowork

How to run this project's `flow-orchestrator` (and the other skills) from a **Claude Cowork** session,
and validate the environment **before** any live publish. This is the Phase 9.8 path: the ≥10-GTIN
batch is meant to be driven through the operator flow in Cowork, not raw scripts.

> **Why Cowork, not Claude Code?** Phase 9.8's DoD requires the operator flow (language select → copy
> review → plan review → **production env-confirmation** → execute → summary → retry) to be exercised
> from a real Cowork chat session. The Claude Code CLI bypasses that UX.

## How Cowork runs this repo (read first)

Cowork runs in the **Claude Desktop app** and executes code/shell **in an isolated sandbox on
Anthropic's servers**, while **reading and writing your local files** through the desktop bridge. Two
consequences that shape everything below:

- The sandbox is **not** your Mac. Your local `.venv` won't carry over — the sandbox installs its own
  deps. Your local **files** (including gitignored `clients.yml`, `.env`, `input/`) are what the bridge
  exposes.
- Publishing needs **outbound network** from the sandbox to `www.noviplast.nl` (WordPress REST) and the
  GS1 API. Whether the sandbox allows that egress is **unverified** — the validation ladder below proves
  it before we write anything live.

This project's skills drive the Python `scripts/` (`run_plan` / `run_execute` / `run_generate` /
`parse_export`), which call the `lib/` clients directly. The `mcps/` TypeScript servers are **not**
used by this flow — no MCP wiring is needed for Cowork.

## 1. Prerequisites

- **Claude Desktop** (macOS or Windows), signed in on a **Pro / Max / Team / Enterprise** plan.
- The **"Cowork"** option visible in the message box.
- This repo checked out locally, with the gitignored runtime inputs present (they already are on the
  build machine):
  - `.env` — GS1 sandbox client id/secret + `NOVIPLAST_WP_APP_PASS` (**single-quoted**; it contains
    spaces). `ANTHROPIC_API_KEY` is **not** needed — the Cowork-native `content-generator` writes copy
    in-session.
  - `clients.yml` (repo root).
  - `input/noviplast/products.xlsx` (the GDSN export) and `input/noviplast/videos/mapping.yml`.
- Python **≥ 3.11** available in the sandbox; `ffmpeg` optional (video transcode; the page still builds
  without it).

## 2. Install the six skills (one-time)

Cowork discovers skills from your **personal skill library** (shared with claude.ai) — not from the
repo folder. The six `skills/*/SKILL.md` now carry Agent-Skill frontmatter (`name` + `description`),
so upload each one once:

1. **Zip each skill folder** (so the zip contains `<name>/SKILL.md`):
   ```bash
   cd skills
   for s in flow-orchestrator content-generator gs1-export-parser \
            wordpress-product-page gs1-digital-link qr-render; do
     zip -qr "$s.zip" "$s"
   done
   cd ..            # produces skills/<name>.zip × 6
   ```
   If the upload rejects this layout, re-zip with `SKILL.md` at the zip root instead
   (`cd skills/<name> && zip -qr ../<name>.zip .`).
2. In Claude, open **Customize** (left sidebar) → **Skills** → **Upload skill**, and upload each of
   the six `.zip` files. (Requires a paid plan with **code execution** enabled; the menu may be
   labelled **Settings → Capabilities → Upload skill**.) Skills uploaded here appear in **both**
   Claude chat and Cowork.
3. **Verify:** in a Cowork chat, type `/` (or click **+**) — the six skills
   (`flow-orchestrator`, `content-generator`, `gs1-export-parser`, `wordpress-product-page`,
   `gs1-digital-link`, `qr-render`) should be listed; or say `run for noviplast` and confirm
   `flow-orchestrator` triggers.

> Alternative for repeat installs: bundle the six into a single Cowork **plugin** and install that
> once instead of uploading six zips (Customize → Plugins → upload a custom plugin). Not needed for
> this pilot.

## 3. Grant file access + provide config

1. In Cowork, **select this repo folder** and grant read/write access.
2. Tell Cowork to **work from the repo root** — the scripts resolve `clients.yml`, `.env`, `input/`,
   and `output/` relative to the current directory, so every command below assumes CWD = the repo
   root. (`GS1_CLIENTS_FILE` can relocate `clients.yml` if you keep it elsewhere.)
3. Confirm the bridge exposes the gitignored `.env`, `clients.yml`, and `input/` to the sandbox
   (they live in the selected folder, so they should be visible).

## 4. Prepare the sandbox environment

```bash
python -V                      # expect >= 3.11
pip install -e ".[dev]"        # installs httpx, pydantic, openpyxl, pyyaml, qrcode[pil], pystache, jsonschema
ffmpeg -version || true        # optional; only needed for video transcode
set -a; source .env; set +a    # load secrets (keep NOVIPLAST_WP_APP_PASS single-quoted in .env)
```

## 5. Validation ladder — run IN THIS ORDER, before any live write

Each rung proves one thing the sandbox must be able to do. Stop at the first failure.

1. **Config + deps load (read-only):**
   ```bash
   python -m scripts.run_plan noviplast
   ```
   Expect a **16-row / 8-GTIN** plan written to `output/noviplast/plan.json` (5 held GTINs are excluded
   by E21 and reported in `generated_issues.json`). Proves the sandbox reads `clients.yml`, the export,
   the video mapping, and the generated-copy cache.

2. **Templates render (no writes):**
   ```bash
   python -m scripts.run_execute noviplast --plan output/noviplast/plan.json --dry-run
   ```
   Expect `16 row(s), 0 error(s)`. No HTTP, no QR, no state change.

3. **Network egress (the make-or-break):**
   ```bash
   curl -sS -o /dev/null -w '%{http_code}\n' https://www.noviplast.nl/wp-json
   curl -sSL -o /dev/null -w '%{http_code}\n' https://id.gs1.org/01/08713195007717   # GET, never HEAD
   ```
   Expect a WordPress REST response and a `200` after the resolver's 307. **If either is blocked, stop:**
   the sandbox can't reach the live services — fall back to publishing via Claude Code (§Fallback) and
   use Cowork only to validate the operator UX up to the execute gate.

## 6. Wave-1 live via `flow-orchestrator` (the Phase 9.8 validation)

In a Cowork chat, with the repo folder selected, say: **`run for noviplast`**

Cowork then walks the gates **one at a time** — it presents each prompt, you reply, it proceeds;
nothing is written until you confirm at the production gate. The prompts (verbatim) and exactly what
to reply:

1. **Language selection**
   ```
   Client noviplast supports [nl, fr]. Which languages should this run cover?
   [all | nl | fr | nl,fr]
   ```
   → reply **`all`** (the GS1 record links nl + fr together).

2. **Review gate #1 — generated copy** (copy is already generated + reviewed)
   ```
   [looks good — continue | regenerate GTIN … | cancel]
   ```
   → reply **`looks good — continue`**. (If the sandbox's cache is empty, gate 1 first runs the
   Cowork-native `content-generator`; review the tagline + Eigenschappen it writes, then continue.)

3. **Plan review gate #2** — restrict to the 3-GTIN wave using the off-menu filter
   ```
   Proceed with all 16 to execute?
   [all | new-only | changed-review | cancel]
   ```
   → reply **`only GTIN 08713195000862, 08713195005409, 08713195007915`**
   (rich-FR, minimal, and an inference-carrying product; avoids the two with the benign title mismatch.)

4. **Production environment-confirmation** (mandatory — this is the first live write)
   ```
   About to execute against PRODUCTION environment (gs1nl-api.gs1.nl).
   This will make live changes to https://www.noviplast.nl.
   Continue?
   [confirm | switch-to-test | cancel]
   ```
   → reply **`confirm`**.

5. **Execute → progress → post-execute summary → retry** — if the summary lists any errored row,
   reply **`yes`** to retry those; otherwise **`no`**.

**Verify the 3 GTINs** (the pipeline fails silently — a blank page still returns 200, so check the
actual HTML). From the repo root:
```bash
for g in 08713195000862 08713195005409 08713195007915; do
  echo "== $g =="
  # follows the resolver 307 to the live page; confirm the tagline/Eigenschappen text is present:
  curl -sSL "https://id.gs1.org/01/$g" | grep -iE "eigenschappen|<title>" | head -2
  # resolution status (GET, never HEAD — HEAD 404s even for a good record):
  curl -sSL -o /dev/null -w "  resolve: %{http_code}\n" "https://id.gs1.org/01/$g"
done
```
Then have the client do a **physical phone-scan of a printed QR sample** (the Phase 9 DoD's literal
requirement), and **pause for go-ahead** before the next wave.

Later waves (through the same flow) cover the remaining with-copy GTINs and **`…0527`** as the 10th
— it has attr-1083 copy in both languages and is video-mapped, but is currently a dirty draft, so
republish it cleanly (see `run_unpublish` / the unpublish mechanics) — until ≥10 GTINs are live for
the Phase 9 DoD.

## Fallback — sandbox can't reach WP/GS1

If rung 3 fails, publish from **Claude Code** (the proven path that put `…7717` live):

```bash
set -a; source .env; set +a
python -m scripts.run_execute noviplast --plan <wave-slice.json> --i-understand-production
```

(`--i-understand-production` is required for a live production run — a bare `run_execute` against
the production GS1 environment is refused. Omit it and add `--dry-run` to preview instead.)

Still use Cowork to walk the operator gates up to (but not through) execute, so the UX is exercised;
tick the Phase 9.8 execute box once the sandbox egress is sorted.
