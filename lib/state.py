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


def state_path(client_id: str) -> Path:
    """Return the state-file path for ``client_id`` (``output/{id}/state.json``)."""
    return Path("output") / client_id / STATE_FILENAME


def load_state(client_id: str) -> State:
    """Load a client's persisted state, or an empty state if none exists (§4.8).

    Args:
        client_id: The client whose state to load.

    Returns:
        The persisted :class:`~lib.records.State`, or an empty one
        (``entries={}``) when no state file is present yet.

    Raises:
        StateError: If the file exists but cannot be read or parsed.
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
        raise StateError(f"state file for {client_id!r} at {path} is corrupt: {exc}") from exc


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


def _classify(
    prior: StateEntry | None, content_hash: str, target_url: str
) -> tuple[PlanClassification, dict[str, tuple[str, str]] | None]:
    """Classify one row against its prior state entry, with a best-effort diff.

    The diff can only carry values recoverable from :class:`~lib.records.StateEntry`,
    which stores no prior product fields — so a CHANGED row surfaces ``target_url``
    (old ``wp_url`` → new) when it differs, and ``None`` otherwise. A title before/after
    is not derivable and is never fabricated.
    """
    if prior is None:
        return PlanClassification.NEW, None
    if prior.content_hash == content_hash:
        return PlanClassification.UNCHANGED, None
    diff = {"target_url": (prior.wp_url, target_url)} if prior.wp_url != target_url else None
    return PlanClassification.CHANGED, diff


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
    CHANGED (with a ``target_url`` diff when it moved). A language with no
    ``product_name`` for a product is omitted with a warning (edge E18) rather than
    emitting a row with a missing title.

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
            content_hash = compute_content_hash(product, language, target_url)
            prior = state.entries.get(product.gtin, {}).get(language)
            classification, diff = _classify(prior, content_hash, target_url)
            rows.append(
                PlanRow(
                    gtin=product.gtin,
                    language=language,
                    classification=classification,
                    title=product.product_name.values[language],
                    slug=slug,
                    content_hash=content_hash,
                    target_url=target_url,
                    diff=diff,
                    product=product,
                )
            )
    return rows
