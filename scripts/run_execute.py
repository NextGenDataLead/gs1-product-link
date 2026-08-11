"""Execute a confirmed run plan against WordPress and GS1 (IMPLEMENTATION_SPEC §8.3).

Usage:
    python -m scripts.run_execute CLIENT_ID (--plan PATH | --confirmed PATH)
                                 [--only {pages,links}] [--dry-run] [--revive]
                                 [--i-understand-production]

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
``output/{client_id}/runs/{ts}.jsonl`` **as it completes**, regardless of success, and
successful rows update ``output/{client_id}/state.json``. Writing the log incrementally
means a run that dies part-way still leaves a record of what it managed to do, and gives a
parent process something to tail — this script has no other progress channel. The path is
printed to stderr at the start of the run as well as at the end. The run is idempotent
(§6.5) and resumable: re-running the same confirmed plan yields the same final state.

``--only`` runs one leg instead of both. ``pages`` writes the WordPress pages and links
them as translations, and stops — nothing permanent happens, so the run is reversible by
editing or deleting the pages. ``links`` writes only the Digital Link records and the QR,
pointing them at pages that already exist. Omitting the flag does both, which is the
behaviour every existing invocation gets. Operators do not type this: the ``/gs1-pages``,
``/gs1-links`` and ``/gs1-publish`` skills supply it after their intent gate, exactly as
``--i-understand-production`` is supplied after the environment gate.

**``--only links`` verifies before it writes.** Its targets do not come from a page this
run just created, so each one is resolved (from ``state.json``, else a slug lookup, else
the plan row's ``target_url``) and must serve 2xx/3xx before the resolver is touched. A
GTIN with any unverifiable target gets no GS1 write at all. This lives here rather than in
the skill because a GS1 record can never be deleted: a permanent QR target pointing at a
404 is not recoverable, and prose in a skill can be skipped.

``--dry-run`` (§5.4 Level B) walks the plan and logs the intended WordPress/GS1
mutations without performing them — no HTTP writes, no QR files, no state update. It builds
no clients and so needs no credentials, which is also why a dry-run ``--only links`` lists
its intended targets without verifying them; the refusal above happens at execute time.

**Production guard.** A real run (not ``--dry-run``) against a client whose ``gs1.environment``
is ``production`` is refused unless ``--i-understand-production`` is passed. This makes the live
write a deliberate, explicit act rather than a bare ``--plan`` invocation — the interactive
review gates otherwise live only in the ``flow-orchestrator`` skill, not in this script. The
pilot allowlist, HELD-drop, and E21 still apply on top.

Exit codes:
    0  every confirmed row succeeded
    1  one or more rows errored (state still saved for the rows that succeeded)
    2  config/setup error (bad client id, unreadable/invalid plan, missing GS1 creds),
       or a production run without --i-understand-production
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, NamedTuple

from pydantic import ValidationError

from lib.acf import build_acf_payload
from lib.config import ClientConfig, GS1LinkConfig, MediaConfig, get_client
from lib.env import load_env
from lib.errors import ConfigError, StateError, VideoMapError, WordPressAPIError
from lib.gs1_dl_client import GS1Config as ResolvedGS1Config
from lib.gs1_dl_client import GS1DigitalLinkClient, LinkInput
from lib.media import convert_image_for_web
from lib.media_video import canon_gtin, fully_mapped_gtins, load_video_map, prepare_video
from lib.qr import render_qr
from lib.records import (
    ConfirmedPlan,
    Plan,
    PlanClassification,
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

#: Fallback resolver link type when a client defines no ``gs1_links`` (§2.4). A GS1 Web
#: Vocabulary CURIE: the API stores ``linkType`` unvalidated, so a bare ``"pip"`` is
#: accepted with a 200 and read back with a null ``linkTypeTitle`` — i.e. unrecognised.
_DEFAULT_LINK_TYPE = "gs1:pip"
#: Run-log timestamp format (UTC), shared with the JSONL filename.
_TS_FORMAT = "%Y%m%dT%H%M%SZ"


class _Mode(StrEnum):
    """Which leg of the publish a run performs (``--only``).

    ``BOTH`` is not a spelling of the flag — it is what omitting the flag means, so every
    invocation written before ``--only`` existed keeps its behaviour untouched.
    """

    PAGES = "pages"
    LINKS = "links"
    BOTH = "both"

    @property
    def writes_pages(self) -> bool:
        """Whether this mode renders and upserts WordPress pages."""
        return self is not _Mode.LINKS

    @property
    def writes_links(self) -> bool:
        """Whether this mode writes GS1 resolver records (and the QR that encodes them)."""
        return self is not _Mode.PAGES


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
    """One language's live page: what the per-GTIN phase needs to know about it.

    ``page_id`` is ``None`` only on the ``--only links`` path, for a page this tool does not
    manage and could not find by slug — the URL is then the plan row's ``target_url``. The
    resolver can still point at it (once verified), but nothing that needs an id may run,
    and no state is recorded for it.
    """

    page_id: int | None
    url: str
    title: str
    featured_media_id: int | None = None


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
            known[language] = _Page(
                entry.wp_page_id, entry.wp_url, entry.title or "", entry.wp_featured_media_id
            )
    return known


def _find_page(cfg: ClientConfig, row: PlanRow, wp: WordPressClient) -> _Page:
    """Locate a page for a row this run is not writing: by slug, else by its planned URL.

    ``--only links`` exists to point resolver records at pages that already exist, so for a
    language with no state entry the page was made by somebody other than this tool. The id
    is looked up by slug (a read, no write); when even that misses — a site whose pages do
    not follow ``slug_pattern``, which is the ordinary case for pre-existing content — the
    row's planned ``target_url`` is used with no id at all.

    Neither branch is trusted on its own: :func:`_verify_targets` still has to see the URL
    serve before anything permanent is written.
    """
    try:
        found = wp.find_by_slug(cfg.wordpress.post_type, row.slug, row.language)
    except WordPressAPIError as exc:
        _log.warning(
            "slug lookup for %s/%s failed (%r); falling back to the planned URL",
            row.gtin,
            row.language,
            exc,
        )
        found = None
    if found is not None:
        return _Page(found["id"], found["link"], row.title)
    _log.warning(
        "gtin %s (%s): no state entry and no %s with slug %r — the resolver will point at "
        "the planned URL %s, and no state will be recorded for it",
        row.gtin,
        row.language,
        cfg.wordpress.post_type,
        row.slug,
        row.target_url,
    )
    return _Page(None, row.target_url, row.title)


def _pages_for_links(  # noqa: PLR0913 — three sources of pages, plus what to look the rest up with
    cfg: ClientConfig,
    gtin: str,
    rows: list[PlanRow],
    fresh: dict[str, _Page],
    wp: WordPressClient,
    state: State,
) -> dict[str, _Page]:
    """Every language's page this GTIN's resolver link set must span.

    Starts from :func:`_known_pages` — this run's pages plus state's — for the reason that
    function exists: the GS1 link array **replaces**, so a language left out is deleted from
    the record. Any confirmed language still unaccounted for is then located live, which is
    the ``--only links`` case where state has never heard of the product.
    """
    pages = _known_pages(gtin, fresh, state)
    for row in rows:
        if row.language not in pages:
            pages[row.language] = _find_page(cfg, row, wp)
    return pages


def _verify_targets(wp: WordPressClient, pages: dict[str, _Page], verified: set[str]) -> None:
    """Raise unless every target not already verified this run serves 2xx/3xx.

    A GS1 record can never be deleted, so a resolver target pointing at a 404 is permanent —
    which makes this the one precondition that cannot live in skill prose, because prose can
    be skipped and a manual invocation would then burn a real GTIN.

    ``verified`` is the set of URLs :func:`_upsert_row` already checked immediately after
    writing them, so on the both-flow this covers only the languages :func:`_known_pages`
    rebuilt from state — which until now were used unverified, on nothing but the age of the
    state file. On ``--only links`` it covers all of them.

    ``verify_url`` returns ``True`` or raises; both outcomes are handled, since a caller that
    trusted only the return value would sail straight past the raise.
    """
    for language, page in sorted(pages.items()):
        if page.url in verified:
            continue
        try:
            ok = wp.verify_url(page.url)
        except WordPressAPIError as exc:
            raise RuntimeError(
                f"target URL for language {language} does not serve: {page.url} ({exc!r}) — "
                f"refusing to point a permanent GS1 record at it"
            ) from exc
        if not ok:
            raise RuntimeError(
                f"target URL for language {language} does not serve: {page.url} — "
                f"refusing to point a permanent GS1 record at it"
            )


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


# --- Media (Phase 9.5) -------------------------------------------------------


class _RowMedia(NamedTuple):
    """The media a row contributes to its page: the hero image and this language's video."""

    featured_media_id: int | None
    image_acf_value: int | str | None
    video_media_id: int | None


_NO_MEDIA = _RowMedia(None, None, None)


def _row_media(cfg: ClientConfig, row: PlanRow, wp: WordPressClient) -> _RowMedia:
    """Resolve the hero image and video for a row, uploading each to WP media.

    Returns all-``None`` when the client has no ``media`` config, so clients without media are
    untouched. Every failure (a 404 image, an undecodable file, a missing video, an ffmpeg
    error) degrades to ``None`` (edge E7): media must never stop the page publishing. Uploads
    are idempotent — ``upload_media`` dedupes by content hash and the converters are
    deterministic — so re-runs reuse attachments rather than duplicating them.
    """
    media = cfg.media
    if media is None:
        return _NO_MEDIA
    hero_id = _hero_media_id(cfg.client_id, media, row, wp)
    image_value = _image_acf_value(media, hero_id, wp)
    video_id = _video_media_id(cfg.client_id, media, row, wp)
    return _RowMedia(hero_id, image_value, video_id)


def _hero_media_id(
    client_id: str, media: MediaConfig, row: PlanRow, wp: WordPressClient
) -> int | None:
    """Download the export image, convert it to web JPEG, and upload it; ``None`` on any failure."""
    url = row.product.image_url
    if not url:
        return None
    data = wp.download_image(url)  # bytes | None (E7)
    if data is None:
        return None
    dest = Path("output") / client_id / "media" / "images" / f"{row.gtin}.jpg"
    jpeg = convert_image_for_web(
        data, dest, max_dim=media.image_max_dim, quality=media.image_quality
    )
    if jpeg is None:
        return None
    return wp.upload_media(jpeg, title=f"{row.title} ({row.gtin})")


def _image_acf_value(
    media: MediaConfig, hero_id: int | None, wp: WordPressClient
) -> int | str | None:
    """The value written to the image ACF fields: an attachment id or its URL, per config."""
    if hero_id is None:
        return None
    if media.image_write_shape == "url":
        return wp.media_source_url(hero_id)
    return hero_id


def _video_media_id(
    client_id: str, media: MediaConfig, row: PlanRow, wp: WordPressClient
) -> int | None:
    """Resolve, prepare (transcode), and upload this language's video; ``None`` if none matches."""
    folder = media.video_folders.get(row.language)
    if not folder or not media.video_map_path:
        return None
    try:
        vmap = load_video_map(Path(media.video_map_path))
    except VideoMapError as exc:
        _log.warning("could not load video map %s: %s (skipping video)", media.video_map_path, exc)
        return None
    filename = vmap.resolve(row.gtin, row.language)
    if not filename:
        return None
    prepared = prepare_video(
        Path(folder) / filename,
        Path("output") / client_id / "media" / "videos",
        transcode=media.video_transcode,
        ffmpeg_bin=media.ffmpeg_bin,
    )
    if prepared is None:
        return None
    return wp.upload_media(prepared, title=f"{row.title} video {row.language} ({row.gtin})")


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
    # Media (Phase 9.5): the hero image and this language's video are uploaded here and injected
    # into the ACF dict imperatively — their attachment ids are only known after upload, so they
    # cannot ride the static acf_map. They join the second (_write_acf) call, never the ?lang
    # create; featured_media is a core field on the create/update body.
    media = _row_media(cfg, row, wp)
    if cfg.media is not None:
        if media.image_acf_value is not None:
            acf[cfg.media.header_image_field] = media.image_acf_value
            acf[cfg.media.regular_image_field] = media.image_acf_value
        if media.video_media_id is not None:
            acf[cfg.media.video_file_field] = media.video_media_id
    prior = state.entries.get(row.gtin, {}).get(row.language)
    page = wp.upsert_page(
        post_type=cfg.wordpress.post_type,
        slug=row.slug,
        title=row.title,
        content=html,
        language=row.language,
        featured_media=media.featured_media_id,
        meta={"gtin": row.gtin},
        existing_id=prior.wp_page_id if prior else None,
        acf=acf,
    )
    page_url = page["link"]
    outcome.wp_page_id = page["id"]
    outcome.wp_url = page_url
    if not wp.verify_url(page_url):
        raise RuntimeError(f"WordPress URL {page_url} did not return 200")
    return _Page(page["id"], page_url, row.title, media.featured_media_id)


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


def _finish_pages(  # noqa: PLR0913 — one collaborator per step; bundling them only hides them
    gtin: str,
    rows: list[PlanRow],
    fresh: dict[str, _Page],
    wp: WordPressClient,
    state: State,
    ts: datetime,
) -> dict[str, StateEntry]:
    """Phase 2, WordPress half: link the languages as translations; build their state.

    Returns the state entries rather than writing them — see :func:`_commit_state` for why
    a GTIN's state has to land all at once or not at all.

    ``gs1_link_set_hash`` carries the prior value, or ``""`` when there is none. That empty
    string is the record of "page published, resolver link never written": ``lib.state``
    reads it and reports the row CHANGED, so a ``--only pages`` run followed by ``--only
    links`` still has something to plan. Preserving a *prior* hash rather than blanking it
    matters just as much — re-running pages over a fully published product must not make it
    look like its resolver link vanished.
    """
    pages = _known_pages(gtin, fresh, state)
    rebuilt = sorted(set(pages) - set(fresh))
    if rebuilt:
        _log.warning(
            "gtin %s: language(s) %s were not written this run; their translation ids come "
            "from state, not from a page verified just now",
            gtin,
            rebuilt,
        )
    wp.link_translations(
        {lang: page.page_id for lang, page in pages.items() if page.page_id is not None}
    )
    entries: dict[str, StateEntry] = {}
    for row in rows:
        prior = state.entries.get(gtin, {}).get(row.language)
        page = fresh[row.language]
        if page.page_id is None:  # every page in ``fresh`` came from an upsert that returned one
            raise RuntimeError(f"page for {gtin}/{row.language} has no id after its upsert")
        entries[row.language] = StateEntry(
            wp_page_id=page.page_id,
            wp_url=page.url,
            wp_featured_media_id=page.featured_media_id,
            content_hash=row.content_hash,
            gs1_link_set_hash=prior.gs1_link_set_hash if prior else "",
            last_run=ts,
            title=row.title,  # the next run diffs against this (§10.6.2)
        )
    return entries


def _finish_links(  # noqa: PLR0913 — one collaborator per step; bundling them only hides them
    cfg: ClientConfig,
    gtin: str,
    rows: list[PlanRow],
    pages: dict[str, _Page],
    gs1: GS1DigitalLinkClient,
    outcomes: dict[str, RunOutcome],
) -> str:
    """Phase 2, GS1 half: one resolver write for the whole GTIN, then its QR.

    Returns the link-set hash for :func:`_commit_state`. One write carrying every language,
    never one per language: CreateOrUpdate replaces the ``links`` array wholesale.
    """
    links = _build_links(cfg, rows[0].product, pages)
    gs1.safe_upsert(
        gtin=gtin,
        item_description=_item_description(cfg, rows, pages),
        links=links,
        is_enabled=True,
        overwrite=True,  # the plan is operator-confirmed; re-runs update in place (§6.5)
    )
    qr_paths = [str(p) for p in _render_qr_for(cfg, rows[0])]
    for row in rows:
        outcomes[row.language].gs1_set = True
        outcomes[row.language].qr_paths = qr_paths
    return _link_set_hash(links)


def _commit_state(  # noqa: PLR0913 — the merge needs both legs' output plus where to put it
    state: State,
    gtin: str,
    rows: list[PlanRow],
    page_entries: dict[str, StateEntry],
    link_hash: str | None,
    ts: datetime,
) -> None:
    """Persist one GTIN's state, once every leg of this run has succeeded.

    Written here rather than inside each leg so a GTIN stays all-or-nothing. A resolver
    write that fails after the pages were upserted must not leave the page half behind: the
    next run would read a fresh ``content_hash``, and only the empty ``gs1_link_set_hash``
    would keep it from classifying UNCHANGED and never retrying the link.

    On ``--only links`` there are no page entries, so each row updates the entry already in
    state. A row with neither gets **no state at all** — that is the page this tool does not
    manage (:func:`_find_page`), and inventing a ``content_hash`` for it would claim we
    published content we never wrote, which the next run would believe.
    """
    for row in rows:
        entry = page_entries.get(row.language) or state.entries.get(gtin, {}).get(row.language)
        if entry is None:
            _log.warning(
                "gtin %s (%s): resolver link written for a page this tool does not manage; "
                "recording no state for it",
                gtin,
                row.language,
            )
            continue
        if link_hash is not None:
            # per-GTIN: every language shares the one link set
            entry = entry.model_copy(update={"gs1_link_set_hash": link_hash, "last_run": ts})
        state.entries.setdefault(gtin, {})[row.language] = entry


def _execute_gtin(  # noqa: PLR0913 — one collaborator per step; bundling them only hides them
    cfg: ClientConfig,
    gtin: str,
    rows: list[PlanRow],
    wp: WordPressClient,
    gs1: GS1DigitalLinkClient,
    engine: TemplateEngine,
    state: State,
    ts: datetime,
    mode: _Mode,
) -> list[RunOutcome]:
    """Run every confirmed row of one GTIN, then its per-product writes. Never raises."""
    outcomes = {
        row.language: RunOutcome(gtin=gtin, language=row.language, ts=ts, status="pending")
        for row in rows
    }
    fresh: dict[str, _Page] = {}
    if mode.writes_pages:
        for row in rows:
            try:
                fresh[row.language] = _upsert_row(
                    cfg, row, wp, engine, state, outcomes[row.language]
                )
            except Exception as exc:  # noqa: BLE001 — one bad row must not abort the run
                outcomes[row.language].status = "error"
                outcomes[row.language].error = repr(exc)
                _log.error("row %s/%s failed: %r", gtin, row.language, exc)
        if len(fresh) != len(rows):
            _block_gtin(gtin, rows, outcomes)
            return [outcomes[row.language] for row in rows]

    try:
        page_entries = _finish_pages(gtin, rows, fresh, wp, state, ts) if mode.writes_pages else {}
        link_hash = None
        if mode.writes_links:
            pages = _pages_for_links(cfg, gtin, rows, fresh, wp, state)
            _verify_targets(wp, pages, {page.url for page in fresh.values()})
            link_hash = _finish_links(cfg, gtin, rows, pages, gs1, outcomes)
        _commit_state(state, gtin, rows, page_entries, link_hash, ts)
    except Exception as exc:  # noqa: BLE001 — one bad GTIN must not abort the run
        for row in rows:
            outcomes[row.language].status = "error"
            outcomes[row.language].error = repr(exc)
        _log.error("gtin %s failed its per-product writes: %r", gtin, exc)
        return [outcomes[row.language] for row in rows]

    for row in rows:
        outcomes[row.language].status = "ok"
    return [outcomes[row.language] for row in rows]


def _execute(  # noqa: PLR0913 — one collaborator per step; bundling them only hides them
    cfg: ClientConfig,
    rows: list[PlanRow],
    wp: WordPressClient,
    gs1: GS1DigitalLinkClient,
    engine: TemplateEngine,
    state: State,
    ts: datetime,
    mode: _Mode,
) -> Iterator[RunOutcome]:
    """Execute the confirmed rows grouped by GTIN, yielding outcomes as each GTIN finishes.

    A generator rather than a list so the caller can write each outcome to the run log the
    moment it is final — and *final* means when its GTIN finishes, not when its row does: a
    row's outcome stays mutable until then, because a later language failing blocks the whole
    GTIN and rewrites every one of its rows to ``error``.

    Grouped with a dict rather than by walking runs of adjacent rows: rows for one GTIN
    happen to be adjacent today only because ``diff_against_state`` builds them in a nested
    loop, and :class:`~lib.records.Plan` promises no such ordering. Outcomes therefore come
    out in completion order, which for any GTIN-grouped plan — every plan this tool writes —
    is also plan order.
    """
    by_gtin: dict[str, list[PlanRow]] = {}
    for row in rows:
        by_gtin.setdefault(row.gtin, []).append(row)

    for gtin, gtin_rows in by_gtin.items():
        yield from _execute_gtin(cfg, gtin, gtin_rows, wp, gs1, engine, state, ts, mode)


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
    cfg: ClientConfig, row: PlanRow, engine: TemplateEngine, ts: datetime, mode: _Mode
) -> RunOutcome:
    """Render the template and log the intended mutations without performing them (§5.4)."""
    outcome = RunOutcome(gtin=row.gtin, language=row.language, ts=ts, status="dry-run")
    try:
        if mode.writes_pages:
            engine.render(row.product, row.language, _client_meta(cfg))
        # One line per row, but the GS1 write is per GTIN: a GTIN with two confirmed rows
        # gets one resolver write carrying both languages' links, not one write per line.
        _log.info("[dry-run] %s/%s: %s", row.gtin, row.language, _preview_text(cfg, row, mode))
    except Exception as exc:  # noqa: BLE001 — surface template errors as a failed preview row
        outcome.status = "error"
        outcome.error = repr(exc)
        _log.error("dry-run row %s/%s failed: %r", row.gtin, row.language, exc)
    return outcome


def _preview_text(cfg: ClientConfig, row: PlanRow, mode: _Mode) -> str:
    """One row's intended mutations, in the words of the leg(s) actually selected.

    The links half names the *planned* target rather than a resolved one: a dry run builds
    no clients, so it cannot read state or look a page up, let alone verify it. Saying so is
    the point — the verification that refuses a 404 happens at execute time.
    """
    parts = []
    if mode.writes_pages:
        media = " (with hero image/video)" if cfg.media is not None else ""
        parts.append(
            f"upsert WP {cfg.wordpress.post_type!r} page {row.slug!r}{media}, then link "
            f"this GTIN's languages as translations"
        )
    if mode.writes_links:
        parts.append(
            f"point GS1 {_digital_link_url(cfg, row)} at {row.target_url} and render its "
            f"QR (the real run verifies that target serves first, and refuses if it does not)"
        )
    return "would " + ", then ".join(parts)


def _confirmed_rows(confirmed: ConfirmedPlan) -> list[PlanRow]:
    """Return the plan rows in the confirmed subset, in plan order."""
    keys = confirmed.confirmed_gtins_by_lang
    return [row for row in confirmed.plan.rows if (row.gtin, row.language) in keys]


def _drop_held(rows: list[PlanRow], *, revive: bool) -> list[PlanRow]:
    """Drop rows for GTINs that were deliberately unpublished, unless ``revive`` (§8.3).

    A held GTIN is one ``run_unpublish`` took down. Confirming a plan is a judgement about
    *content* — the operator is agreeing the pages are right, not that a product somebody
    unpublished should go back up — so reviving one takes its own flag rather than riding
    along on that confirmation.

    Dropped by GTIN rather than by row, for the reason the per-GTIN phase exists at all:
    the resolver write carries every language at once, so publishing one language of a
    held GTIN would write a link set missing the other.
    """
    held = {row.gtin for row in rows if row.classification is PlanClassification.HELD}
    if not held:
        return rows
    if revive:
        _log.warning(
            "--revive: re-publishing %d held GTIN(s): %s", len(held), ", ".join(sorted(held))
        )
        return rows
    _log.warning(
        "skipping %d held (unpublished) GTIN(s): %s — pass --revive to publish them again",
        len(held),
        ", ".join(sorted(held)),
    )
    return [row for row in rows if row.gtin not in held]


def _pilot_allowlist(cfg: ClientConfig) -> frozenset[str] | None:
    """The canonical GTINs a run may touch, or ``None`` when unrestricted (§9.5).

    ``None`` unless the client sets ``media.restrict_to_mapped_gtins``. Otherwise the set of GTINs
    with a client-confirmed video in every language, read live from the mapping file. If the
    mapping cannot be loaded the set is **empty** (block everything) — failing safe, since a run
    that cannot determine the allowlist must not publish anything.
    """
    media = cfg.media
    if media is None or not media.restrict_to_mapped_gtins or not media.video_map_path:
        return None
    try:
        vmap = load_video_map(Path(media.video_map_path))
    except VideoMapError as exc:
        _log.error(
            "cannot load video map %s for the pilot allowlist: %s — blocking every GTIN",
            media.video_map_path,
            exc,
        )
        return frozenset()
    return fully_mapped_gtins(vmap, cfg.wordpress.languages)


def _restrict_to_pilot(rows: list[PlanRow], allowlist: frozenset[str] | None) -> list[PlanRow]:
    """Drop rows for GTINs outside the pilot allowlist so no other GTIN is ever written (§9.5).

    A hard safety gate applied to every run: even a plan passed with ``--plan`` cannot publish a
    GTIN that lacks a client-confirmed video in each language. ``None`` means unrestricted.
    """
    if allowlist is None:
        return rows
    blocked = sorted({row.gtin for row in rows if canon_gtin(row.gtin) not in allowlist})
    if blocked:
        _log.warning(
            "pilot restriction: blocking %d GTIN(s) with no confirmed video in every language: %s",
            len(blocked),
            ", ".join(blocked),
        )
    return [row for row in rows if canon_gtin(row.gtin) in allowlist]


class _RunLog:
    """The run's JSONL, written a row at a time rather than all at once at the end.

    Two things follow from writing incrementally. A run that dies part-way — a crashed
    process, a killed terminal, an unhandled error building the clients — used to lose the
    *entire* log, including the rows that had already succeeded and been committed to
    ``state.json``; now it keeps them. And a parent process has a file it can tail while the
    run is in flight, which is the only progress channel this script offers.

    The file is created with ``"x"`` (atomic exclusive create) and disambiguated on
    collision, because the name is ``datetime.now(UTC)`` to the second: two runs started in
    the same second would otherwise share one file and interleave their rows into nonsense.
    That does **not** make concurrent runs supported (E20) — ``state.json`` still races —
    it only stops one run's log from being corrupted by another's.

    Each row is flushed as it is written, so a hard kill loses nothing already reported.
    """

    #: How many same-second suffixes to try before giving up.
    _MAX_COLLISIONS: Final = 100

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        for n in range(self._MAX_COLLISIONS):
            candidate = path if n == 0 else path.with_stem(f"{path.stem}-{n}")
            try:
                self._handle = candidate.open("x", encoding="utf-8")
            except FileExistsError:
                continue
            self.path = candidate
            return
        raise OSError(f"cannot create a run log at {path}: {self._MAX_COLLISIONS} names taken")

    def append(self, outcome: RunOutcome) -> RunOutcome:
        """Write one outcome as a JSON line and flush; returns it for convenient chaining."""
        self._handle.write(json.dumps(outcome.model_dump(mode="json"), ensure_ascii=False) + "\n")
        self._handle.flush()
        return outcome

    def append_all(self, outcomes: Iterable[RunOutcome]) -> list[RunOutcome]:
        """Consume ``outcomes``, writing each as it arrives, and return them all."""
        return [self.append(outcome) for outcome in outcomes]

    def __enter__(self) -> _RunLog:
        return self

    def __exit__(self, *exc: object) -> None:
        self._handle.close()


def _run(  # noqa: PLR0913 — the plan, its credentials, and one flag per policy switch
    cfg: ClientConfig,
    confirmed: ConfirmedPlan,
    resolved_gs1: ResolvedGS1Config | None,
    *,
    dry_run: bool,
    revive: bool,
    mode: _Mode,
) -> int:
    """Execute (or preview) the confirmed plan; return the process exit code."""
    rows = _restrict_to_pilot(
        _drop_held(_confirmed_rows(confirmed), revive=revive), _pilot_allowlist(cfg)
    )
    engine = TemplateEngine(cfg.client_id, cfg.template)
    ts = datetime.now(UTC)
    prefix = "[dry-run] " if dry_run else ""
    leg = "" if mode is _Mode.BOTH else f" ({mode} only)"

    # Announced up front, not just at the end: the name is derived from a timestamp only this
    # process knows, so nothing outside it can compute where the run is reporting to — and a
    # run that dies never reaches the closing line at all.
    with _RunLog(
        Path("output") / cfg.client_id / "runs" / f"{ts.strftime(_TS_FORMAT)}.jsonl"
    ) as log:
        print(f"{prefix}{len(rows)} row(s){leg}; log: {log.path}", file=sys.stderr)
        if dry_run or resolved_gs1 is None:
            outcomes = log.append_all(_preview_row(cfg, row, engine, ts, mode) for row in rows)
        else:
            state = load_state(cfg.client_id)
            with (
                WordPressClient(cfg.wordpress) as wp,
                GS1DigitalLinkClient(resolved_gs1) as gs1,
            ):
                outcomes = log.append_all(_execute(cfg, rows, wp, gs1, engine, state, ts, mode))
            save_state(state)

    errors = sum(1 for o in outcomes if o.status == "error")
    _log.info("run complete: %d ok, %d error(s)", len(outcomes) - errors, errors)
    print(
        f"{prefix}{len(outcomes)} row(s){leg}, {errors} error(s); log: {log.path}",
        file=sys.stderr,
    )
    return _EXIT_ERRORS if errors else _EXIT_OK


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_execute", description="Execute a confirmed run plan."
    )
    parser.add_argument(
        "client_id",
        nargs="?",
        help="Key under clients: in clients.yml (optional when only one client is defined)",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--plan", help="Path to a Plan JSON (all rows confirmed)")
    source.add_argument("--confirmed", help="Path to a ConfirmedPlan JSON")
    parser.add_argument(
        "--only",
        choices=[_Mode.PAGES.value, _Mode.LINKS.value],
        help=(
            "Run one leg instead of both: 'pages' writes the WordPress pages and stops "
            "(reversible); 'links' writes only the Digital Link records and QR, pointing them "
            "at pages that already exist (permanent, and each target is verified first). "
            "Omit to do both, which is the default."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview intended mutations without performing them"
    )
    parser.add_argument(
        "--revive",
        action="store_true",
        help="Also publish GTINs that run_unpublish took down (skipped by default)",
    )
    parser.add_argument(
        "--i-understand-production",
        action="store_true",
        help=(
            "Required to execute a real run when gs1.environment is 'production' — guards live "
            "writes to WordPress and permanent GS1 records. Not needed with --dry-run."
        ),
    )
    return parser.parse_args(argv)


def _production_refusal(cfg: ClientConfig, mode: _Mode) -> str:
    """The exit-2 message for a production run without the acknowledgment flag.

    Names only what the selected leg actually does. ``--only pages`` writes live pages but
    creates no permanent records, and claiming otherwise would teach the operator to read
    past this message — which is the one thing it cannot afford.
    """
    if mode is _Mode.PAGES:
        consequence = f"would publish live pages to {cfg.wordpress.site_url}"
    elif mode is _Mode.LINKS:
        consequence = (
            "would register permanent GS1 records (GS1 v2 has no delete — records can only "
            "be disabled)"
        )
    else:
        consequence = (
            f"would publish live pages to {cfg.wordpress.site_url} and register permanent GS1 "
            "records (GS1 v2 has no delete — records can only be disabled)"
        )
    return (
        f"refusing to write to PRODUCTION: gs1.environment is 'production', so this run "
        f"{consequence}. Re-run with --i-understand-production to proceed, or --dry-run to "
        f"preview without writing."
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    args = _parse_args(argv)
    # A dry run exists to show the intended mutations, and those are logged at INFO — at
    # WARNING it printed nothing but a row count, which is the one thing it did not need to
    # say. Only dry runs are raised: a real run's per-row detail belongs in the JSONL.
    logging.basicConfig(
        level=logging.INFO if args.dry_run else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    mode = _Mode(args.only) if args.only else _Mode.BOTH
    try:
        cfg = get_client(args.client_id)
        confirmed = _load_confirmed(args)
        if (
            not args.dry_run
            and cfg.gs1.environment == "production"
            and not args.i_understand_production
        ):
            print(_production_refusal(cfg, mode), file=sys.stderr)
            return _EXIT_CONFIG_ERROR
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
    return _run(cfg, confirmed, resolved_gs1, dry_run=args.dry_run, revive=args.revive, mode=mode)


if __name__ == "__main__":
    load_env()
    raise SystemExit(main())
