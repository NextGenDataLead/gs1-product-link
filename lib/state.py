"""Per-client run state: persistence, atomic writes, and content hashing.

Implements ``docs/IMPLEMENTATION_SPEC.md`` §4.8. State records, per
``(GTIN, language)``, the WordPress page id/URL, the featured-media id, the
content and GS1 link-set hashes, and the last-run timestamp — enough for
``scripts/run_execute.py`` to run idempotently and for change detection between
runs. The state models themselves (:class:`~lib.records.State`,
:class:`~lib.records.StateEntry`) live in ``lib/records.py``; this module is the
persistence/logic layer over them.

``save_state`` is **atomic**: it writes to a temporary file in the destination
directory and ``os.replace``s it into place, so a crash mid-write leaves the
previous ``state.json`` intact rather than a truncated file (§12 Phase 6 DoD).
``load_state`` recovers from a corrupt file rather than aborting (edge E19) — see its
docstring for why that is safe and what the caller must surface.

``diff_against_state`` (§4.8) classifies each ``(GTIN, language)`` against prior
state — NEW / UNCHANGED / CHANGED by content hash — and builds the ``PlanRow`` list
that ``scripts/run_plan.py`` writes to ``plan.json``. Its signature takes the whole
:class:`~lib.config.WordPressConfig` rather than §4.8's bare ``target_url_pattern``,
because building a ``PlanRow`` needs the slug pattern, site URL, post type, and
default language too — all of which live on that config.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from lib.errors import ConfigError, StateError
from lib.records import PlanClassification, PlanRow, ProductRecord, State, StateEntry

if TYPE_CHECKING:
    from lib.config import WordPressConfig

_log = logging.getLogger(__name__)

#: Per-client state file, relative to the working directory (mirrors the
#: ``output/{client_id}/...`` layout used by ``scripts/parse_export.py``).
STATE_FILENAME: Final = "state.json"

#: Timestamp suffix for a quarantined corrupt state file (matches the run-log format).
CORRUPT_BACKUP_TS_FORMAT: Final = "%Y%m%dT%H%M%SZ"

#: The WordPress post status a live product page carries. Anything else means the page is
#: not publicly reachable, which :func:`_is_held` reads as "taken down on purpose".
_PUBLISHED_STATUS: Final = "publish"


def state_path(client_id: str) -> Path:
    """Return the state-file path for ``client_id`` (``output/{id}/state.json``)."""
    return Path("output") / client_id / STATE_FILENAME


def load_state(client_id: str) -> State:
    """Load a client's persisted state, or an empty state if none exists (§4.8).

    A **corrupt** state file is recovered from rather than fatal (edge E19): it is moved
    aside to ``state.json.corrupt.{ts}``, an ERROR is logged, and an empty state is
    returned with ``reset_from_corrupt`` set. State is a cache of what the tool believes
    it already did — derivable from the live systems, and safe to rebuild, because every
    write path is idempotent (§6.1–§6.5): ``upsert_page`` still finds the live page by
    slug or ``meta.gtin`` without a known id, ``safe_upsert`` reads before it writes, and
    QR renders are byte-identical. The cost of a reset is redundant work, not corruption.
    An **unreadable** file (permissions, I/O fault) is a different animal — that is an
    environmental fault where continuing would be wrong, so it still raises.

    Callers must surface ``reset_from_corrupt``: a reset turns an incremental re-run into
    a full rewrite (every row reclassifies as NEW), and the operator confirms the plan
    before any of it executes — so the reset has to reach them there, not only in a log.

    Args:
        client_id: The client whose state to load.

    Returns:
        The persisted :class:`~lib.records.State`; an empty one (``entries={}``) when no
        state file is present yet; or an empty one with ``reset_from_corrupt=True`` when
        a corrupt file was moved aside.

    Raises:
        StateError: If the file exists but cannot be read, or a corrupt file cannot be
            moved aside.
    """
    path = state_path(client_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return State(client_id=client_id, entries={})
    except OSError as exc:
        raise StateError(f"cannot read state for {client_id!r} at {path}: {exc}") from exc

    try:
        return State.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        backup = _quarantine_corrupt(path, client_id, exc)
        _log.error(
            "state for %s at %s was corrupt (%s); moved to %s and starting fresh — every "
            "row will re-plan as NEW",
            client_id,
            path,
            exc,
            backup,
        )
        return State(client_id=client_id, entries={}, reset_from_corrupt=True)


def _quarantine_corrupt(path: Path, client_id: str, cause: Exception) -> Path:
    """Move a corrupt state file aside to ``state.json.corrupt.{ts}`` and return its path.

    The bad file is preserved, never deleted: it is the only evidence of what went wrong,
    and the operator's instinct otherwise is to delete it.
    """
    ts = datetime.now(UTC).strftime(CORRUPT_BACKUP_TS_FORMAT)
    backup = path.with_name(f"{path.name}.corrupt.{ts}")
    try:
        os.replace(path, backup)
    except OSError as exc:
        raise StateError(
            f"state file for {client_id!r} at {path} is corrupt ({cause}) and cannot be "
            f"moved aside to {backup}: {exc}"
        ) from exc
    return backup


def save_state(state: State) -> None:
    """Atomically persist ``state`` to ``output/{client_id}/state.json`` (§4.8).

    Writes to a temporary file in the destination directory, flushes and fsyncs
    it, then ``os.replace``s it over the target. The replace is atomic on POSIX,
    so a crash at any point leaves either the old file or the fully-written new
    file — never a partial one.

    Args:
        state: The state to persist; its ``client_id`` determines the path.

    Raises:
        StateError: If the directory or file cannot be written.
    """
    path = state_path(state.client_id)
    payload = json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise StateError(f"cannot write state for {state.client_id!r} at {path}: {exc}") from exc
    _log.info("Wrote state for %s (%d GTINs)", state.client_id, len(state.entries))


def compute_content_hash(product: ProductRecord, language: str, target_url: str) -> str:
    """Return a stable SHA-256 of the inputs that define a page's content (§4.8).

    The hash covers the full product, the language, and the resolver target URL,
    so any change to the rendered page or where it points changes the hash. It is
    canonical (sorted keys, fixed separators), hence deterministic across runs and
    processes.

    Args:
        product: The product whose content is being hashed.
        language: The page language (ISO 639-1).
        target_url: The resolver target URL for this ``(GTIN, language)``.

    Returns:
        The hex-encoded SHA-256 digest.
    """
    canonical = json.dumps(
        {
            "product": product.model_dump(mode="json"),
            "language": language,
            "target_url": target_url,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _lang_segment(language: str, default_language: str) -> str:
    """Return the URL path segment for a language ("" for the default, else ``{lang}/``)."""
    return "" if language == default_language else f"{language}/"


def _is_held(prior: StateEntry) -> bool:
    """Whether this entry records a product that was deliberately taken down.

    Either half counts. ``run_unpublish`` retracts the resolver before drafting the pages,
    so an interrupted run leaves entries with ``gs1_enabled=False`` and a still-published
    page; treating that as held is what lets the next run finish the job rather than
    reverse it.
    """
    return prior.wp_status != _PUBLISHED_STATUS or not prior.gs1_enabled


def _classify(
    prior: StateEntry | None, content_hash: str, title: str, target_url: str
) -> tuple[PlanClassification, dict[str, tuple[str, str]] | None]:
    """Classify one row against its prior state entry, with a field-level diff.

    The diff carries the fields whose prior value :class:`~lib.records.StateEntry`
    actually recorded — ``title`` and ``target_url`` (old ``wp_url`` → new) — in the
    order §10.6.2 presents them. Fields state does not keep are never fabricated, and
    an entry written before titles were persisted (``title is None``) omits the title
    row rather than guessing. A CHANGED row whose recorded fields all still match
    carries no diff: the change is in the product body, which state does not retain.

    HELD is tested **before** the hash, because a deliberately unpublished product's hash
    still matches the content it was published with — that is what makes it invisible.
    Comparing content first would classify it UNCHANGED and let the next confirmed run put
    it straight back up.
    """
    if prior is None:
        return PlanClassification.NEW, None
    if _is_held(prior):
        return PlanClassification.HELD, None
    if prior.content_hash == content_hash:
        return PlanClassification.UNCHANGED, None
    diff: dict[str, tuple[str, str]] = {}
    if prior.title is not None and prior.title != title:
        diff["title"] = (prior.title, title)
    if prior.wp_url != target_url:
        diff["target_url"] = (prior.wp_url, target_url)
    return PlanClassification.CHANGED, diff or None


def diff_against_state(
    products: list[ProductRecord],
    state: State,
    languages: list[str],
    wordpress: WordPressConfig,
) -> list[PlanRow]:
    """Classify each ``(GTIN, language)`` against prior state, building plan rows (§4.8, §8.2).

    For every product × language, computes the slug, resolver target URL, title, and
    content hash, then compares the hash to the persisted
    :class:`~lib.records.StateEntry`: no entry → NEW, equal hash → UNCHANGED, else
    CHANGED (carrying a ``title`` and/or ``target_url`` diff for whichever of those
    moved). A language with no ``product_name`` for a product is omitted with a warning
    (edge E18) rather than emitting a row with a missing title.

    Args:
        products: The products to plan.
        state: The client's persisted state (the classification baseline).
        languages: The languages to plan per product (``wordpress.languages``).
        wordpress: The client's WordPress config; supplies the slug/target-URL
            patterns, site URL, post type, and default language.

    Returns:
        One :class:`~lib.records.PlanRow` per planned ``(GTIN, language)``, in input
        order.

    Raises:
        ConfigError: If ``wordpress.slug_pattern`` or ``wordpress.target_url_pattern``
            is unset — both are required to build a plan.
    """
    slug_pattern = wordpress.slug_pattern
    target_url_pattern = wordpress.target_url_pattern
    if slug_pattern is None or target_url_pattern is None:
        raise ConfigError(
            "wordpress.slug_pattern and wordpress.target_url_pattern are required to build a plan"
        )

    rows: list[PlanRow] = []
    for product in products:
        for language in languages:
            if language not in product.product_name.values:  # E18
                _log.warning(
                    "SKIPPED %s (%s): missing product_name.%s", product.gtin, language, language
                )
                continue
            slug = slug_pattern.format(gtin=product.gtin, gtin14=product.gtin14)
            target_url = target_url_pattern.format(
                site_url=wordpress.site_url.rstrip("/"),
                lang_segment=_lang_segment(language, wordpress.default_language),
                post_type=wordpress.post_type,
                slug=slug,
                gtin=product.gtin,
                gtin14=product.gtin14,
            )
            title = product.product_name.values[language]
            content_hash = compute_content_hash(product, language, target_url)
            prior = state.entries.get(product.gtin, {}).get(language)
            classification, diff = _classify(prior, content_hash, title, target_url)
            rows.append(
                PlanRow(
                    gtin=product.gtin,
                    language=language,
                    classification=classification,
                    title=title,
                    slug=slug,
                    content_hash=content_hash,
                    target_url=target_url,
                    diff=diff,
                    product=product,
                )
            )
    return rows
