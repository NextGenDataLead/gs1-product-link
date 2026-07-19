"""Build a run plan by classifying products against prior state (IMPLEMENTATION_SPEC §8.2).

Usage:
    python -m scripts.run_plan CLIENT_ID [--products PATH]

Loads the client config, its persisted state, and the parsed products, then classifies
each ``(GTIN, language)`` as NEW / UNCHANGED / CHANGED (``lib.state.diff_against_state``)
and writes the resulting :class:`~lib.records.Plan` to ``output/{client_id}/plan.json``.

When the client configures a ``website_status`` control file, products are first gated
to the create-only candidate set — eligible only when the GTIN is already in GS1 and not
yet on the website. Products that are already live, not yet in GS1, or absent from the
control file are excluded from the plan and reported in the summary. Without a
``website_status`` config, every product is planned (the plain spec behaviour).

    --products:   default output/{client_id}/data/products.json

A *corrupt* state file is not fatal (E19): ``load_state`` moves it aside and starts fresh,
and the summary leads with a warning — every row then re-plans as NEW, which is idempotent
to execute but rewrites live pages and resolver targets. An *unreadable* one still exits 2.

Emits:  output/{client_id}/plan.json (a Plan as JSON)
Exit codes:
    0  plan written
    2  config/state error (bad client id, unreadable products/state/control file,
       missing slug/target_url patterns)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from lib.categories import resolve_category
from lib.config import ClientConfig, get_client
from lib.errors import ConfigError, GeneratorError, StateError, WebsiteStatusError
from lib.generator import load_cache, merge_generated
from lib.records import Plan, PlanClassification, ProductRecord, SourceIssue
from lib.state import diff_against_state, load_state
from lib.website_status import WebsiteStatus, load_website_status

_log = logging.getLogger("scripts.run_plan")

_EXIT_OK = 0
_EXIT_CONFIG_ERROR = 2

#: Leads the summary when prior state was reset from a corrupt file (E19). Every row then
#: re-plans as NEW: re-running them is idempotent, but it rewrites live pages and resolver
#: targets rather than skipping them, so the operator must see this before confirming.
_STATE_RESET_WARNING = (
    "WARNING: prior state was corrupt and has been reset (backed up alongside state.json). "
    "All rows re-plan as NEW — executing them will rewrite live pages and resolver targets."
)


def _default_products_path(client_id: str) -> Path:
    """The default parsed-products location written by ``scripts/parse_export.py``."""
    return Path("output") / client_id / "data" / "products.json"


def _load_products(path: Path) -> list[ProductRecord]:
    """Read the parsed-products JSON array into ``ProductRecord``s."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [ProductRecord.model_validate(item) for item in data]


def _gate(
    products: list[ProductRecord], statuses: dict[str, WebsiteStatus]
) -> tuple[list[ProductRecord], dict[str, int]]:
    """Filter products to the create-only candidate set (in GS1, not yet on site).

    Returns the eligible products plus a tally of why the rest were excluded:
    ``on_website`` (already live), ``not_in_gs1`` (GS1 record absent), and ``unknown``
    (GTIN not present in the control file).
    """
    eligible: list[ProductRecord] = []
    excluded = {"on_website": 0, "not_in_gs1": 0, "unknown": 0}
    for product in products:
        # Statuses are keyed by GTIN-14 so a 13-digit control-file barcode joins to a
        # 14-digit product GTIN regardless of a leading zero.
        status = statuses.get(product.gtin14)
        if status is None:
            excluded["unknown"] += 1
        elif status.on_website:
            excluded["on_website"] += 1
        elif not status.in_gs1:
            excluded["not_in_gs1"] += 1
        else:
            eligible.append(product)
    return eligible, excluded


def _assign_categories(
    cfg: ClientConfig, products: list[ProductRecord]
) -> tuple[list[ProductRecord], list[SourceIssue]]:
    """Assign each product's site category from ``cfg.categories`` (Phase 7.5).

    A no-op when the client has no ``categories`` config. Otherwise resolves every product's
    category (per-GTIN override > brick map > none) and returns products with ``category`` set,
    plus one :class:`SourceIssue` per product whose brick maps to nothing. An unmapped brick
    warns and leaves the category unset — the tool never guesses. Called before classification
    so the category is part of the content hash: a category change reclassifies as CHANGED.
    """
    if cfg.categories is None:
        return products, []

    allowed = frozenset(cfg.categories.terms)
    assigned: list[ProductRecord] = []
    issues: list[SourceIssue] = []
    for product in products:
        resolution = resolve_category(
            product,
            brick_category_map=cfg.categories.brick_category_map,
            overrides=cfg.categories.overrides,
            allowed_terms=allowed,
        )
        if resolution.term is None:
            assigned.append(product)
        else:
            assigned.append(product.model_copy(update={"category": resolution.term}))
        if resolution.issue is not None:
            issues.append(resolution.issue)

    for brick in sorted({i.value for i in issues if i.issue == "category_unmapped" and i.value}):
        _log.warning("GPC brick %s maps to no category term; leaving category unset", brick)
    missing_brick = sum(1 for i in issues if i.issue == "category_brick_missing")
    if missing_brick:
        _log.warning("%d product(s) have no GPC brick to derive a category from", missing_brick)
    return assigned, issues


def _generate_content(
    cfg: ClientConfig, products: list[ProductRecord]
) -> tuple[list[ProductRecord], list[SourceIssue]]:
    """Fold cached generated copy onto each product (generator SPEC), cache-only — no network.

    A no-op when the client has no ``generator`` config. Otherwise loads the generated-copy cache
    and runs :func:`lib.generator.merge_generated`, materialising the combined title, tagline, and
    three-part description onto each record so generated changes enter the content hash and
    reclassify as CHANGED — mirroring :func:`_assign_categories`, and for the same reason it runs
    before ``diff_against_state``. Filling a missing French name from the cache also stops the E18
    skip firing for a gap the generator has since filled; a genuine gap (no fresh cache entry) gets
    no generated fields and falls to the E18 backstop. Returns the products with generated fields
    set, plus one :class:`SourceIssue` per generated/adjusted value and per blank marketing message.
    """
    if cfg.generator is None:
        return products, []
    cache = load_cache(cfg.client_id)
    return merge_generated(
        products,
        cache,
        cfg.wordpress.languages,
        cfg.wordpress.default_language,
        cfg.generator.prompt_version,
    )


def _write_issue_report(client_id: str, filename: str, issues: list[SourceIssue]) -> None:
    """Write a per-step issue report to ``output/{client}/data/{filename}``, always — even empty.

    Written unconditionally (like ``parse_export``'s source_issues.json) so an empty file means
    "this run found nothing" and a missing file means "this step did not run". Each step owns its
    own file, separate from source_issues.json, which ``parse_export`` owns and overwrites.
    """
    path = Path("output") / client_id / "data" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [issue.model_dump(mode="json") for issue in issues]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_plan(
    cfg: ClientConfig, products: list[ProductRecord]
) -> tuple[Plan, dict[str, int], bool, list[SourceIssue], list[SourceIssue]]:
    """Gate, assign categories, merge generated copy, classify, and assemble the :class:`Plan`.

    Returns the plan, the gate-exclusion tally, whether prior state was reset from a corrupt
    file (E19) — which the caller must surface, because it means every row re-plans as NEW —
    the category-mapping issues (unmapped bricks left unset), and the generated-content issues
    (one per generated/adjusted value and per blank marketing message).
    """
    if cfg.website_status is not None:
        candidates, excluded = _gate(products, load_website_status(cfg.website_status))
    else:
        candidates, excluded = products, {"on_website": 0, "not_in_gs1": 0, "unknown": 0}

    candidates, category_issues = _assign_categories(cfg, candidates)
    candidates, generated_issues = _generate_content(cfg, candidates)

    state = load_state(cfg.client_id)
    rows = diff_against_state(candidates, state, cfg.wordpress.languages, cfg.wordpress)
    counts = {c: sum(1 for row in rows if row.classification is c) for c in PlanClassification}
    plan = Plan(
        client_id=cfg.client_id,
        generated_at=datetime.now(UTC),
        total=len(rows),
        counts=counts,
        rows=rows,
    )
    return plan, excluded, state.reset_from_corrupt, category_issues, generated_issues


def _write_plan(client_id: str, plan: Plan) -> Path:
    """Write ``plan.json`` under the client's output directory and return its path."""
    path = Path("output") / client_id / "plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2)
    path.write_text(payload, encoding="utf-8")
    return path


def _summary(
    plan: Plan,
    excluded: dict[str, int],
    state_was_reset: bool,
    unmapped_categories: int = 0,
    generated_issues: int = 0,
) -> str:
    """Render the stderr summary (§8.2): gate exclusions when non-zero, E19 reset when it fired.

    The reset warning leads, because it reframes every count below it — with no prior state
    every row is NEW, and that is a full rewrite rather than the incremental run the operator
    is expecting.
    """
    line = (
        f"{plan.counts[PlanClassification.NEW]} new, "
        f"{plan.counts[PlanClassification.UNCHANGED]} unchanged, "
        f"{plan.counts[PlanClassification.CHANGED]} changed"
    )
    held = plan.counts[PlanClassification.HELD]
    if held:
        line += f", {held} held (unpublished; run_execute skips these without --revive)"
    gated = sum(excluded.values())
    if gated:
        line += (
            f"; {gated} excluded ("
            f"{excluded['on_website']} already on website, "
            f"{excluded['not_in_gs1']} not yet in GS1, "
            f"{excluded['unknown']} not in control file)"
        )
    if unmapped_categories:
        line += f"; {unmapped_categories} product(s) with unmapped category (left unset)"
    if generated_issues:
        line += f"; {generated_issues} generated-content note(s) — see generated_issues.json"
    if state_was_reset:
        line = f"{_STATE_RESET_WARNING}\n{line}"
    return line


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="run_plan", description="Build a run plan from products.")
    parser.add_argument("client_id", help="Key under clients: in clients.yml")
    parser.add_argument(
        "--products",
        help="Path to the parsed products JSON (default: output/{id}/data/products.json)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    try:
        cfg = get_client(args.client_id)
        products_path = (
            Path(args.products) if args.products else _default_products_path(cfg.client_id)
        )
        products = _load_products(products_path)
        plan, excluded, state_was_reset, category_issues, generated_issues = _build_plan(
            cfg, products
        )
    except (
        ConfigError,
        GeneratorError,
        StateError,
        WebsiteStatusError,
        FileNotFoundError,
        json.JSONDecodeError,
        ValidationError,
    ) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return _EXIT_CONFIG_ERROR

    path = _write_plan(cfg.client_id, plan)
    if cfg.categories is not None:
        _write_issue_report(cfg.client_id, "category_issues.json", category_issues)
    if cfg.generator is not None:
        _write_issue_report(cfg.client_id, "generated_issues.json", generated_issues)
    _log.info("wrote plan for %s (%d rows) to %s", cfg.client_id, plan.total, path)
    print(
        _summary(plan, excluded, state_was_reset, len(category_issues), len(generated_issues)),
        file=sys.stderr,
    )
    return _EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
