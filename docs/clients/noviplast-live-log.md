# Noviplast — live-publish log

The durable, committed record of what this pipeline has published to the **live** customer site
(`www.noviplast.nl`) and the **GS1 NL production** resolver. Append one row per `(GTIN, language)`
each time a wave goes live.

> **Why this exists.** The machine source of truth is `output/noviplast/state.json`, but that is
> gitignored (local-only), so it is not a shareable audit trail. This file is. Keep them in sync:
> after a live run, copy the new `state.json` entries here. `state.json` wins on conflict — it is
> what the pipeline actually wrote.
>
> **GS1 is append-only.** GS1 v2 has no delete: `retract` only sets `isEnabled:false`; the record
> persists forever. So a GTIN that ever had a GS1 record stays listed here even if later disabled.

## Currently live

Pages serving on `www.noviplast.nl` with an **enabled** GS1 Digital Link record.

| GTIN | Product (nl / fr) | Lang | WP page | URL | GS1 | First published | Wave |
|---|---|---|---|---|---|---|---|
| `08713195007717` | Hogedrukreiniger / Nettoyeur haute pression | nl | 1449 | https://www.noviplast.nl/noviplast/p-08713195007717/ | enabled | 2026-07-19 | pilot proof |
| `08713195007717` | Hogedrukreiniger / Nettoyeur haute pression | fr | 1450 | https://www.noviplast.nl/fr/noviplast/p-08713195007717/ | enabled | 2026-07-19 | pilot proof |

**Live GTIN count (via this pipeline): 1.** (The Phase 9 DoD needs ≥10.)

Verify any row with a GET (never HEAD — the resolver 404s to HEAD):
`curl -sSL -o /dev/null -w '%{http_code}\n' https://id.gs1.org/01/<gtin>` → expect `200` after the 307.

## Drafts / partially provisioned (not live)

Pages that exist but are **not** serving (draft), and/or GS1 records that were created then disabled.

| GTIN | Product (nl / fr) | Lang | WP page | WP status | GS1 record | Note |
|---|---|---|---|---|---|---|
| `08713195000527` | microvezeldoek / Schoonmaakdoek | nl | 1447 | draft | disabled | "dirty draft"; decide: republish clean (it has 1083 copy + video → candidate 10th GTIN) or leave down. GS1 record persists disabled. |
| `08713195000527` | microvezeldoek / Schoonmaakdoek | fr | 1448 | draft | disabled | as above |

## Not published by this pipeline

For completeness — these were live on the site **before/outside** this pipeline (client-maintained,
per `website_status.xlsx`), so they are **not** counted above and have no state entry here:
`08713195000473`, `08713195001739`, `08713195003948`, `08713195005676`, `08713195007359`.

## How to update

After each live wave (`run_execute … --i-understand-production`, or the `flow-orchestrator` execute):

1. Read the new/changed entries in `output/noviplast/state.json`.
2. Add a row per `(GTIN, language)` to **Currently live** (or **Drafts** if `wp_status != publish`),
   with the WP page id + URL + GS1 status + date + wave label.
3. Bump the live GTIN count and, once ≥10, note the Phase 9 DoD is met.
4. Commit this file with the wave.
