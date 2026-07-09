# GS1 Digital Link Orchestrator

Turn compliant product data in **GS1 Data Source** into printable, GS1-compliant
QR codes (a *QR code powered by GS1*, encoding a GS1 Digital Link URI) whose
resolver target points at the supplier's own website — and provision the
destination WordPress pages along the way.

The tool runs inside **Claude Cowork**: deterministic Python scripts do the
per-row work, while Claude handles planning, user interaction, and exception
cases. It is **multi-tenant by design** — every user supplies their own
credentials via a gitignored config file. There are **no central services** and
nothing to host.

## Status

**v0.0.1 — Phase 1 (repository skeleton).** Not yet functional. This repository
currently contains the project structure, configuration contracts, tooling, and
CI. Implementation lands phase by phase per the plan in the handover document.

See [`CHANGELOG.md`](CHANGELOG.md) for what has shipped so far.

## Cost to users

The tool itself is free (open-source, self-hosted). The only GS1 NL cost a user
incurs is their **existing GS1 Data Source contract** — the same one that gave
them their GTINs. The GS1 Digital Link API (the write path this tool automates)
is free of charge, and the Excel export from MyGS1 is a standard feature. GS1
Data Link (the paid read API) is explicitly out of scope.

## Documentation

The two authoritative documents — read them before contributing:

- **[docs/PROJECT_HANDOVER.md](docs/PROJECT_HANDOVER.md)** — the *why*: scope,
  architecture decisions, phase plan, risks, and reference artifacts.
- **[docs/IMPLEMENTATION_SPEC.md](docs/IMPLEMENTATION_SPEC.md)** — the *how*:
  types, module contracts, error handling, idempotency, and Definition of Done
  per phase.

Supporting material:

- [docs/PREPARATION.md](docs/PREPARATION.md) — operator preparation checklist.
- [docs/architecture.svg](docs/architecture.svg) — end-to-end system diagram.

## Getting started (developers)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

ruff check                 # lint
ruff format --check        # formatting
mypy --strict lib          # type-check
pytest                     # tests
```

Copy `clients.example.yml` to `clients.yml` and `.env.example` to `.env`, then
fill in per-client configuration and credentials. Both real files are
gitignored and must never be committed.

## License

[MIT](LICENSE).
