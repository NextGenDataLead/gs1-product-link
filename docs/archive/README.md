# Archive

Historical working notes, kept for provenance. **Nothing here is current documentation — do not
follow it.** They lived at the repository root until 2026-07-31, where newcomers reasonably mistook
them for the doc set.

Current documentation starts at [`../README.md`](../README.md), which routes by who you are.

**`PREPARATION.md` joined them on 2026-08-20.** It was the one file in the doc set addressed to
"You — the operator", and it was a checklist of things to do *before starting Phase 1* — written
before the build, let alone before the desktop shell existed. An operator following the index
landed in a pre-build checklist. What it was trying to be is now
[`../operator-guide.md`](../operator-guide.md).

| File | What it was | Why it is stale |
|---|---|---|
| `OBSIDIAN_NOTE_content.md` | Hub note listing the copy-paste starter prompt for each of the 11 build phases | All 11 phases are complete. It also names an external Obsidian vault as the project's "source of truth", which **contradicts the current model** — the repository's `docs/` is derived from the code and is authoritative. |
| `GS1 Data Source → Digital Link QR — file locations & context.md` | Dutch-language hub note written before the build | Describes the project as *"klaar om te bouwen"* (ready to build) and as multi-tenant. Both were overtaken: the tool shipped as `v0.1.0`, and the deployment model is one client per repository. |
| `RESUME_PROMPT.md` | A prompt to paste into a fresh session to resume the Democlient page-adapter work | That work finished in Phase 8. Resuming is now covered by [`../setup.md`](../setup.md) and [`../clients/democlient-pilot-handoff.md`](../clients/democlient-pilot-handoff.md). |

The `[[wikilink]]` references to these names elsewhere in `docs/` point at notes in the original
Obsidian vault, not at these files; they never resolved on GitHub and are unaffected by the move.
