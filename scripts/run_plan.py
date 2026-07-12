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

from lib.config import ClientConfig, get_client
from lib.errors import ConfigError, StateError, WebsiteStatusError
from lib.records import Plan, PlanClassification, ProductRecord
from lib.state import diff_against_state, load_state
from lib.website_status import WebsiteStatus, load_website_status

_log = logging.getLogger("scripts.run_plan")

_EXIT_OK = 0
_EXIT_CONFIG_ERROR = 2


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
        status = statuses.get(product.gtin)
        if status is None:
            excluded["unknown"] += 1
        elif status.on_website:
            excluded["on_website"] += 1
        elif not status.in_gs1:
            excluded["not_in_gs1"] += 1
        else:
            eligible.append(product)
    return eligible, excluded


def _build_plan(cfg: ClientConfig, products: list[ProductRecord]) -> tuple[Plan, dict[str, int]]:
    """Gate, classify, and assemble the :class:`Plan` (plus the gate-exclusion tally)."""
    if cfg.website_status is not None:
        candidates, excluded = _gate(products, load_website_status(cfg.website_status))
    else:
        candidates, excluded = products, {"on_website": 0, "not_in_gs1": 0, "unknown": 0}

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
    return plan, excluded


def _write_plan(client_id: str, plan: Plan) -> Path:
    """Write ``plan.json`` under the client's output directory and return its path."""
    path = Path("output") / client_id / "plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2)
    path.write_text(payload, encoding="utf-8")
    return path


def _summary(plan: Plan, excluded: dict[str, int]) -> str:
    """Render the one-line stderr summary (§8.2), with gate exclusions when non-zero."""
    line = (
        f"{plan.counts[PlanClassification.NEW]} new, "
        f"{plan.counts[PlanClassification.UNCHANGED]} unchanged, "
        f"{plan.counts[PlanClassification.CHANGED]} changed"
    )
    gated = sum(excluded.values())
    if gated:
        line += (
            f"; {gated} excluded ("
            f"{excluded['on_website']} already on website, "
            f"{excluded['not_in_gs1']} not yet in GS1, "
            f"{excluded['unknown']} not in control file)"
        )
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
        plan, excluded = _build_plan(cfg, products)
    except (
        ConfigError,
        StateError,
        WebsiteStatusError,
        FileNotFoundError,
        json.JSONDecodeError,
        ValidationError,
    ) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return _EXIT_CONFIG_ERROR

    path = _write_plan(cfg.client_id, plan)
    _log.info("wrote plan for %s (%d rows) to %s", cfg.client_id, plan.total, path)
    print(_summary(plan, excluded), file=sys.stderr)
    return _EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
