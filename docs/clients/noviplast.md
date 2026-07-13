# Noviplast — client notes

Client-specific quirks and decisions. Expanded during the Phase 9 pilot; started in
Phase 7 to record the create-only gate.

## Website-status control file (create-only gate)

Noviplast's run is **create-only**: a product gets a WordPress page + QR only when it is
already registered in GS1 and not yet on the website. This is driven by an operator-
maintained control file, **not** the datasource export:

- **Location:** `input/noviplast/website_status.xlsx` (git-ignored, operator-provided).
- **Columns:** `Artikelnr. | Omschrijving NL | Barcode | Momenteel op Website | Al in Gs1 | Link naar site`.
- **Join key:** `Barcode` = GTIN (matches `ProductRecord.gtin`). `Artikelnr.` is the
  internal article number; `Link naar site` is the existing-page URL — both informational.
- **Eligibility:** `Al in Gs1` filled (GS1 record exists) **and** `Momenteel op Website`
  blank (not yet live). Already-live, not-in-GS1, and GTINs absent from the file are
  excluded and reported in the `run_plan` summary.

Configured under `clients.yml` → `noviplast.website_status`. Because every eligible row is
new, the hash-based CHANGED/diff detection (built per §8.2/§4.8) stays dormant at runtime —
it is exercised by unit tests and reserved for future product updates.

This control file is **not part of the original spec** — a deliberate, user-approved
extension for the pilot workflow (see CHANGELOG, Phase 7).
