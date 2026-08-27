# Documentation

Three different people read this folder, and the right first page is different for each.

## If you are the person publishing products

You have been sent a folder and you need to get products onto the website and their barcodes
registered. You do not need a terminal.

1. **[`operator-install.md`](operator-install.md)** — two double-clicks, plus the five files the
   maintainer has to send you separately. Read the `state.json` warning in it.
2. **[`operator-guide.md`](operator-guide.md)** — the walkthrough of one batch, screen by screen,
   with screenshots, a *when something goes wrong* section, and a glossary.

Those two are the whole set. Everything else here is written for someone else.

## If you are taking this project over from someone else

1. **[`consultant-onboarding.md`](consultant-onboarding.md)** — clone, install, and the two inputs
   you produce yourself: the GS1 Data Source export and the video mapping, with the exact format,
   filename and location each has to have. Then the two guides above.
2. **[`handover-briefing.md`](handover-briefing.md)** — for the person doing the handing over:
   what to decide first, what to send, a session plan, and the eight things a newcomer gets wrong.

## If you are setting the tool up for a new client

1. [`setup.md`](setup.md) — install, verify, configure, run, onboard a client.
2. [`gs1-nl-onboarding.md`](gs1-nl-onboarding.md) — GS1 contracts, OAuth2 credentials, resolution,
   retraction.
3. [`wordpress-onboarding.md`](wordpress-onboarding.md) — application passwords, post types, slugs,
   ACF versus templates, WPML, media.
4. [`data-source-export-schema.md`](data-source-export-schema.md) — reading a GDSN or flat export,
   and mapping its columns.
5. [`template-variables.md`](template-variables.md) — `acf_map` and the page template's variables.
6. [`costs.md`](costs.md) — what running this costs, and the one variable cost.

## If you are working on the code

- [`troubleshooting.md`](troubleshooting.md) — every exception type, the HTTP outcomes, the E1–E23
  edge-case inventory, rollback, and the traps already paid for on a live site.
- [`ui-operator-shell.md`](ui-operator-shell.md) — why each screen of the desktop shell is built the
  way it is, and where its safety lives. **Not** the operator's guide; that is above.
- [`verifying-live.md`](verifying-live.md) — proving the pipeline still writes to production
  without degrading a live product.
- [`IMPLEMENTATION_SPEC.md`](IMPLEMENTATION_SPEC.md) — the *how*: types, module contracts, error
  handling, idempotency, script contracts, and the Definition of Done per phase (§12).
- [`PROJECT_HANDOVER.md`](PROJECT_HANDOVER.md) — the *why*: scope, architecture decisions, phase
  plan, risks, reference artifacts.
- [`OPEN_DECISIONS.md`](OPEN_DECISIONS.md) — decisions analysed, with evidence and outcome. None is
  currently open; OD-2 (publishing the MCP servers) is decided as *no*.
- [`ROADMAP.md`](ROADMAP.md) — phase status and the per-PR record.
- [`architecture.svg`](architecture.svg) — the end-to-end diagram.

## Per client

[`clients/`](clients/) — one folder of notes per client: the page model, the generator spec, the
live audit trail. `{client}-live-log.md` is gitignored; [`clients/live-log.template.md`](clients/live-log.template.md)
is what it is copied from.

## Not current

[`archive/`](archive/) — superseded working notes, kept for provenance only. Nothing in there
should be followed. [`ui-shell-research-brief.md`](ui-shell-research-brief.md) is also history: it
describes the operator shell before any of it was built.

---

`images/` holds the screenshots embedded in the operator guide. They are captured against a
throwaway client, never a real one — the recipe is in
[`ui-operator-shell.md`](ui-operator-shell.md#regenerating-the-screenshots).
