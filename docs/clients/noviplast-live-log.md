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
| `08713195000862` | raamwisser / Raclette | nl | 1501 | https://www.noviplast.nl/noviplast/p-08713195000862/ | enabled | 2026-07-28 | 1 (2026-07-28) |
| `08713195000862` | raamwisser / Raclette | fr | 1506 | https://www.noviplast.nl/fr/noviplast/p-08713195000862/ | enabled | 2026-07-28 | 1 (2026-07-28) |
| `08713195005409` | siliconenbak / plateau en silicone | nl | 1511 | https://www.noviplast.nl/noviplast/p-08713195005409/ | enabled | 2026-07-28 | 1 (2026-07-28) |
| `08713195005409` | siliconenbak / plateau en silicone | fr | 1516 | https://www.noviplast.nl/fr/noviplast/p-08713195005409/ | enabled | 2026-07-28 | 1 (2026-07-28) |
| `08713195007915` | lamp / Lampe | nl | 1521 | https://www.noviplast.nl/noviplast/p-08713195007915/ | enabled | 2026-07-28 | 1 (2026-07-28) |
| `08713195007915` | lamp / Lampe | fr | 1526 | https://www.noviplast.nl/fr/noviplast/p-08713195007915/ | enabled | 2026-07-28 | 1 (2026-07-28) |
| `08713195004181` | onkruidborstel / Set desherbant | nl | 1531 | https://www.noviplast.nl/noviplast/p-08713195004181/ | enabled | 2026-07-28 | 2 (2026-07-28) |
| `08713195004181` | onkruidborstel / Set desherbant | fr | 1536 | https://www.noviplast.nl/fr/noviplast/p-08713195004181/ | enabled | 2026-07-28 | 2 (2026-07-28) |
| `08713195004778` | afvoerzeef / Drain saver | nl | 1541 | https://www.noviplast.nl/noviplast/p-08713195004778/ | enabled | 2026-07-28 | 2 (2026-07-28) |
| `08713195004778` | afvoerzeef / Drain saver | fr | 1546 | https://www.noviplast.nl/fr/noviplast/p-08713195004778/ | enabled | 2026-07-28 | 2 (2026-07-28) |
| `08713195005546` | groefborstel / Brosse à rainures | nl | 1551 | https://www.noviplast.nl/noviplast/p-08713195005546/ | enabled | 2026-07-28 | 2 (2026-07-28) |
| `08713195005546` | groefborstel / Brosse à rainures | fr | 1556 | https://www.noviplast.nl/fr/noviplast/p-08713195005546/ | enabled | 2026-07-28 | 2 (2026-07-28) |
| `08713195006178` | stofzuiger / aspirateur et souffleur | nl | 1561 | https://www.noviplast.nl/noviplast/p-08713195006178/ | enabled | 2026-07-28 | 2 (2026-07-28) |
| `08713195006178` | stofzuiger / aspirateur et souffleur | fr | 1566 | https://www.noviplast.nl/fr/noviplast/p-08713195006178/ | enabled | 2026-07-28 | 2 (2026-07-28) |
| `08713195007496` | onkruidverwijderaar / Désherbant | nl | 1571 | https://www.noviplast.nl/noviplast/p-08713195007496/ | enabled | 2026-07-28 | 2 (2026-07-28) |
| `08713195007496` | onkruidverwijderaar / Désherbant | fr | 1576 | https://www.noviplast.nl/fr/noviplast/p-08713195007496/ | enabled | 2026-07-28 | 2 (2026-07-28) |
| `08713195000527` | microvezeldoek / Schoonmaakdoek | nl | 1447 | https://www.noviplast.nl/noviplast/p-08713195000527/ | enabled | 2026-07-28 | republish (2026-07-28) |
| `08713195000527` | microvezeldoek / Schoonmaakdoek | fr | 1448 | https://www.noviplast.nl/fr/noviplast/p-08713195000527/ | enabled | 2026-07-28 | republish (2026-07-28) |

**Live GTIN count (via this pipeline): 10** (`…7717` + the 8-GTIN batch + `…0527`, all 2026-07-28).
**The Phase 9 ≥10-live DoD is met.** `…0527` was republished cleanly via `run_execute --revive`
(copy generated in-session first — it was held for missing generated copy, not a blank feed). The
5 blank-1083 GTINs remain held (see data-quality report §1). All 10 verified 2026-07-28:
`GET id.gs1.org/01/<gtin>` → 307 → 200, both nl + fr pages render copy. **Physical phone-scan of a
printed QR confirmed working 2026-07-28 — Phase 9 is complete (all three §12 DoD boxes met).**

**QR language routing (decided 2026-07-28): keep as-is.** A single printed QR encodes the bare
Digital Link and resolves to the **nl** default page; there is no per-language QR. French buyers
reach the fr page via the site's language switcher. Accepted for v0.1.

Verify any row with a GET (never HEAD — the resolver 404s to HEAD):
`curl -sSL -o /dev/null -w '%{http_code}\n' https://id.gs1.org/01/<gtin>` → expect `200` after the 307.

## Drafts / partially provisioned (not live)

Pages that exist but are **not** serving (draft), and/or GS1 records that were created then disabled.

_None — `…0527` was republished cleanly on 2026-07-28 and is now in **Currently live** above._

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
