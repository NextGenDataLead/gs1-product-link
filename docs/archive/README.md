# Archive

Historical working notes, kept for provenance. **Nothing here is current documentation — do not
follow it.** They lived at the repository root until 2026-07-31, where newcomers reasonably mistook
them for the doc set.

Current documentation starts at [`../setup.md`](../setup.md), then
[`../troubleshooting.md`](../troubleshooting.md).

| File | What it was | Why it is stale |
|---|---|---|
| `OBSIDIAN_NOTE_content.md` | Hub note listing the copy-paste starter prompt for each of the 11 build phases | All 11 phases are complete. It also names an external Obsidian vault as the project's "source of truth", which **contradicts the current model** — the repository's `docs/` is derived from the code and is authoritative. |
| `GS1 Data Source → Digital Link QR — file locations & context.md` | Dutch-language hub note written before the build | Describes the project as *"klaar om te bouwen"* (ready to build) and as multi-tenant. Both were overtaken: the tool shipped as `v0.1.0`, and the deployment model is one client per repository. |
| `RESUME_PROMPT.md` | A prompt to paste into a fresh session to resume the Noviplast page-adapter work | That work finished in Phase 8. Resuming is now covered by [`../setup.md`](../setup.md) and [`../clients/noviplast-pilot-handoff.md`](../clients/noviplast-pilot-handoff.md). |

The `[[wikilink]]` references to these names elsewhere in `docs/` point at notes in the original
Obsidian vault, not at these files; they never resolved on GitHub and are unaffected by the move.
