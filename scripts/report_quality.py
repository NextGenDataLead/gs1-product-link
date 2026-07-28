"""Render the per-step issue reports into one human-readable data-quality worklist.

Usage:
    python -m scripts.report_quality CLIENT_ID [--out PATH]

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
    parser.add_argument("client_id")
    parser.add_argument(
        "--out", help="output path (default output/{client_id}/data-quality-report.md)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    data_dir = Path("output") / args.client_id / "data"
    if not data_dir.is_dir():
        print(
            f"config error: {data_dir} does not exist — run the pipeline "
            f"(parse_export / run_plan) for {args.client_id} first",
            file=sys.stderr,
        )
        return _EXIT_CONFIG_ERROR

    issues: dict[str, list[SourceIssue]] = {}
    freshness: dict[str, str] = {}
    for filename, key in _ISSUE_FILES.items():
        path = data_dir / filename
        issues[key] = _load_issues(path)
        freshness[key] = _mtime(path)

    markdown = render_quality_report(
        client_id=args.client_id,
        source_issues=issues["source"],
        generated_issues=issues["generated"],
        video_map_issues=issues["video_map"],
        category_issues=issues["category"],
        products=_load_products(data_dir / "products.json"),
        snapshot=datetime.now(UTC).strftime("%Y-%m-%d"),
        freshness=freshness,
        observations=_load_observations(data_dir / "observations.json"),
    )

    out = Path(args.out) if args.out else Path("output") / args.client_id / "data-quality-report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")
    total = sum(len(v) for v in issues.values())
    print(f"Wrote {out} ({total} finding(s) across {len(_ISSUE_FILES)} sources)", file=sys.stderr)
    return _EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
