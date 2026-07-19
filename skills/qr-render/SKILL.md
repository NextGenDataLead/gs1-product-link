# QR Render

## When to load

Trigger phrases: **"render QR for {client}"**, **"generate the QR files for {client}"** (§10.4) —
e.g. "render QR for noviplast". Load this skill to produce scannable QR symbols for a client's GTINs,
each encoding the product's GS1 Digital Link URI. In the pilot this runs as part of
`flow-orchestrator` / `run_execute`; load it directly to render or re-render QR files on their own.

## What this skill does

Wraps `lib/qr.py` and the `qr-render` MCP server. For each GTIN it builds the Digital Link URI from
the client's `digital_link_url_pattern`, then renders one file per requested format to
`output/{client}/qr/{gtin}.{ext}`. The renderer uppercases the URI's scheme and host (the GTIN path
is untouched) so the whole string falls in the QR alphanumeric set for a denser symbol; SVG output is
deterministic and byte-identical for identical inputs (§6.4), and EPS is greyscale-converted (the EPS
writer rejects 1-bit mode). Tone is **concise and business-like, not conversational**.

## Inputs

- `client_id` (from the trigger phrase; ask if unclear) — used to build the output directory and to
  read the client's QR config; the `qr_render` tool itself takes an explicit request, not a
  `client_id`.
- `clients.yml` `qr` config: `formats` (default `[svg, png]`), `size_mm` (20), `error_correction`
  (`M`), `dpi` (300); and `digital_link_url_pattern` for the URI.
- The GTINs in scope (usually the confirmed plan subset) from
  `output/{client}/data/products.json`.

## Steps

1. **Resolve the client.** Determine `client_id` from the request; ask if ambiguous. Confirm which
   GTINs are in scope.

2. **Build the URI.** For each GTIN build the Digital Link URI from the client's
   `digital_link_url_pattern` (e.g. `https://id.gs1.org/01/{gtin14}`, GTIN zero-padded to 14) — the
   same URI the resolver serves.

3. **Render.** Call `qr_render` with `uri`, `output_dir` = `output/{client}/qr`, `gtin`, `formats`,
   `size_mm`, and `error_correction` from the client's `qr` config. The output filename is
   `{gtin}.{format}`; the directory is created if absent.

4. **Present output paths (§10.4).** List the written files verbatim:
   ```
   Rendered QR for noviplast: 2 file(s) per GTIN → output/noviplast/qr/
     08713195000374.svg
     08713195000374.png
   ```

## MCP tools used

- `qr_render` — render a QR symbol for a Digital Link URI to `svg`/`png`/`eps` files. Params:
  `uri`, `output_dir`, `gtin`, `formats[]` (enum `svg|png|eps`), `size_mm`, `error_correction`
  (enum `L|M|Q|H`). It takes a **fully explicit request** — no `client_id`, no `clients.yml`
  lookup — so you pass the `output_dir` and QR settings yourself.

The parallel library `lib/qr.py` (`render_qr(...)`) is what `scripts/run_execute.py` drives on the
orchestrated path, reading the same values from `cfg.qr`.

## Failure modes

- **No client config → skipped, not failed.** On the orchestrated path, if the client has no `qr`
  config `run_execute` logs `no qr config for client {id}; skipping QR for {gtin}` and moves on —
  the page still publishes. A missing QR is not a run failure.
- **Bad error-correction level.** `error_correction` must be one of `L|M|Q|H`; anything else raises
  a `KeyError` in `lib/qr.py`. It is a bare library with no exit codes of its own — errors propagate
  to the caller (`run_execute`), which owns the error accounting.
- **`--dry-run` writes no files.** A dry-run `run_execute` renders nothing; expect an empty
  `output/{client}/qr/` afterwards.
- **This pipeline fails silently.** A rendered file is not a scannable one. Scan a sample against
  the live resolver before printing at scale — the Phase 9 pilot requires every printed sample to
  scan and resolve correctly.
