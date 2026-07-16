"""Execute a confirmed run plan against WordPress and GS1 (IMPLEMENTATION_SPEC §8.3).

Usage:
    python -m scripts.run_execute CLIENT_ID (--plan PATH | --confirmed PATH) [--dry-run]

Per confirmed ``(GTIN, language)`` row, in order: render the product template →
upsert the WordPress page → verify it serves 200 → set the GS1 resolver target
(GET-before-write via ``safe_upsert``, §5.4) → render the QR. Each row's
:class:`~lib.records.RunOutcome` is appended to ``output/{client_id}/runs/{ts}.jsonl``
regardless of success, and successful rows update ``output/{client_id}/state.json``.
The run is idempotent (§6.5) and resumable: re-running the same confirmed plan yields
the same final state.

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

from pydantic import ValidationError

from lib.acf import build_acf_payload
from lib.config import ClientConfig, GS1LinkConfig, get_client
from lib.errors import ConfigError, StateError
from lib.gs1_dl_client import GS1Config as ResolvedGS1Config
from lib.gs1_dl_client import GS1DigitalLinkClient, LinkInput
from lib.qr import render_qr
from lib.records import ConfirmedPlan, Plan, PlanRow, RunOutcome, State, StateEntry
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


def _link_title(link: GS1LinkConfig, cfg: ClientConfig, row: PlanRow) -> str:
    """Resolve a resolver link's title from its ``title_pattern`` (§2.4)."""
    if not link.title_pattern:
        return row.title
    name = row.product.product_name.get(row.language, cfg.wordpress.default_language) or row.title
    return link.title_pattern.format(
        product_name=name, title=row.title, gtin=row.gtin, brand=row.product.brand
    )


def _build_links(cfg: ClientConfig, row: PlanRow, target_url: str) -> list[LinkInput]:
    """Build the resolver link set for one row, all pointing at ``target_url`` (§4.3)."""
    configs = cfg.gs1_links or [GS1LinkConfig(link_type=_DEFAULT_LINK_TYPE, default=True)]
    return [
        LinkInput(
            link_type=link.link_type,
            language=row.language,
            link_title=_link_title(link, cfg, row),
            target_url=target_url,
            default_link_type=link.default,
            public=link.public,
            media_type=cfg.gs1.default_media_type,
        )
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


def _execute_row(  # noqa: PLR0913 — one collaborator per pipeline step; grouping them adds noise
    cfg: ClientConfig,
    row: PlanRow,
    wp: WordPressClient,
    gs1: GS1DigitalLinkClient,
    engine: TemplateEngine,
    state: State,
    ts: datetime,
) -> RunOutcome:
    """Run one row end-to-end, updating ``state`` on success. Never raises."""
    outcome = RunOutcome(gtin=row.gtin, language=row.language, ts=ts, status="pending")
    try:
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

        links = _build_links(cfg, row, page_url)
        gs1.safe_upsert(
            gtin=row.gtin,
            item_description=row.title,
            links=links,
            is_enabled=True,
            overwrite=True,  # the plan is operator-confirmed; re-runs update in place (§6.5)
        )
        outcome.gs1_set = True

        outcome.qr_paths = [str(p) for p in _render_qr_for(cfg, row)]
        outcome.status = "ok"
        state.entries.setdefault(row.gtin, {})[row.language] = StateEntry(
            wp_page_id=page["id"],
            wp_url=page_url,
            wp_featured_media_id=None,
            content_hash=row.content_hash,
            gs1_link_set_hash=_link_set_hash(links),
            last_run=ts,
            title=row.title,  # the next run diffs against this (§10.6.2)
        )
    except Exception as exc:  # noqa: BLE001 — one bad row must not abort the run
        outcome.status = "error"
        outcome.error = repr(exc)
        _log.error("row %s/%s failed: %r", row.gtin, row.language, exc)
    return outcome


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
        _log.info(
            "[dry-run] %s/%s: would upsert WP %r page %r and set GS1 %s -> the page URL",
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
            outcomes = [_execute_row(cfg, row, wp, gs1, engine, state, ts) for row in rows]
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
