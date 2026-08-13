"""Render the per-step issue reports into one human-readable data-quality worklist.

Usage:
    python -m scripts.report_quality [CLIENT_ID] [--out PATH]

``CLIENT_ID`` may be omitted when ``clients.yml`` defines exactly one client.

Reads the machine-readable issue files under ``output/{client_id}/data/``
(``source_issues.json``, ``generated_issues.json``, ``video_map_issues.json``,
``category_issues.json``) plus ``products.json`` (for product names), and writes a single
markdown report grouped by owner and action (what blocks publishing, what to review, what the
client fixes in MyGS1). Absent issue files are treated as empty (that producer has not run); a
missing data directory is a config error.

The rendering itself lives in :func:`lib.quality_report.render_quality_report` (pure); this script
is the I/O + clock wrapper (it stamps the snapshot date and each source's last-modified date).

Emits:  output/{client_id}/data-quality-report.md
Exit codes:
    0  report written
    2  config error (the client's output/{client_id}/data directory does not exist)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from lib.config import get_client, resolve_client_id
from lib.env import load_env
from lib.errors import ConfigError, ExportParseError, VideoMapError
from lib.mandatory import MandatoryGap, missing_mandatory
from lib.media_video import fully_mapped_gtins, load_video_map
from lib.preflight import in_scope
from lib.quality_report import render_quality_report
from lib.records import ProductRecord, SourceIssue

_EXIT_OK = 0
_EXIT_CONFIG_ERROR = 2

#: issue-file basename -> freshness key used in the report header.
_ISSUE_FILES = {
    "source_issues.json": "source",
    "generated_issues.json": "generated",
    "video_map_issues.json": "video_map",
    "category_issues.json": "category",
}


def _mtime(path: Path) -> str:
    """The file's last-modified date (YYYY-MM-DD), or an em-dash if it is absent."""
    if not path.exists():
        return "—"
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).strftime("%Y-%m-%d")


def _load_issues(path: Path) -> list[SourceIssue]:
    """Read an issue file into ``SourceIssue``s; an absent file is an empty list."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [SourceIssue.model_validate(item) for item in data]


def _load_products(path: Path) -> dict[str, ProductRecord]:
    """Read ``products.json`` into a GTIN-14-keyed map; absent or empty yields ``{}``."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    products = [ProductRecord.model_validate(item) for item in data]
    return {product.gtin14: product for product in products}


def _publish_blocks(
    client_id: str, products: dict[str, ProductRecord]
) -> tuple[dict[str, list[MandatoryGap]], list[str]]:
    """The two whole-SKU holds, recomputed from config rather than read from a run artifact.

    Recomputed on purpose: the report must be able to say what blocks publishing *today*, from an
    export the operator may have replaced since the last ``run_plan``. Reading a stale plan would
    describe a run rather than the data, and the data is what the client has to fix.

    Restricted to the products in scope, so the report lists work the operator asked for rather
    than the whole catalogue. Any config failure yields empty holds and leaves the rest of the
    report intact — ``doctor`` is where a broken config is reported, and a quality report that
    refuses to render because of it helps nobody.

    Returns:
        ``(gaps by GTIN, GTINs held for want of a confirmed video)``.
    """
    try:
        cfg = get_client(client_id)
    except (ConfigError, ExportParseError):
        return {}, []

    scoped = in_scope(cfg, list(products.values()))
    languages = cfg.wordpress.languages
    gaps = {
        product.gtin14: found
        for product in scoped
        if (found := missing_mandatory(product, cfg.export.gdsn_map, languages))
    }

    media = cfg.media
    if media is None or not media.restrict_to_mapped_gtins or not media.video_map_path:
        return gaps, []
    try:
        confirmed = fully_mapped_gtins(load_video_map(Path(media.video_map_path)), languages)
    except VideoMapError:
        return gaps, []  # the video-map section reports this; do not fail twice over it
    # Products already held by E23 are not listed again here: E23 runs first, so naming the same
    # SKU twice would imply two independent blocks where the first already stops the run.
    held = [p.gtin14 for p in scoped if p.gtin14 not in gaps and p.gtin14 not in confirmed]
    return gaps, sorted(held)


def _load_observations(path: Path) -> list[str]:
    """Read the in-session review notes (``observations.json``); absent yields ``[]``.

    Contract: ``{"notes": ["...", "..."]}`` — free-text flags the assistant wrote while
    reviewing a run, so they land in the report as well as the chat. Non-string entries are
    coerced; a malformed file yields no notes rather than failing the report.
    """
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [str(note) for note in data.get("notes", [])]
    except (json.JSONDecodeError, AttributeError):
        return []


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render the consolidated data-quality report.")
    parser.add_argument(
        "client_id",
        nargs="?",
        help="Key under clients: in clients.yml (optional when only one client is defined)",
    )
    parser.add_argument(
        "--out", help="output path (default output/{client_id}/data-quality-report.md)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    args = _parse_args(argv)
    try:
        client_id = resolve_client_id(args.client_id)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return _EXIT_CONFIG_ERROR

    data_dir = Path("output") / client_id / "data"
    if not data_dir.is_dir():
        print(
            f"config error: {data_dir} does not exist — run the pipeline "
            f"(parse_export / run_plan) for {client_id} first",
            file=sys.stderr,
        )
        return _EXIT_CONFIG_ERROR

    issues: dict[str, list[SourceIssue]] = {}
    freshness: dict[str, str] = {}
    for filename, key in _ISSUE_FILES.items():
        path = data_dir / filename
        issues[key] = _load_issues(path)
        freshness[key] = _mtime(path)

    products = _load_products(data_dir / "products.json")
    mandatory_gaps, video_held = _publish_blocks(client_id, products)

    markdown = render_quality_report(
        client_id=client_id,
        source_issues=issues["source"],
        generated_issues=issues["generated"],
        video_map_issues=issues["video_map"],
        category_issues=issues["category"],
        products=products,
        snapshot=datetime.now(UTC).strftime("%Y-%m-%d"),
        freshness=freshness,
        observations=_load_observations(data_dir / "observations.json"),
        mandatory_gaps=mandatory_gaps,
        video_held=video_held,
    )

    out = Path(args.out) if args.out else Path("output") / client_id / "data-quality-report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")
    total = sum(len(v) for v in issues.values())
    print(f"Wrote {out} ({total} finding(s) across {len(_ISSUE_FILES)} sources)", file=sys.stderr)
    return _EXIT_OK


if __name__ == "__main__":
    load_env()
    raise SystemExit(main())
