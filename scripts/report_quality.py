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
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from lib import video_signoff, video_signoff_archive
from lib.config import ClientConfig, get_client, resolve_client_id
from lib.env import load_env
from lib.errors import (
    ConfigError,
    ExportParseError,
    MissingCredentialError,
    StateError,
    VideoMapError,
    WordPressAPIError,
)
from lib.live_inventory import inventory_lines, live_products
from lib.mandatory import MandatoryGap, missing_mandatory
from lib.media_video import canon_gtin, check_video_map, files_by_language, load_video_map
from lib.preflight import in_scope, load_video_status
from lib.quality_report import MatrixInput, render_quality_report
from lib.quality_report_video import SignoffReview, VideoReport
from lib.records import ProductRecord, SourceIssue
from lib.state import peek_state
from lib.video_status import HAS_VIDEO
from lib.wp_client import WordPressClient

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


def _generated_at() -> str:
    """When this report was written, in the reader's own clock: ``2026-08-13 22:02 CEST``.

    Local time rather than UTC so it matches what the file browser shows beside the file — the
    two disagreeing by an hour or two is exactly the confusion this line exists to remove. The
    zone is named so the timestamp is still unambiguous when the file is sent to someone else.
    """
    now = datetime.now(UTC).astimezone()
    return f"{now:%Y-%m-%d %H:%M} {now:%Z}".strip()


def _scope(
    cfg: ClientConfig, products: dict[str, ProductRecord], live: frozenset[str] | None
) -> list[ProductRecord]:
    """The products this report is about: the batch in progress, or — with ``--live`` — every
    product the ledger records as live. One function, so no section can use the other scope."""
    if live is None:
        return in_scope(cfg, list(products.values()))
    return [product for product in products.values() if product.gtin14 in live]


def _publish_blocks(
    client_id: str, products: dict[str, ProductRecord], live: frozenset[str] | None = None
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

    scoped = _scope(cfg, products, live)
    languages = cfg.wordpress.languages
    gaps = {
        product.gtin14: found
        for product in scoped
        if (found := missing_mandatory(product, cfg.export.all_sources, languages))
    }

    if cfg.media is None or not cfg.media.restrict_to_mapped_gtins:
        return gaps, []
    status = load_video_status(cfg, scoped)
    if status is None:
        return gaps, []  # the video-map section reports why; do not fail twice over it
    # Products already held by E23 are not listed again here: E23 runs first, so naming the same
    # SKU twice would imply two independent blocks where the first already stops the run.
    return gaps, sorted(p.gtin for p in status.held if p.gtin not in gaps)


def _video_report(
    client_id: str, products: dict[str, ProductRecord], live: frozenset[str] | None = None
) -> VideoReport | None:
    """§1's inputs: the selection joined to the video mapping. ``None`` with no readable mapping.

    Follows :func:`_publish_blocks`' rule — every failure is an absent section, never a traceback;
    ``doctor`` is where a broken config is reported.
    """
    try:
        cfg = get_client(client_id)
    except (ConfigError, ExportParseError):
        return None
    status = load_video_status(cfg, _scope(cfg, products, live))
    if status is None:
        return None
    review = _signoff_review(cfg, products)
    if isinstance(review, str):
        return VideoReport(status=status, signoff_absent=review)
    return VideoReport(status=status, signoff=review)


def _signoff_review(cfg: ClientConfig, products: dict[str, ProductRecord]) -> SignoffReview | str:
    """The client's newest sign-off sheet re-planned against the mapping as it is now — or why not.

    Re-planned on every render, so §1d is never a record of what the sheet *would have* changed
    when it arrived: a fill applied since reads as "already match", a row edited by hand since
    reads as a conflict, and the counts correct themselves.

    **It refuses rather than guess** when the sheet's headings no longer match the ones the column
    choice was made against: somebody edited the sheet in place and a column moved, and planning
    against the recorded positions would read one column as another — a filename as a barcode.
    """
    media = cfg.media
    assert media is not None and media.video_map_path  # load_video_status returned a status
    found = video_signoff_archive.newest(Path(media.video_map_path))
    if isinstance(found, video_signoff_archive.Absent):
        return video_signoff_archive.describe(found)
    sheet, note = found
    try:
        grid = video_signoff.read_sheet(sheet)
        vmap = load_video_map(Path(media.video_map_path))
    except (OSError, zipfile.BadZipFile, ET.ParseError, VideoMapError) as exc:
        return f"The newest sign-off sheet ({sheet.name}) could not be read: {exc}"
    if grid is None:
        return f"The newest sign-off sheet ({sheet.name}) has no row that could be a header."
    for name, index in note.columns.items():
        now = grid.header[index] if index < len(grid.header) else ""
        if now != note.headers.get(name, ""):
            return (
                f"The newest sign-off sheet ({sheet.name}) has changed since its columns were "
                f'chosen: the {name} column was headed "{note.headers.get(name, "")}" and is '
                f'now "{now}". Choose the columns again where the sheet is uploaded.'
            )
    plan = video_signoff.plan(
        grid,
        vmap,
        exported={product.gtin14 for product in products.values()},
        languages=cfg.wordpress.languages,
        where=note.columns,
    )
    return SignoffReview(sheet=sheet.name, given_name=note.given_name, at=note.at[:10], plan=plan)


def _languages(client_id: str, issues: dict[str, list[SourceIssue]]) -> list[str]:
    """The client's configured site languages — the column set for §2 and §4, and §0's marks.

    Falls back to the languages the findings themselves name when the config cannot be read, so a
    report still renders (``doctor`` reports config problems; blanking the document helps nobody).
    Sorted in that case, because there is no configured order to honour.
    """
    try:
        return get_client(client_id).wordpress.languages
    except (ConfigError, ExportParseError):
        found = (
            i.field.rsplit(".", 1)[-1] for group in issues.values() for i in group if "." in i.field
        )
        return sorted({lang for lang in found if lang})


def _scoped_issues(
    client_id: str,
    products: dict[str, ProductRecord],
    issues: list[SourceIssue],
    live: frozenset[str] | None = None,
) -> list[SourceIssue]:
    """Drop findings about GTINs this run will not touch.

    The whole report describes one run, and every other section was already scoped: §0's matrix
    and §1's holds are computed over :func:`lib.preflight.in_scope`, and §2/§4 come from the
    generator, which only ever ran for in-scope units. §3 was the exception — its rows came
    straight from ``source_issues.json``, which ``parse_export`` writes over the entire workbook.
    Four of its eleven GTINs were outside the run and therefore appeared nowhere else in the
    document, which is exactly how a reader finds out: by looking for one in §0 and not finding it.

    Applied here rather than in the renderer because scope needs the client config, and the
    renderer is pure. A finding is not lost — it returns the moment its GTIN joins the process
    list.

    Findings with no GTIN (an unmapped video file) are kept: they are about the *input*, not
    about a product.
    """
    try:
        cfg = get_client(client_id)
    except (ConfigError, ExportParseError):
        return issues  # doctor reports config problems; do not also blank the report
    scope = {p.gtin14 for p in _scope(cfg, products, live)}
    return [i for i in issues if not i.gtin or canon_gtin(i.gtin) in scope]


def _matrix_input(
    client_id: str, products: dict[str, ProductRecord], live: frozenset[str] | None = None
) -> MatrixInput | None:
    """Gather the §0 matrix inputs, or ``None`` when there is nothing to tabulate.

    Scoped to the process list, like every other per-SKU section: a coverage table over the whole
    catalogue would be mostly rows nobody asked about, which is the failure the scope check exists
    to prevent. Config problems yield ``None`` rather than an exception — ``doctor`` reports those,
    and a report that refuses to render because of one helps nobody.
    """
    try:
        cfg = get_client(client_id)
    except (ConfigError, ExportParseError):
        return None
    if not cfg.export.gdsn_map:
        return None

    languages = cfg.wordpress.languages
    scoped = _scope(cfg, products, live)
    # ● means the page gets a video in that language — what ``VideoMap.resolve`` attaches, not
    # merely "some row names this GTIN". A GTIN confirmed to two files is therefore ○: the page
    # gets neither. With no readable mapping every cell is ○, which is what "not confirmed" means.
    status = load_video_status(cfg, scoped)
    confirmed: dict[str, set[str]] = {
        lang: {
            p.gtin
            for p in (status.products if status else ())
            if p.by_language.get(lang) == HAS_VIDEO
        }
        for lang in languages
    }
    return MatrixInput(
        products=scoped,
        gdsn_map=cfg.export.gdsn_map,
        gdsn_extras=cfg.export.gdsn_extras,
        video_confirmed=confirmed,
    )


def _live_video_issues(client_id: str) -> tuple[list[SourceIssue], str] | None:
    """The mapping's gaps as they stand now, dated by the mapping itself — or ``None``.

    ``video_map_issues.json`` is written only by ``build_video_map --check``, which nothing on the
    Data screen runs; this report only ever *read* it. On the pilot it was seven weeks old and said
    118 files had no barcode while the mapping itself had 18 — in the same document whose other
    sections are recomputed on every render. So the gaps are recomputed here with the same
    :func:`lib.media_video.check_video_map` that writes the file, and the date shown beside them is
    the mapping's own last change rather than the last time somebody ran a command.

    ``None`` — read the file as before — when there is no ``media`` block, no mapping path, or a
    mapping that will not load. Not a convenience: a client that attaches no videos has no mapping
    to recompute from, and the file is then the only record there is.
    """
    try:
        cfg = get_client(client_id)
    except (ConfigError, ExportParseError):
        return None
    media = cfg.media
    if media is None or not media.video_map_path:
        return None
    path = Path(media.video_map_path)
    try:
        vmap = load_video_map(path)
    except VideoMapError:
        return None
    return check_video_map(vmap, files_by_language(media.video_folders)), _mtime(path)


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
        "--out",
        help="output path (default output/{client_id}/data-quality-report.md, or "
        "live-data-quality-report.md with --live)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="report on every product this tool has put live (the ledger, checked against the "
        "site) instead of the batch in progress",
    )
    return parser.parse_args(argv)


def _live_scope(client_id: str, data_dir: Path) -> tuple[frozenset[str], list[str]]:
    """``--live``: every product the ledger records as live, and the section that lists them.

    The ledger is read with :func:`lib.state.peek_state`, which never quarantines a corrupt file —
    this is a report, and a report must not change what the next run does. The site is then asked
    which of those pages it lists; if it cannot be asked, every page says "not checked".

    Raises:
        ConfigError: The client config will not load.
        StateError: The ledger will not read.
    """
    cfg = get_client(client_id)
    live = live_products(peek_state(client_id))
    on_site, note = _listed_on_site(cfg)
    lines = inventory_lines(live, list(cfg.wordpress.languages), on_site=on_site, site_note=note)
    written = _generated_for(data_dir)
    covered = sorted(p.gtin for p in live if canon_gtin(p.gtin) in written)
    lines += [
        "Sections 3 and 5 come from the last text generation, which wrote for "
        f"{len(covered)} of these {len(live)} product(s)"
        + (f" ({', '.join(f'`{g}`' for g in covered)})" if covered else "")
        + ". For the rest there is no record of which claims were inferred or which values were "
        "translated — not that there were none.",
        "",
    ]
    return frozenset(canon_gtin(p.gtin) for p in live), lines


def _listed_on_site(cfg: ClientConfig) -> tuple[set[tuple[str, str]] | None, str]:
    """``(gtin14, language)`` for every published tool page the site lists — or ``None`` and why.

    A listing per language (an unscoped one answers with the default language only), read-only.
    Any failure is reported in the section rather than failing the report: the data findings are
    still worth having when the site cannot be reached.
    """
    found: set[tuple[str, str]] = set()
    try:
        with WordPressClient(cfg.wordpress) as client:
            for language in cfg.wordpress.languages:
                for page in client.list_pages_with_gtin(cfg.wordpress.post_type, language):
                    meta = page.get("meta")
                    gtin = str(meta.get("gtin", "")) if isinstance(meta, dict) else ""
                    if gtin and page.get("status", "publish") == "publish":
                        found.add((canon_gtin(gtin), language))
    except (ConfigError, MissingCredentialError, WordPressAPIError) as exc:
        return None, str(exc)
    return found, ""


def _generated_for(data_dir: Path) -> set[str]:
    """The barcodes the last text generation wrote for, from ``generation_results.json``."""
    try:
        data = json.loads((data_dir / "generation_results.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {
        canon_gtin(str(item["gtin"]))
        for item in data.get("results", [])
        if isinstance(item, dict) and item.get("gtin")
    }


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
    if (live := _live_video_issues(client_id)) is not None:
        issues["video_map"], freshness["video_map"] = live

    products = _load_products(data_dir / "products.json")
    live: frozenset[str] | None = None
    preface: list[str] = []
    if args.live:
        try:
            live, preface = _live_scope(client_id, data_dir)
        except (ConfigError, StateError) as exc:
            print(f"config error: {exc}", file=sys.stderr)
            return _EXIT_CONFIG_ERROR
    issues = {
        key: _scoped_issues(client_id, products, found, live) for key, found in issues.items()
    }
    mandatory_gaps, video_held = _publish_blocks(client_id, products, live)
    matrix = _matrix_input(client_id, products, live)
    video = _video_report(client_id, products, live)

    markdown = render_quality_report(
        client_id=client_id,
        languages=_languages(client_id, issues),
        source_issues=issues["source"],
        generated_issues=issues["generated"],
        video_map_issues=issues["video_map"],
        category_issues=issues["category"],
        products=products,
        snapshot=_generated_at(),
        freshness=freshness,
        observations=_load_observations(data_dir / "observations.json"),
        mandatory_gaps=mandatory_gaps,
        video_held=video_held,
        matrix=matrix,
        video=video,
        preface=preface,
        live=args.live,
    )

    name = "live-data-quality-report.md" if args.live else "data-quality-report.md"
    out = Path(args.out) if args.out else Path("output") / client_id / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")
    total = sum(len(v) for v in issues.values())
    print(f"Wrote {out} ({total} finding(s) across {len(_ISSUE_FILES)} sources)", file=sys.stderr)
    return _EXIT_OK


if __name__ == "__main__":
    load_env()
    raise SystemExit(main())
