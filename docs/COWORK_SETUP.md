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

## 2. Make the skills discoverable

The six skills under `skills/*/SKILL.md` now carry Agent-Skill YAML frontmatter (`name` +
`description` with trigger phrases), so a Skills-aware surface can discover them. On first run,
**confirm how Cowork loads them** and record the working method here:

- Most likely: copy or symlink the six skill folders into the skills directory Cowork scans (Cowork
  shares Claude Code's architecture, which reads `~/.claude/skills/<name>/SKILL.md` and project
  `.claude/skills/`). Alternatively, selecting the repo folder plus a short folder-instruction that
  points at `skills/` may be enough.
- Sanity check: ask Cowork *"which skills are available?"* — the six (`flow-orchestrator`,
  `content-generator`, `gs1-export-parser`, `wordpress-product-page`, `gs1-digital-link`, `qr-render`)
  should be listed, or `run for noviplast` should trigger `flow-orchestrator`.

## 3. Grant file access + provide config

- In Cowork, **select this repo folder** and grant read/write access.
- Confirm the bridge exposes the gitignored `.env`, `clients.yml`, and `input/` to the sandbox (the
  scripts read them from the working directory; `GS1_CLIENTS_FILE` can relocate `clients.yml` if
  needed).

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

Trigger `run for noviplast` and let Cowork drive the flow **one gate at a time** — present each gate,
wait for the choice, then proceed; never batch or auto-confirm:

1. **Language** → `all` (nl + fr; the GS1 record links both).
2. **Review gate #1 (generated copy)** → the copy is already generated and reviewed; confirm.
3. **Plan review gate #2** → restrict to the **wave-1 subset**: `08713195000862`, `08713195005409`,
   `08713195007915` (rich-FR, minimal, and an inference-carrying product; avoids the two GTINs with the
   benign cross-market title mismatch).
4. **Production environment-confirmation** → `confirm` only when you intend live writes to
   `www.noviplast.nl` + GS1.
5. **Execute → progress → post-execute summary → retry.**

**Verify each published GTIN** (the pipeline fails silently — a blank page still returns 200):

- Fetch the public page HTML and confirm the tagline + Eigenschappen are actually in it.
- `curl -sSL -o /dev/null -w '%{http_code}' https://id.gs1.org/01/{gtin}` → **307 → page → 200** (GET).

Then pause for go-ahead before the next wave. Remaining with-copy GTINs and `…0527` (the 10th; it has
1083 copy in both languages and is video-mapped — republish the dirty draft cleanly) follow in later
waves through the same flow, until ≥10 GTINs are live for the Phase 9 DoD.

## Fallback — sandbox can't reach WP/GS1

If rung 3 fails, publish from **Claude Code** (the proven path that put `…7717` live):

```bash
set -a; source .env; set +a
python -m scripts.run_execute noviplast --plan <wave-slice.json>
```

Still use Cowork to walk the operator gates up to (but not through) execute, so the UX is exercised;
tick the Phase 9.8 execute box once the sandbox egress is sorted.
