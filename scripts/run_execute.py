"""Execute a confirmed run plan against WordPress and GS1 (IMPLEMENTATION_SPEC §8.3).

Usage:
    python -m scripts.run_execute CLIENT_ID (--plan PATH | --confirmed PATH) [--dry-run]

Work is grouped by GTIN and runs in two phases, because some of it is per language and
some of it is per *product*:

1. **Per confirmed ``(GTIN, language)`` row:** render the product template → upsert the
   WordPress page → verify it serves 200.
2. **Per GTIN, once every one of its rows has survived phase 1:** link the pages as
   translations of one another (§4.5) → set **one** GS1 resolver target carrying a link
   for *every* language (GET-before-write via ``safe_upsert``, §5.4) → render the QR.

The split is not tidiness. GS1's CreateOrUpdate **replaces** the whole ``links`` array,
so a write per language would leave only the last language's link — silently destroying
the others. And a translation group cannot be linked until every page in it exists. If
any row of a GTIN fails phase 1 the GTIN gets neither: a partial link set would destroy
the missing language's link, and persisting the survivor's state would make the next run
classify it UNCHANGED and never retry.

Each row's :class:`~lib.records.RunOutcome` is appended to
``output/{client_id}/runs/{ts}.jsonl`` regardless of success, and successful rows update
``output/{client_id}/state.json``. The run is idempotent (§6.5) and resumable: re-running
the same confirmed plan yields the same final state.

``--dry-run`` (§5.4 Level B) walks the plan and logs the intended WordPress/GS1
mutations without performing them — no HTTP writes, no QR files, no state update.

Exit codes:
    0  every confirmed row succeeded
    1  one or more rows errored (state still saved for the rows that succeeded)
    2  config/setup error (bad client id, unreadable/invalid plan, missing GS1 creds)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from pydantic import ValidationError

from lib.acf import build_acf_payload
from lib.config import ClientConfig, GS1LinkConfig, get_client
from lib.errors import ConfigError, StateError
from lib.gs1_dl_client import GS1Config as ResolvedGS1Config
from lib.gs1_dl_client import GS1DigitalLinkClient, LinkInput
from lib.qr import render_qr
from lib.records import (
    ConfirmedPlan,
    Plan,
    PlanRow,
    ProductRecord,
    RunOutcome,
    State,
    StateEntry,
)
from lib.state import load_state, save_state
from lib.templates import TemplateEngine
from lib.wp_client import WordPressClient

_log = logging.getLogger("scripts.run_execute")

_EXIT_OK = 0
_EXIT_ERRORS = 1
_EXIT_CONFIG_ERROR = 2

#: Fallback resolver link type when a client defines no ``gs1_links`` (§2.4).
_DEFAULT_LINK_TYPE = "pip"
#: Run-log timestamp format (UTC), shared with the JSONL filename.
_TS_FORMAT = "%Y%m%dT%H%M%SZ"


# --- Plan loading ------------------------------------------------------------


def _load_confirmed(args: argparse.Namespace) -> ConfirmedPlan:
    """Load the plan and resolve the confirmed ``(gtin, language)`` subset (§8.3).

    ``--confirmed`` is read as a :class:`ConfirmedPlan`; ``--plan`` is read as a
    :class:`Plan` with every row implicitly confirmed.
    """
    if args.confirmed:
        data = json.loads(Path(args.confirmed).read_text(encoding="utf-8"))
        return ConfirmedPlan.model_validate(data)
    plan = Plan.model_validate(json.loads(Path(args.plan).read_text(encoding="utf-8")))
    confirmed = {(row.gtin, row.language) for row in plan.rows}
    return ConfirmedPlan(plan=plan, confirmed_gtins_by_lang=confirmed)


# --- Per-row helpers ---------------------------------------------------------


def _client_meta(cfg: ClientConfig) -> dict[str, str]:
    """Return the client-level template context (§4.6)."""
    return {
        "id": cfg.client_id,
        "display_name": cfg.display_name,
        "default_language": cfg.wordpress.default_language,
    }


class _Page(NamedTuple):
    """One language's live page: what the per-GTIN phase needs to know about it."""

    page_id: int
    url: str
    title: str


def _known_pages(gtin: str, fresh: dict[str, _Page], state: State) -> dict[str, _Page]:
    """Every language this GTIN has a page for — this run's, plus state's for the rest.

    An operator can confirm rows individually, so a run may carry only the fr row of a
    GTIN whose nl page already exists. The GS1 link array replaces, and WPML's translation
    group is the full set, so building either from the confirmed rows alone would drop nl
    — deleting its resolver link and breaking the translation pair. The state entry is the
    only record of a page this run did not touch, so it is what the missing languages are
    rebuilt from.

    Fresh pages win: a language written this run is more current than its state entry.
    """
    known = dict(fresh)
    for language, entry in state.entries.get(gtin, {}).items():
        if language not in known:
            known[language] = _Page(entry.wp_page_id, entry.wp_url, entry.title or "")
    return known


def _link_title(
    link: GS1LinkConfig,
    cfg: ClientConfig,
    product: ProductRecord,
    language: str,
    fallback: str,
) -> str:
    """Resolve a resolver link's title from its ``title_pattern`` (§2.4).

    Takes a product and a language rather than a :class:`PlanRow`: a link is built for
    every language of the GTIN, including ones whose row was not confirmed this run and
    so has no row at all (see :func:`_known_pages`).
    """
    if not link.title_pattern:
        return fallback
    name = product.product_name.get(language, cfg.wordpress.default_language) or fallback
    return link.title_pattern.format(
        product_name=name, title=fallback, gtin=product.gtin, brand=product.brand
    )


def _build_links(
    cfg: ClientConfig, product: ProductRecord, pages: dict[str, _Page]
) -> list[LinkInput]:
    """Build the resolver link set for one GTIN, spanning every known language (§4.3).

    This is the whole record's link set, not one language's: GS1's CreateOrUpdate replaces
    the ``links`` array wholesale, so whatever is omitted here is deleted from the record.

    Languages are emitted in sorted order so :func:`_link_set_hash` is stable across runs
    regardless of plan order.
    """
    configs = cfg.gs1_links or [GS1LinkConfig(link_type=_DEFAULT_LINK_TYPE, default=True)]
    return [
        LinkInput(
            link_type=link.link_type,
            language=language,
            link_title=_link_title(link, cfg, product, language, pages[language].title),
            target_url=pages[language].url,
            # "standaardlink voor nl, niet voor fr": only the default language's link is
            # the default one, however many languages the record carries.
            default_link_type=link.default and language == cfg.wordpress.default_language,
            public=link.public,
            media_type=cfg.gs1.default_media_type,
        )
        for language in sorted(pages)
        for link in configs
    ]


def _link_set_hash(links: list[LinkInput]) -> str:
    """Return a stable SHA-256 of the resolver link set for change detection (§5.4)."""
    canonical = json.dumps(links, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _digital_link_url(cfg: ClientConfig, row: PlanRow) -> str:
    """Build the canonical Digital Link URI for a GTIN (``.../01/{gtin14}``)."""
    return cfg.gs1.digital_link_url_pattern.format(gtin14=row.product.gtin14)


# --- Execution ---------------------------------------------------------------


def _upsert_row(  # noqa: PLR0913 — one collaborator per step, plus the outcome it annotates
    cfg: ClientConfig,
    row: PlanRow,
    wp: WordPressClient,
    engine: TemplateEngine,
    state: State,
    outcome: RunOutcome,
) -> _Page:
    """Phase 1 for one row: render, upsert the page, verify it serves. Raises on failure.

    Deliberately writes no state and sets no final status — the row is not done until its
    GTIN's per-product phase has run (see the module docstring).

    ``outcome`` is filled in as we go rather than from the return value, so that a page
    created and *then* failed by ``verify_url`` still reports its id and URL in the run
    log. Without that the operator gets an error naming no page.
    """
    html = engine.render(row.product, row.language, _client_meta(cfg))
    # Themes that render from ACF (Oxygen) ignore post_content entirely, so for those
    # clients the ACF payload *is* the page. The body is still written: it is inert
    # where it is ignored, and it is what non-ACF clients render from.
    acf = build_acf_payload(row.product, row.language, cfg.wordpress.acf_map)
    prior = state.entries.get(row.gtin, {}).get(row.language)
    page = wp.upsert_page(
        post_type=cfg.wordpress.post_type,
        slug=row.slug,
        title=row.title,
        content=html,
        language=row.language,
        featured_media=None,  # image pipeline deferred to the Phase 9 pilot
        meta={"gtin": row.gtin},
        existing_id=prior.wp_page_id if prior else None,
        acf=acf,
    )
    page_url = page["link"]
    outcome.wp_page_id = page["id"]
    outcome.wp_url = page_url
    if not wp.verify_url(page_url):
        raise RuntimeError(f"WordPress URL {page_url} did not return 200")
    return _Page(page["id"], page_url, row.title)


def _item_description(cfg: ClientConfig, rows: list[PlanRow], pages: dict[str, _Page]) -> str:
    """The GS1 record's ``itemDescription`` — one per GTIN, so the default language's."""
    page = pages.get(cfg.wordpress.default_language)
    if page is not None and page.title:
        return page.title
    return rows[0].title


def _block_gtin(gtin: str, rows: list[PlanRow], outcomes: dict[str, RunOutcome]) -> None:
    """Fail every row of a GTIN whose sibling failed phase 1, writing no state.

    Neither half of the per-product phase can run: a link set built from the surviving
    languages would **replace** the array and delete the failed language's link, and a
    translation group cannot be linked to a page that does not exist. Marking the survivor
    ``error`` is not bookkeeping — persisting its state would have the next run classify it
    UNCHANGED, so the GS1 write would never be retried and the failure would vanish.
    """
    failed = sorted(lang for lang, o in outcomes.items() if o.status == "error")
    for row in rows:
        outcome = outcomes[row.language]
        if outcome.status == "error":
            continue
        outcome.status = "error"
        outcome.error = (
            f"blocked: language(s) {', '.join(failed)} of this GTIN failed, so its GS1 link "
            f"set and translation group were not written"
        )
        _log.error("row %s/%s blocked by failed sibling(s) %s", gtin, row.language, failed)


def _finish_gtin(  # noqa: PLR0913 — one collaborator per step; bundling them only hides them
    cfg: ClientConfig,
    gtin: str,
    rows: list[PlanRow],
    fresh: dict[str, _Page],
    wp: WordPressClient,
    gs1: GS1DigitalLinkClient,
    state: State,
    ts: datetime,
    outcomes: dict[str, RunOutcome],
) -> None:
    """Phase 2: the writes that belong to the product rather than to one language."""
    pages = _known_pages(gtin, fresh, state)
    rebuilt = sorted(set(pages) - set(fresh))
    if rebuilt:
        _log.warning(
            "gtin %s: language(s) %s were not written this run; their resolver links and "
            "translation ids come from state, not from a page verified just now",
            gtin,
            rebuilt,
        )
    wp.link_translations({lang: page.page_id for lang, page in pages.items()})
    links = _build_links(cfg, rows[0].product, pages)
    gs1.safe_upsert(
        gtin=gtin,
        item_description=_item_description(cfg, rows, pages),
        links=links,
        is_enabled=True,
        overwrite=True,  # the plan is operator-confirmed; re-runs update in place (§6.5)
    )
    qr_paths = [str(p) for p in _render_qr_for(cfg, rows[0])]
    link_hash = _link_set_hash(links)
    for row in rows:
        outcome = outcomes[row.language]
        outcome.gs1_set = True
        outcome.qr_paths = qr_paths
        outcome.status = "ok"
        state.entries.setdefault(gtin, {})[row.language] = StateEntry(
            wp_page_id=fresh[row.language].page_id,
            wp_url=fresh[row.language].url,
            wp_featured_media_id=None,
            content_hash=row.content_hash,
            gs1_link_set_hash=link_hash,  # per-GTIN: every language shares the one link set
            last_run=ts,
            title=row.title,  # the next run diffs against this (§10.6.2)
        )


def _execute_gtin(  # noqa: PLR0913 — one collaborator per step; bundling them only hides them
    cfg: ClientConfig,
    gtin: str,
    rows: list[PlanRow],
    wp: WordPressClient,
    gs1: GS1DigitalLinkClient,
    engine: TemplateEngine,
    state: State,
    ts: datetime,
) -> list[RunOutcome]:
    """Run every confirmed row of one GTIN, then its per-product writes. Never raises."""
    outcomes = {
        row.language: RunOutcome(gtin=gtin, language=row.language, ts=ts, status="pending")
        for row in rows
    }
    fresh: dict[str, _Page] = {}
    for row in rows:
        try:
            fresh[row.language] = _upsert_row(cfg, row, wp, engine, state, outcomes[row.language])
        except Exception as exc:  # noqa: BLE001 — one bad row must not abort the run
            outcomes[row.language].status = "error"
            outcomes[row.language].error = repr(exc)
            _log.error("row %s/%s failed: %r", gtin, row.language, exc)

    if len(fresh) != len(rows):
        _block_gtin(gtin, rows, outcomes)
    else:
        try:
            _finish_gtin(cfg, gtin, rows, fresh, wp, gs1, state, ts, outcomes)
        except Exception as exc:  # noqa: BLE001 — one bad GTIN must not abort the run
            for row in rows:
                outcomes[row.language].status = "error"
                outcomes[row.language].error = repr(exc)
            _log.error("gtin %s failed its per-product writes: %r", gtin, exc)
    return [outcomes[row.language] for row in rows]


def _execute(  # noqa: PLR0913 — one collaborator per step; bundling them only hides them
    cfg: ClientConfig,
    rows: list[PlanRow],
    wp: WordPressClient,
    gs1: GS1DigitalLinkClient,
    engine: TemplateEngine,
    state: State,
    ts: datetime,
) -> list[RunOutcome]:
    """Execute the confirmed rows grouped by GTIN, returning outcomes in plan order.

    Grouped with a dict rather than by walking runs of adjacent rows: rows for one GTIN
    happen to be adjacent today only because ``diff_against_state`` builds them in a nested
    loop, and :class:`~lib.records.Plan` promises no such ordering.
    """
    by_gtin: dict[str, list[PlanRow]] = {}
    for row in rows:
        by_gtin.setdefault(row.gtin, []).append(row)

    done: dict[tuple[str, str], RunOutcome] = {}
    for gtin, gtin_rows in by_gtin.items():
        for outcome in _execute_gtin(cfg, gtin, gtin_rows, wp, gs1, engine, state, ts):
            done[(outcome.gtin, outcome.language)] = outcome
    return [done[(row.gtin, row.language)] for row in rows]


def _render_qr_for(cfg: ClientConfig, row: PlanRow) -> list[Path]:
    """Render the QR for a row, or nothing when the client has no QR config."""
    if cfg.qr is None:
        _log.warning("no qr config for client %s; skipping QR for %s", cfg.client_id, row.gtin)
        return []
    return render_qr(
        uri=_digital_link_url(cfg, row),
        output_dir=Path("output") / cfg.client_id / "qr",
        gtin=row.gtin,
        formats=cfg.qr.formats,
        size_mm=cfg.qr.size_mm,
        ecc=cfg.qr.error_correction,
        dpi=cfg.qr.dpi,
    )


def _preview_row(
    cfg: ClientConfig, row: PlanRow, engine: TemplateEngine, ts: datetime
) -> RunOutcome:
    """Render the template and log the intended mutations without performing them (§5.4)."""
    outcome = RunOutcome(gtin=row.gtin, language=row.language, ts=ts, status="dry-run")
    try:
        engine.render(row.product, row.language, _client_meta(cfg))
        # One line per row, but the GS1 write is per GTIN: a GTIN with two confirmed rows
        # gets one resolver write carrying both languages' links, not one write per line.
        _log.info(
            "[dry-run] %s/%s: would upsert WP %r page %r, then link this GTIN's languages "
            "as translations and point GS1 %s at their pages",
            row.gtin,
            row.language,
            cfg.wordpress.post_type,
            row.slug,
            _digital_link_url(cfg, row),
        )
    except Exception as exc:  # noqa: BLE001 — surface template errors as a failed preview row
        outcome.status = "error"
        outcome.error = repr(exc)
        _log.error("dry-run row %s/%s failed: %r", row.gtin, row.language, exc)
    return outcome


def _confirmed_rows(confirmed: ConfirmedPlan) -> list[PlanRow]:
    """Return the plan rows in the confirmed subset, in plan order."""
    keys = confirmed.confirmed_gtins_by_lang
    return [row for row in confirmed.plan.rows if (row.gtin, row.language) in keys]


def _write_log(log_path: Path, outcomes: list[RunOutcome]) -> None:
    """Append each outcome as one JSON line to the run log."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        for outcome in outcomes:
            handle.write(json.dumps(outcome.model_dump(mode="json"), ensure_ascii=False) + "\n")


def _run(
    cfg: ClientConfig,
    confirmed: ConfirmedPlan,
    resolved_gs1: ResolvedGS1Config | None,
    *,
    dry_run: bool,
) -> int:
    """Execute (or preview) the confirmed plan; return the process exit code."""
    rows = _confirmed_rows(confirmed)
    engine = TemplateEngine(cfg.client_id, cfg.template)
    ts = datetime.now(UTC)
    log_path = Path("output") / cfg.client_id / "runs" / f"{ts.strftime(_TS_FORMAT)}.jsonl"

    if dry_run or resolved_gs1 is None:
        outcomes = [_preview_row(cfg, row, engine, ts) for row in rows]
    else:
        state = load_state(cfg.client_id)
        with (
            WordPressClient(cfg.wordpress) as wp,
            GS1DigitalLinkClient(resolved_gs1) as gs1,
        ):
            outcomes = _execute(cfg, rows, wp, gs1, engine, state, ts)
        save_state(state)

    _write_log(log_path, outcomes)
    errors = sum(1 for o in outcomes if o.status == "error")
    _log.info("run complete: %d ok, %d error(s)", len(outcomes) - errors, errors)
    prefix = "[dry-run] " if dry_run else ""
    print(
        f"{prefix}{len(outcomes)} row(s), {errors} error(s); log: {log_path}",
        file=sys.stderr,
    )
    return _EXIT_ERRORS if errors else _EXIT_OK


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_execute", description="Execute a confirmed run plan."
    )
    parser.add_argument("client_id", help="Key under clients: in clients.yml")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--plan", help="Path to a Plan JSON (all rows confirmed)")
    source.add_argument("--confirmed", help="Path to a ConfirmedPlan JSON")
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview intended mutations without performing them"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    try:
        cfg = get_client(args.client_id)
        confirmed = _load_confirmed(args)
        resolved_gs1 = None if args.dry_run else cfg.gs1.resolve()
    except (
        ConfigError,
        StateError,
        FileNotFoundError,
        json.JSONDecodeError,
        ValidationError,
    ) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return _EXIT_CONFIG_ERROR
    return _run(cfg, confirmed, resolved_gs1, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
