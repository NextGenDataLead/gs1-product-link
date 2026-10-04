"""Ask the live site which products still have no tagline or Eigenschappen text.

Usage:
    python -m scripts.report_live_copy [CLIENT_ID] [--json]

``CLIENT_ID`` may be omitted when ``clients.yml`` defines exactly one client.

Every other "does this need copy?" in this pipeline is answered from ``state.json``. That ledger
records what *this machine* wrote, and it does not travel: two machines publish, two ledgers
diverge, and neither can say what is on the site. This asks the site.

For each product on the process list it reports one of three states:

* **has text** — live in every configured language, with every generated field populated;
* **needs text** — a page missing, or live with a blank generated field, and the export carries
  something to write copy from;
* **no inputs** — the same, but the export carries neither attr 1083 nor attr 1067, so nothing on
  this machine can supply it. That is a MyGS1 worklist, not a Generate button.

What it deliberately does **not** answer is whether the inputs *changed* since the live text was
written. That needs a fingerprint of the published unit, which is exactly the ledger this exists
to stop depending on — so it is the operator's call, made by picking products to regenerate.

**Two requests per language to list, then one per page to read.** The listing is language-scoped
and its ``acf`` payload is empty on a WPML site (measured: all thirteen French pages read as
textless while rendering their text correctly), so every page is re-read by id with no ``lang``
parameter. That is the only read that tells the truth — see :meth:`lib.wp_client.read_page`.

**Read-only.** GETs and nothing else, and it reads no state at all.

Emits (--json): the report on stdout, for the operator shell to parse.
Exit codes:
    0  the site was read
    2  config error, or the site could not be read
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from lib.config import ClientConfig, get_client, resolve_client_id
from lib.env import load_env
from lib.errors import ConfigError, ExportParseError, MissingCredentialError, WordPressAPIError
from lib.live_copy import LiveCopyReport, LiveText, classify
from lib.media_video import canon_gtin
from lib.preflight import held_for_video, in_scope
from lib.records import ProductRecord
from lib.wp_client import WordPressClient

_EXIT_OK = 0
_EXIT_ERROR = 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="report_live_copy",
        description="Which products have no tagline or Eigenschappen text on the live site.",
    )
    parser.add_argument("client_id", nargs="?", help="Key under clients: in clients.yml")
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON on stdout")
    return parser.parse_args(argv)


def _load_products(cfg: ClientConfig) -> list[ProductRecord]:
    """The parsed export, from the path every other script reads it from."""
    path = Path("output") / cfg.client_id / "data" / "products.json"
    if not path.is_file():
        raise ConfigError(f"{path} does not exist — run `python -m scripts.parse_export` first")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [ProductRecord.model_validate(entry) for entry in raw]


def _fetch(cfg: ClientConfig) -> dict[tuple[str, str], LiveText]:
    """Every tool-made page, with its generated fields read the one way that works.

    The listing is asked per language because an unscoped query on a WPML site answers with the
    default language only. The *fields* are then read by id with no language parameter, because a
    scoped read answers ``acf: []`` for a page that has them — which is how thirteen correct
    French pages came to look empty.
    """
    fields = tuple(cfg.wordpress.acf_map)
    found: dict[tuple[str, str], LiveText] = {}
    with WordPressClient(cfg.wordpress) as client:
        for language in cfg.wordpress.languages:
            for listed in client.list_pages_with_gtin(cfg.wordpress.post_type, language):
                meta = listed.get("meta")
                gtin = str(meta.get("gtin", "")) if isinstance(meta, dict) else ""
                if not gtin:
                    continue
                page_id = int(listed.get("id", 0))
                page = client.read_page(cfg.wordpress.post_type, page_id) or {}
                acf = page.get("acf")
                values = acf if isinstance(acf, dict) else {}
                filled = {f for f in fields if str(values.get(f) or "").strip()}
                found[canon_gtin(gtin), language] = LiveText(
                    gtin=canon_gtin(gtin),
                    language=language,
                    page_id=page_id,
                    filled=frozenset(filled),
                    empty=tuple(f for f in fields if f not in filled),
                )
    return found


def _as_json(cfg: ClientConfig, report: LiveCopyReport, checked_at: str) -> dict[str, object]:
    return {
        "client_id": cfg.client_id,
        "checked_at": checked_at,
        "site_url": cfg.wordpress.site_url,
        "languages": list(report.languages),
        "fields": list(report.fields),
        "counts": report.counts,
        "products": [
            {
                "gtin": product.gtin,
                "name": product.name,
                "bucket": product.bucket.value,
                "missing_languages": list(product.missing_languages),
                "absent_languages": list(product.absent_languages),
                "held_for_video": product.held_for_video,
            }
            for product in report.products
        ],
    }


def _render(report: LiveCopyReport) -> None:
    counts = report.counts
    # Worded by what happens to each group, and in that order, because that is what the reader
    # is deciding. The shell says the same three things in the same three words.
    print(
        f"{counts['in_scope']} product(s) in scope: {counts['needs_text']} to process (no live "
        f"text, input available), {counts['no_inputs']} skipped (no live text, input "
        f"unavailable), {counts['has_text']} skipped (live text already)."
    )
    for product in report.needs_text:
        where = ", ".join(product.missing_languages)
        held = " (also waiting on a confirmed video)" if product.held_for_video else ""
        print(f"  [process] {product.gtin} {product.name} — no live text in {where}{held}")
    for product in report.no_inputs:
        print(f"  [skip] {product.gtin} {product.name} — no attr 1083 or 1067 in the export")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        cfg = get_client(resolve_client_id(args.client_id))
        products = in_scope(cfg, _load_products(cfg))
        held = {product.gtin14 for product in held_for_video(cfg, products)}
        live = _fetch(cfg)
    except (ConfigError, ExportParseError, MissingCredentialError, WordPressAPIError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_ERROR

    report = classify(products, live, cfg.wordpress.languages, held_for_video=held)
    checked_at = datetime.now(UTC).isoformat(timespec="seconds")
    if args.json:
        print(json.dumps(_as_json(cfg, report, checked_at), indent=2))
    else:
        _render(report)
    return _EXIT_OK


if __name__ == "__main__":
    load_env()
    raise SystemExit(main())
