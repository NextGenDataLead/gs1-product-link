# GS1 Digital Link Orchestrator

Turn compliant product data in **GS1 Data Source** into printable, GS1-compliant
QR codes (a *QR code powered by GS1*, encoding a GS1 Digital Link URI) whose
resolver target points at the supplier's own website — and provision the
destination WordPress pages along the way.

The tool runs inside **Claude Code**: deterministic Python scripts do the
per-row work, while Claude handles planning, user interaction, and exception
cases. It is **multi-tenant by design** — every user supplies their own
credentials via a gitignored config file. There are **no central services** and
nothing to host.

## Status

**Pre-release, and proven end-to-end on a live pilot.** Phases 1–9.8 of the build are
complete: 10 real products are published on the pilot client's live WordPress site in
Dutch and French, registered on GS1 production, and every one of their QR codes resolves
(`GET id.gs1.org/01/{gtin14}` → 307 → the product page → 200), including a physical
phone-scan of a printed sample. The full operator flow has been driven end-to-end from a
fresh Claude Code session through its confirmation gates.

Not yet released as `v0.1.0` — the version in `pyproject.toml` is still `0.0.1`, and the
tag, changelog, and MCP registry entry are Phase 11. Expect rough edges outside the paths
the pilot exercised, and read [`docs/troubleshooting.md`](docs/troubleshooting.md) before
pointing it at a new client.

See [`CHANGELOG.md`](CHANGELOG.md) for what has shipped, and
[`docs/ROADMAP.md`](docs/ROADMAP.md) for where the build stands.

## Quickstart

```bash
git clone https://github.com/NextGenDataLead/gs1-product-link.git
cd gs1-product-link

python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

ruff check && ruff format --check    # lint
mypy --strict lib                    # type-check
pytest                               # test suite

cp clients.example.yml clients.yml   # per-client config (gitignored)
cp .env.example .env                 # secrets only    (gitignored)
```

Then **run it from Claude Code** — that is the operating surface, not an alternative to
the command line:

```
run for {client_id}
```

This loads the `flow-orchestrator` skill, which drives the pipeline and stops at every
operator gate: language selection → copy review → plan review → per-row diff → a mandatory
production environment-confirmation → execute → summary. Nothing proceeds without your
answer. **Claude.ai, Claude Desktop, and Claude Cowork are out of scope** — Cowork was
removed because it runs in a remote sandbox that would need your production credentials.

The Python scripts underneath (`inspect_export`, `parse_export`, `report_quality`,
`run_plan`, `run_execute`, …) are what Claude Code invokes for you. You run them directly
for install verification and when mapping a new client's export, not to publish.

**Full walkthrough, including onboarding a new client: [`docs/setup.md`](docs/setup.md).**

## Documentation

Start here:

- **[docs/setup.md](docs/setup.md)** — install, configure, run, and onboard a client.
- **[docs/troubleshooting.md](docs/troubleshooting.md)** — every error type, and the traps
  already paid for on a live site.

The two external systems:

- [docs/gs1-nl-onboarding.md](docs/gs1-nl-onboarding.md) — contracts, OAuth2, resolution, retraction.
- [docs/wordpress-onboarding.md](docs/wordpress-onboarding.md) — app passwords, post types, WPML, media.

Data in, page out:

- [docs/data-source-export-schema.md](docs/data-source-export-schema.md) — reading a GDSN or flat export.
- [docs/template-variables.md](docs/template-variables.md) — `acf_map` and template variables.
- [docs/costs.md](docs/costs.md) — what running this costs.
- [docs/architecture.svg](docs/architecture.svg) — end-to-end diagram.

The two authoritative design documents — read them before contributing:

- **[docs/PROJECT_HANDOVER.md](docs/PROJECT_HANDOVER.md)** — the *why*: scope,
  architecture decisions, phase plan, risks, and reference artifacts.
- **[docs/IMPLEMENTATION_SPEC.md](docs/IMPLEMENTATION_SPEC.md)** — the *how*:
  types, module contracts, error handling, idempotency, and Definition of Done
  per phase (§12).

Also: [docs/OPEN_DECISIONS.md](docs/OPEN_DECISIONS.md) — decisions analysed but not yet
taken (currently: where credentials should live) · [docs/PREPARATION.md](docs/PREPARATION.md) —
operator preparation checklist · [docs/ROADMAP.md](docs/ROADMAP.md) — phase status ·
[docs/clients/](docs/clients/) — per-client notes, including the pilot's page model and
live audit trail.

## Cost to users

The tool itself is free (open-source, self-hosted). The only GS1 NL cost a user
incurs is their **existing GS1 Data Source contract** — the same one that gave
them their GTINs. The GS1 Digital Link API (the write path this tool automates)
is free of charge, and the Excel export from MyGS1 is a standard feature. GS1
Data Link (the paid read API) is explicitly out of scope. The one metered
component is optional: see [docs/costs.md](docs/costs.md).

## Safety

This tool writes to a live website and creates **permanent** GS1 records — the Digital Link
API has no DELETE, so a record can only be deactivated, never removed. Accordingly:

- Dry-run before every real run.
- A live production run requires `--i-understand-production`.
- `pytest` deselects the staging tests by default; they write to live WordPress and GS1
  production. Read `.env.example`'s staging block before running `pytest -m staging`.
- Never point a smoke test at a real product's GTIN.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). CI runs `ruff check`, `ruff format --check`,
`mypy --strict lib`, and `pytest` on every push — all four must pass.

## License

[MIT](LICENSE).
