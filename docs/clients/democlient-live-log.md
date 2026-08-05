# Democlient — live-publish log

The durable, committed record of what this pipeline has published to the **live** customer site and
the **GS1 NL production** resolver. Append one row per `(GTIN, language)` each time a wave goes live.

> **Names vs. facts.** This repository's documentation calls the pilot client `democlient`. The
> **URLs, page ids and GTINs recorded below are the real published ones** and are deliberately not
> rewritten — an audit trail that records addresses nothing serves is worse than no audit trail. So
> the client *name* here is a placeholder; every *identifier* is real and verifiable.

> **Why this exists.** The machine source of truth is `output/{client_id}/state.json`, but that is
> gitignored (local-only), so it is not a shareable audit trail. This file is. Keep them in sync:
> after a live run, copy the new `state.json` entries here. `state.json` wins on conflict — it is
> what the pipeline actually wrote.
>
> **GS1 is append-only.** GS1 v2 has no delete: `retract` only sets `isEnabled:false`; the record
> persists forever. So a GTIN that ever had a GS1 record stays listed here even if later disabled.

## Currently live

Pages serving on the live client site with an **enabled** GS1 Digital Link record.

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

## Verification runs

Deliberate live runs whose purpose was to **prove the pipeline works**, not to publish anything new.
No GTIN entered or left "Currently live" as a result. Recorded here because they involve real writes
to the live site and the production resolver, and the audit trail must not show unexplained activity.

### 2026-08-04 — end-to-end verification of the three publish flows (PR #37)

**Subject:** `08713195007717` (nl + fr), chosen because it was the known-good pilot proof.
**Outcome:** WordPress and GS1 write paths both **proven**; one **bug found** (below). System
restored to its exact prior state — `run_plan` returns the same `0 new, 0 unchanged, 0 changed`
baseline it did before the run, and every `state.json` field for the GTIN is byte-identical.

Method — the pipeline is idempotent, so nothing had to be taken down. Only `state.json` was edited
(one entry removed, making the GTIN re-plan as NEW); pages were updated in place and the resolver
record rewritten to the same values. Because success therefore *looks identical to the start state*,
each leg needed independent server-side evidence:

| Leg | Evidence | Result |
|---|---|---|
| WordPress | `modified_gmt` `2026-07-20T01:00:14/15` → `2026-08-04T01:51:55/58` | **write proven** |
| Page content | title/content/excerpt and all 9 ACF fields diffed before vs. after | unchanged, as intended |
| ACF render | full normalised field text located in live HTML, both languages | renders |
| GS1 | link targets deliberately repointed to `https://www.noviplast.nl/` first, then required to be restored by the run | **write proven** |
| Resolution | `GET id.gs1.org/01/…` → 307 → nl product page → 200 | restored |

**The GS1 perturbation was a hand-written `upsert` outside `flow-orchestrator`** — the only way to
make an identical-content rewrite observable, since a Digital Link record carries no timestamp or
version field and the code never reads it back. Both links were pointed at the client homepage, so
the QR kept resolving to a real page (307 → 200) throughout; it was never disabled and never aimed
at a 404 or at another product. Snapshot and restore payload were captured before the change.

**Bug found — `/gs1-pages` → `/gs1-links` handoff is unreachable under the pilot allowlist.**
`run_execute --only pages` writes a state entry with an empty `gs1_link_set_hash`, which `_classify`
is designed to report CHANGED (`gs1_link: not written`) so a follow-up `/gs1-links` has something to
plan. But `_pilot_allowlist` (`scripts/run_plan.py:122-129`) drops **any** GTIN already present in
`state.entries` *before* classification runs, so the row never reaches `_classify` and the plan comes
back empty. While `media.restrict_to_mapped_gtins` is on, a pages-only run therefore removes the GTIN
from every subsequent plan and the two-step flow cannot complete. Unit tests miss it because both
mechanisms are correct in isolation; only their interaction fails.

Workaround used during the run: `run_execute --confirmed` with the plan confirmed *before* the pages
run — `run_execute`'s own allowlist (`run_execute.py:807-827`) enforces only the video-mapping half,
not state presence, so the rows still execute.

**Fixed** in `fix/pilot-allowlist-pages-only`: `_pilot_gate` now counts a GTIN as finished only when
every language has a non-empty `gs1_link_set_hash`, so a half-published GTIN stays in the queue until
its resolver record exists. Verified against this GTIN's real state — simulating the pages-only entry
yields `2 changed` with `diff={'gs1_link': ...}`, which is what the two-step flow needs.

## How to update

After each live wave (`run_execute … --i-understand-production`, or the `flow-orchestrator` execute):

1. Read the new/changed entries in `output/{client_id}/state.json`.
2. Add a row per `(GTIN, language)` to **Currently live** (or **Drafts** if `wp_status != publish`),
   with the WP page id + URL + GS1 status + date + wave label.
3. Bump the live GTIN count and, once ≥10, note the Phase 9 DoD is met.
4. Commit this file with the wave.
