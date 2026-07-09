# GS1 Data Source → Digital Link QR — file locations & context

Hub-notitie voor het GS1 Digital Link Orchestrator project. Bewaar deze in `MDP/Assets/` in Obsidian; link erheen vanuit de bovenliggende projectnotitie.

---

## Wat is dit project

Open-source tool die Nederlandse leveranciers van GS1 Data Source Excel-export → gepubliceerde WordPress-productpagina + geconfigureerde GS1-resolver-redirect + geprinte QR-code brengt. Draait in Claude Cowork; deterministische Python voor het werk, Claude voor planning en interactie. Multi-tenant via `clients.yml`. Zelf-gehost, geen centrale infra.

**Status:** klaar om te bouwen. Alle blokkerende vragen aan GS1 NL zijn beantwoord.

**Pilotklant:** Noviplast (custom post type `noviplast`, Polylang, NL + FR).

---

## Bestanden

### Google Drive (project handover)
- `PROJECT_HANDOVER.md` — het "waarom"-document (context, beslissingen, fasering)
- `IMPLEMENTATION_SPEC.md` — het "hoe"-document (types, contracten, error handling, tests)
- `GS1_NL_EMAIL.txt` — oorspronkelijke vragenlijst aan GS1 NL, inmiddels beantwoord (historische referentie)

### Lokale repo (nog aan te maken)
- Padvoorstel: `~/code/gs1-digital-link-orchestrator/`
- GitHub: TBD — repo aanmaken tijdens Phase 1

### Credentials (buiten deze notitie houden)
- GS1 NL Digital Link API keys (test + productie) → in `~/code/gs1-digital-link-orchestrator/.env`
- WordPress application password Noviplast staging → idem
- MyGS1 login voor Noviplast → password manager

---

## Externe referenties

- GS1 NL tarievenpagina: https://www.gs1.nl/producten-services/data-exchange/tarieven/
- Digital Link API OpenAPI spec: https://stgs1corpwebapist.blob.core.windows.net/yaml/digitallinkapi.yml
- MijnGS1 login: https://mijn-v2.gs1.nl
- GS1 NL developer portal (acceptance): https://gs1nl-api-acc-developer.gs1.nl/
- Kennisbank "Aan de slag met API's": https://www.gs1.nl/kennisbank/data-services/aan-de-slag-met-api-s/
- Noviplast productie: https://www.noviplast.nl

---

## De eerste prompt voor Claude Code

Deze prompt geef je aan Claude Code (in de terminal, of via de UI van Claude Code) om ontwikkeling te starten. Zorg dat `PROJECT_HANDOVER.md` en `IMPLEMENTATION_SPEC.md` toegankelijk zijn — bijvoorbeeld in dezelfde directory als waar je Claude Code start, of geüpload in de session context.

**Kopieer alles tussen de streepjes:**

---

```
I'm building an open-source tool called "gs1-digital-link-orchestrator" — a Python + TypeScript project that helps Dutch suppliers turn GS1 Data Source Excel exports into WordPress product pages with QR codes powered by GS1. The tool runs in Claude Cowork; deterministic Python does the work, Claude handles planning and user interaction. It's multi-tenant, open-source, self-hosted.

I have two authoritative documents that fully specify this project. Please read both in full before doing anything else:

1. PROJECT_HANDOVER.md — architecture, decisions, phases, rationale (the "why")
2. IMPLEMENTATION_SPEC.md — types, contracts, error handling, testing conventions (the "how")

IMPLEMENTATION_SPEC.md is your operational bible. If you're ever unsure how to implement something, the answer is in there. If it's genuinely not there, ask me — don't invent conventions.

# Your first task: Phase 1 — Repo skeleton

Per PROJECT_HANDOVER.md §8.2 Phase 1 and IMPLEMENTATION_SPEC.md §1 and §12:

- Initialise a Git repo with the exact structure from PROJECT_HANDOVER.md §7
- Commit:
  - MIT LICENSE
  - README.md (baseline: intent + status + links to both spec documents)
  - CHANGELOG.md starting at 0.0.1
  - .gitignore covering: clients.yml, .env, output/, input/, __pycache__, *.pyc, .venv, node_modules, dist, build
  - clients.example.yml (from PROJECT_HANDOVER.md §10.1)
  - .env.example (from PROJECT_HANDOVER.md §10.2)
- pyproject.toml per IMPLEMENTATION_SPEC.md §1.1 (Python 3.11+, httpx, pydantic, openpyxl, pyyaml, qrcode[pil], pystache, jsonschema; dev deps pytest, pytest-httpx, mypy, ruff; ruff and mypy configured)
- package.json for the MCPs (Node 20+, TypeScript, @modelcontextprotocol/sdk as dependency)
- schema/clients.schema.json derived from the Pydantic models in IMPLEMENTATION_SPEC.md §2.4
- GitHub Actions workflow (.github/workflows/ci.yml): on push and PR, run `ruff check`, `ruff format --check`, `mypy --strict lib`, `pytest`
- Empty directories with .gitkeep files where needed: lib/, scripts/, mcps/, skills/, templates/_default/, templates/, tests/lib/, tests/scripts/, tests/fixtures/, docs/, input/

# Working principles

- Follow the naming and style conventions in IMPLEMENTATION_SPEC.md §1 exactly. No deviation.
- Ask before improvising. If the spec doesn't cover something, ask me one clarifying question rather than guessing.
- Commit in small logical chunks with clear conventional-commit messages (feat:, chore:, docs:, ci:, etc.). Not one giant "initial commit".
- Do not start Phase 2 until we've agreed Phase 1 is done.

# Definition of Done for Phase 1 (from IMPLEMENTATION_SPEC.md §12)

- [ ] `ruff check` passes with zero warnings
- [ ] `mypy --strict lib` passes (even though lib/ is empty — add a lib/__init__.py)
- [ ] `pytest` runs (may pass with zero tests)
- [ ] GitHub Actions workflow file committed and green on push
- [ ] README.md links to PROJECT_HANDOVER.md and IMPLEMENTATION_SPEC.md

# Please start by

1. Confirming you've read both documents in full
2. Asking any clarifying questions you have before writing code (there may be none — but ask if so)
3. Then execute Phase 1, committing as you go

When Phase 1's Definition of Done is fully checked, stop and tell me. I'll review, we agree it's done, then I give you the go-ahead for Phase 2.
```

---

## Ontwikkelvolgorde na deze prompt

Elke volgende sessie: verwijs naar de relevante fase in `PROJECT_HANDOVER.md` §8.2 en de bijbehorende Definition of Done in `IMPLEMENTATION_SPEC.md` §12. Volgorde:

1. **Fase 1 — Repo skeleton** (deze eerste prompt)
2. **Fase 2 — GS1 Digital Link client + MCP** — vergt eerst de fixtures uit `IMPLEMENTATION_SPEC.md` §13.2 (curl commando's uitvoeren tegen test-API)
3. **Fase 3 — Excel parser + records** — vergt eerst een echte MyGS1-export van Noviplast per §13.1
4. **Fase 4 — WordPress client + MCP** — vergt staging WP toegang per §13.3
5. **Fase 5 — QR + templates**
6. **Fase 6 — lib, scripts, state**
7. **Fase 7 — Re-run en change detection**
8. **Fase 8 — Skills en flow orchestrator**
9. **Fase 9 — Pilot end-to-end**
10. **Fase 10 — Documentatie**
11. **Fase 11 — Productie cut + 0.1.0 release**

## Data die nog verzameld moet worden (§13 IMPLEMENTATION_SPEC)

- [ ] Echte MyGS1 Excel-export van Noviplast → `input/noviplast/products.xlsx` (blokkeert Fase 3)
- [ ] Vijf curl-responses van de Digital Link API → `tests/fixtures/gs1_api/` (blokkeert Fase 2 afronding)
- [ ] Staging WordPress voor Noviplast met application password → env vars (blokkeert Fase 4)

## Prompts voor volgende fases (template)

Voor elke volgende fase, gebruik ongeveer:

```
Phase X is now up. Per PROJECT_HANDOVER.md §8.2 Phase X and IMPLEMENTATION_SPEC.md §[relevant sections] and §12 Phase X, please implement:

[copy the phase steps from PROJECT_HANDOVER.md §8.2]

Follow the same working principles as Phase 1. Ask before deviating from the spec.

Definition of Done for Phase X:
[copy from IMPLEMENTATION_SPEC.md §12]

Start when ready.
```

---

## Gerelateerde notities

- [[Noviplast — klant context]] (nog aan te maken — bevat GLN, product-scope, contactpersoon)
- [[GS1 NL contact log]] (nog aan te maken — chronologisch overzicht van contactmomenten met GS1 NL)
- [[WordPress patterns — custom post types + Polylang]] (nog aan te maken — herbruikbaar patroon voor toekomstige klanten)

---

## Versie

- **v0.1** — 2026-05-27 — initieel opgezet met eerste Claude Code prompt en fase-volgorde
