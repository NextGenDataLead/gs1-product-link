"""Content-generator core: cache, request/result contract, and deterministic merge.

The generator writes the copy WordPress shows for a product — the tagline and the
``Eigenschappen`` benefit bullets — while everything else on the page stays deterministic
assembly. This module is **producer-agnostic and network-free**: it defines the cache that
stores generated copy between runs, the :class:`GenerationRequest`/:class:`GenerationResult`
contract both producers (the Cowork-native session and the headless API backend) fill, and
the pure :func:`merge_generated` step that folds cached copy onto :class:`ProductRecord`
before classification (mirroring ``run_plan._assign_categories``). See
``docs/clients/noviplast-generator-spec.md``.

Determinism comes from the cache, not the producer: each entry is keyed by a fingerprint of
the source inputs plus a ``prompt_version``, so re-runs reuse frozen copy and a feed edit (a
new fingerprint) supersedes a stale generated value.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lib.errors import GeneratorError
from lib.records import LocalisedText, ProductRecord, SourceIssue
from lib.units import decode_net_content, decode_unit

_log = logging.getLogger(__name__)

CACHE_FILENAME: Final = "generated_cache.json"

#: Longest a feed USP (attr 1067) may be to use verbatim; a longer one is tightened by the
#: producer instead. Roughly one readable line — the live taglines run ~30-60 chars.
MAX_VERBATIM_USP_CHARS: Final = 80

#: How a cache entry's copy came to be — drives what the report says about it.
ORIGIN_FEED: Final = "feed"  # 1067 used verbatim (short); authoritative, not reported
ORIGIN_TIGHTENED: Final = "tightened"  # 1067 shortened by the producer; reported as adjusted
ORIGIN_GENERATED: Final = "generated"  # produced from 1083 (no usable 1067); reported as generated

#: What a pending request asks the producer to do.
MODE_TIGHTEN: Final = "tighten"  # 1067 present but too long — shorten and rank it
MODE_GENERATE: Final = "generate"  # no usable 1067 — write from 1083

#: Placeholder material values the feed carries in lieu of a real one — treated as absent.
_PLACEHOLDER_PREFIX: Final = "zzz"

#: Per-language labels for the assembled description. Falls back to the default language's
#: labels for any language not listed (only nl/fr exist in the pilot data).
_LABELS: Final[dict[str, dict[str, str]]] = {
    "nl": {
        "eigenschappen": "Eigenschappen",
        "technische": "Technische details",
        "afmetingen": "Afmetingen",
        "materiaal": "Materiaal",
    },
    "fr": {
        "eigenschappen": "Caractéristiques",
        "technische": "Détails techniques",
        "afmetingen": "Dimensions",
        "materiaal": "Matériau",
    },
}


# --- Contract: inputs, requests, results, cache ------------------------------


class GenerationInputs(BaseModel):
    """The source-data inputs one generation is derived from, for one language.

    These feed both the producer's prompt and the cache fingerprint, so any change to them
    invalidates the cached copy. Language-agnostic feed values (dimensions, material) are
    shared across a product's languages; the localised ones are already language-specific.
    """

    model_config = ConfigDict(frozen=True)

    functional_name: str | None = None
    marketing_message: str | None = None  # attr 1083
    feature_benefit: str | None = None  # attr 1067
    net_content: str | None = None
    dim_height: str | None = None
    dim_width: str | None = None
    dim_depth: str | None = None
    material: str | None = None


class GenerationResult(BaseModel):
    """The copy a producer generated for one ``(gtin, language)``.

    ``usps`` is one ranked list: ``usps[0]`` is the tagline (the page headline, the header-video
    caption, and the description's opening line), and ``usps[1:]`` are the Eigenschappen bullets.
    The Technische-details block is not here — it is assembled deterministically from feed data
    (net content, dimensions, material). ``product_name`` is populated only when the feed lacked a
    name in this language and the producer supplied a translation (the missing-French fill).
    """

    model_config = ConfigDict(frozen=True)

    usps: list[str] = Field(min_length=1)
    product_name: str | None = None


class GenerationRequest(BaseModel):
    """One unit of copy to generate: a ``(gtin, language)`` plus its inputs and fingerprint.

    ``needs_name`` tells the producer to also supply a translated ``product_name`` because the
    feed carries none for this language.
    """

    model_config = ConfigDict(frozen=True)

    gtin: str
    language: str
    inputs: GenerationInputs
    input_fingerprint: str
    needs_name: bool = False
    mode: str = MODE_GENERATE  # MODE_TIGHTEN to shorten a long 1067, else MODE_GENERATE
    candidates: list[str] = Field(default_factory=list)  # the 1067 USPs to tighten (MODE_TIGHTEN)


class CacheEntry(BaseModel):
    """A stored generation for one ``(gtin, language)``.

    ``input_fingerprint`` gates reuse; ``provenance`` and ``source_input`` are audit metadata
    (which producer made it, and the source-language text it was derived from) surfaced in the
    generated-content report. ``source_input`` never participates in the fingerprint.
    """

    model_config = ConfigDict(frozen=True)

    usps: list[str]
    product_name: str | None
    origin: str  # ORIGIN_FEED | ORIGIN_TIGHTENED | ORIGIN_GENERATED
    input_fingerprint: str
    provenance: str
    source_input: str
    generated_at: datetime


class GeneratedCache(BaseModel):
    """The persisted generated-copy cache for a client, keyed ``entries[gtin][language]``.

    Mutable, like :class:`~lib.records.State`: it is a between-runs artifact that producers
    upsert into and :func:`merge_generated` reads.
    """

    client_id: str
    entries: dict[str, dict[str, CacheEntry]] = Field(default_factory=dict)

    def get(self, gtin: str, language: str) -> CacheEntry | None:
        """Return the entry for ``(gtin, language)``, or ``None`` when absent."""
        return self.entries.get(gtin, {}).get(language)


# --- Cache IO (mirrors lib.state atomic write) -------------------------------


def cache_path(client_id: str) -> Path:
    """Return the cache path (``output/{client_id}/data/generated_cache.json``)."""
    return Path("output") / client_id / "data" / CACHE_FILENAME


def load_cache(client_id: str) -> GeneratedCache:
    """Load a client's generated-copy cache, or an empty one when none exists.

    Args:
        client_id: The client whose cache to load.

    Returns:
        The persisted cache, or an empty cache when no file is present.

    Raises:
        GeneratorError: If the file exists but cannot be read or parsed. Unlike state, a
            corrupt cache is not silently reset — losing generated copy re-bills the producer,
            so the operator is told rather than have it quietly regenerated.
    """
    path = cache_path(client_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return GeneratedCache(client_id=client_id)
    except OSError as exc:
        raise GeneratorError(f"cannot read cache for {client_id!r} at {path}: {exc}") from exc
    try:
        return GeneratedCache.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise GeneratorError(f"cache for {client_id!r} at {path} is corrupt: {exc}") from exc


def save_cache(cache: GeneratedCache) -> None:
    """Atomically persist ``cache`` to ``output/{client_id}/data/generated_cache.json``.

    Writes to a temporary file in the destination directory and ``os.replace``s it over the
    target, so a crash mid-write leaves either the old file or the whole new one.

    Args:
        cache: The cache to persist; its ``client_id`` determines the path.

    Raises:
        GeneratorError: If the directory or file cannot be written.
    """
    path = cache_path(cache.client_id)
    payload = json.dumps(cache.model_dump(mode="json"), ensure_ascii=False, indent=2)
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
        raise GeneratorError(
            f"cannot write cache for {cache.client_id!r} at {path}: {exc}"
        ) from exc


# --- Fingerprint & input gathering -------------------------------------------


def _fingerprint(inputs: GenerationInputs, language: str, prompt_version: str) -> str:
    """Return a stable SHA-256 over the inputs, language, and prompt version.

    Canonical (sorted keys, fixed separators) like ``lib.state.compute_content_hash``, so it is
    deterministic across runs. The producer/model is deliberately excluded: the Cowork and API
    backends are interchangeable producers of the same logical copy, so switching between them
    must not invalidate the cache.
    """
    canonical = json.dumps(
        {
            "inputs": inputs.model_dump(mode="json"),
            "language": language,
            "prompt_version": prompt_version,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_placeholder(value: str | None) -> bool:
    """Whether a material value is a datapool placeholder rather than a real material."""
    return value is not None and value.strip().casefold().startswith(_PLACEHOLDER_PREFIX)


def _material(product: ProductRecord) -> str | None:
    """The product's material, or ``None`` when absent or a placeholder."""
    material = product.extras.get("material")
    return None if _is_placeholder(material) else material


def _gather_inputs(product: ProductRecord, language: str) -> GenerationInputs:
    """Assemble the generation inputs for one ``(gtin, language)`` from the record."""
    name = None if product.product_name is None else product.product_name.get(language)
    return GenerationInputs(
        functional_name=name or product.extras.get("functional_name"),
        marketing_message=(
            product.description_short.get(language) if product.description_short else None
        ),
        feature_benefit=(
            product.description_long.get(language) if product.description_long else None
        ),
        net_content=product.net_content,
        dim_height=product.extras.get("dim_height"),
        dim_width=product.extras.get("dim_width"),
        dim_depth=product.extras.get("dim_depth"),
        material=_material(product),
    )


def _feature_candidates(inputs: GenerationInputs) -> list[str]:
    """Split the joined 1067 feature/benefit text into candidate USPs (newline-separated)."""
    if not inputs.feature_benefit:
        return []
    return [line.strip() for line in inputs.feature_benefit.split("\n") if line.strip()]


def _all_short(candidates: list[str]) -> bool:
    """Whether every candidate USP is short enough to use verbatim."""
    return all(len(c) <= MAX_VERBATIM_USP_CHARS for c in candidates)


def _has_name(product: ProductRecord, language: str) -> bool:
    return product.product_name is not None and product.product_name.get(language) is not None


def _is_fresh(cache: GeneratedCache, gtin: str, language: str, fingerprint: str) -> bool:
    """Whether the cache already holds an entry matching the current input fingerprint."""
    entry = cache.get(gtin, language)
    return entry is not None and entry.input_fingerprint == fingerprint


def prefill_from_feed(
    products: list[ProductRecord],
    cache: GeneratedCache,
    languages: list[str],
    prompt_version: str,
    *,
    now: datetime,
) -> None:
    """Fill the cache in place for units whose 1067 USPs are short enough to use verbatim.

    Deterministic and network-free: when the feed carries feature/benefit copy (attr 1067) and
    every entry is within :data:`MAX_VERBATIM_USP_CHARS`, that copy *is* the ranked USP list, so no
    producer is needed. Longer 1067 and absent 1067 are left for :func:`pending_requests`. Skips
    units already fresh in the cache. Run this before ``pending_requests``.
    """
    for product in products:
        for language in languages:
            inputs = _gather_inputs(product, language)
            fingerprint = _fingerprint(inputs, language, prompt_version)
            if _is_fresh(cache, product.gtin, language, fingerprint):
                continue
            candidates = _feature_candidates(inputs)
            if not candidates or not _all_short(candidates):
                continue
            request = GenerationRequest(
                gtin=product.gtin,
                language=language,
                inputs=inputs,
                input_fingerprint=fingerprint,
                needs_name=not _has_name(product, language),
            )
            apply_result(
                cache,
                request,
                GenerationResult(usps=candidates),
                origin=ORIGIN_FEED,
                provenance="feed:1067",
                now=now,
            )


def pending_requests(
    products: list[ProductRecord],
    cache: GeneratedCache,
    languages: list[str],
    prompt_version: str,
) -> list[GenerationRequest]:
    """Return the ``(gtin, language)`` units still needing a producer, each with its mode.

    A unit is pending when it has no fresh cache entry (no entry, or a fingerprint that no longer
    matches the inputs after a feed edit or ``prompt_version`` bump) and it was not verbatim-filled
    by :func:`prefill_from_feed`. Its ``mode`` is :data:`MODE_TIGHTEN` when the feed carries 1067
    copy that is too long to use as-is (the producer shortens and ranks it), else
    :data:`MODE_GENERATE` (the producer writes from 1083).

    Args:
        products: The parsed products.
        cache: The current generated-copy cache (call ``prefill_from_feed`` first).
        languages: The languages to generate for.
        prompt_version: The active prompt version (part of the fingerprint).

    Returns:
        The pending requests, each carrying its inputs, fingerprint, mode, and 1067 candidates.
    """
    requests: list[GenerationRequest] = []
    for product in products:
        for language in languages:
            inputs = _gather_inputs(product, language)
            fingerprint = _fingerprint(inputs, language, prompt_version)
            if _is_fresh(cache, product.gtin, language, fingerprint):
                continue
            candidates = _feature_candidates(inputs)
            mode = MODE_TIGHTEN if candidates else MODE_GENERATE
            requests.append(
                GenerationRequest(
                    gtin=product.gtin,
                    language=language,
                    inputs=inputs,
                    input_fingerprint=fingerprint,
                    needs_name=not _has_name(product, language),
                    mode=mode,
                    candidates=candidates,
                )
            )
    return requests


def _clean_bullets(bullets: list[str]) -> list[str]:
    """Strip each bullet and drop the empties."""
    return [stripped for stripped in (b.strip() for b in bullets) if stripped]


def apply_result(  # noqa: PLR0913 — a validated write needs its result, provenance, and clock
    cache: GeneratedCache,
    request: GenerationRequest,
    result: GenerationResult,
    *,
    origin: str,
    provenance: str,
    now: datetime,
) -> None:
    """Validate a producer's result and upsert it into ``cache`` in place.

    Args:
        cache: The cache to update (mutated).
        request: The request this result answers (supplies gtin/language/fingerprint/inputs).
        result: The producer's copy.
        origin: How the copy came to be — :data:`ORIGIN_TIGHTENED` (shortened from 1067) or
            :data:`ORIGIN_GENERATED` (written from 1083). :data:`ORIGIN_FEED` is set by
            :func:`prefill_from_feed`, not here.
        provenance: Which producer made it, e.g. ``"api:claude-sonnet-5"`` or ``"cowork"``.
        now: The generation timestamp (injected for determinism).

    Raises:
        GeneratorError: If the result has no usable USPs after cleaning.
    """
    usps = _clean_bullets(result.usps)
    if not usps:
        raise GeneratorError(
            f"empty generation result for {request.gtin}/{request.language}: "
            f"usps={result.usps!r}"
        )
    source_input = (
        request.inputs.feature_benefit
        or request.inputs.marketing_message
        or request.inputs.functional_name
        or ""
    )
    entry = CacheEntry(
        usps=usps,
        product_name=result.product_name if request.needs_name else None,
        origin=origin,
        input_fingerprint=request.input_fingerprint,
        provenance=provenance,
        source_input=source_input,
        generated_at=now,
    )
    cache.entries.setdefault(request.gtin, {})[request.language] = entry


# --- Deterministic assembly --------------------------------------------------


def _labels(language: str) -> dict[str, str]:
    """Return the per-language labels, defaulting to Dutch for an unlisted language."""
    return _LABELS.get(language, _LABELS["nl"])


def _combine_title(name: str, variation: str | None) -> str:
    """Combine a functional name with a product variation, avoiding duplication.

    Blind concatenation produces "Snoeischaar snoeischaar"; a variation already contained in the
    name (case-insensitively) is dropped, otherwise it is appended.
    """
    if not variation:
        return name
    if variation.casefold() in name.casefold():
        return name
    return f"{name} {variation}"


def _bullets_block(heading: str, bullets: list[str]) -> str:
    """Render a ``<p><strong>heading</strong><br />• …</p>`` block."""
    lines = "<br />".join(f"• {b}" for b in bullets)
    return f"<p><strong>{heading}</strong><br />{lines}</p>"


def _dimensions_bullet(product: ProductRecord, language: str, fallback: str) -> str | None:
    """Render the "H × W × D unit" dimensions bullet, or ``None`` when incomplete.

    Every pilot product carries all three dimensions in the same unit (``MMT``); the bullet is
    emitted only when all three are present so a partial measurement never renders half a size.
    """
    raw = [product.extras.get(k) for k in ("dim_height", "dim_width", "dim_depth")]
    if not all(raw):
        return None
    values: list[str] = []
    unit_word: str | None = None
    for cell in raw:
        assert cell is not None  # guarded by all(raw)
        number, separator, code = cell.rpartition(" ")
        if not separator:
            return None  # no unit code — do not guess a dimension line
        values.append(number)
        unit_word = decode_unit(code, language, fallback_language=fallback) or code
    label = _labels(language)["afmetingen"]
    return f"{label}: {' × '.join(values)} {unit_word}"


def _technische_details(product: ProductRecord, language: str, fallback: str) -> list[str]:
    """Assemble the deterministic Technische-details bullets from feed data.

    Net content and dimensions decode their unit codes per language; material is a
    language-agnostic feed scalar shown verbatim (so a French page shows the Dutch material
    word — a candidate for future translation, tracked in the spec).
    """
    bullets: list[str] = []
    net_content = decode_net_content(product.net_content, language, fallback_language=fallback)
    if net_content:
        bullets.append(net_content)
    dimensions = _dimensions_bullet(product, language, fallback)
    if dimensions:
        bullets.append(dimensions)
    material = _material(product)
    if material:
        bullets.append(f"{_labels(language)['materiaal']}: {material}")
    return bullets


def _assemble_description(
    usps: list[str], product: ProductRecord, language: str, fallback: str
) -> str:
    """Assemble the three-part ``product_description`` HTML blob from the USP list and feed data.

    ``usps[0]`` is the tagline (``<p><strong>…</strong></p>``); ``usps[1:]`` are the generated
    Eigenschappen bullets; the Technische-details bullets are assembled deterministically from net
    content, dimensions, and material. Matches the live page shape.
    """
    labels = _labels(language)
    parts = [f"<p><strong>{usps[0]}</strong></p>"]
    if len(usps) > 1:
        parts.append(_bullets_block(labels["eigenschappen"], usps[1:]))
    technische = _technische_details(product, language, fallback)
    if technische:
        parts.append(_bullets_block(labels["technische"], technische))
    return "\n".join(parts)


def _missing_input_issue(gtin: str, language: str, inputs: GenerationInputs) -> SourceIssue:
    """Flag a blank marketing message (attr 1083) — the primary input for USP generation.

    Reported even when 1067 lets generation proceed, because 1083 is the field the datapool
    should carry; the detail notes whether the feature/benefit fallback exists.
    """
    has_fallback = bool(inputs.feature_benefit)
    detail = (
        f"No marketing message (attr 1083) for {language}; "
        + (
            "USPs can still be seeded from feature/benefit (1067), but "
            if has_fallback
            else "there is nothing to generate USPs from — "
        )
        + "add marketing copy in MyGS1."
    )
    return SourceIssue(
        gtin=gtin,
        field=f"description_short.{language}",
        source="MarketingInformation attr 1083",
        issue="missing_generation_input",
        value="",
        detail=detail,
    )


def _content_issue(gtin: str, language: str, entry: CacheEntry) -> SourceIssue | None:
    """Report generated or adjusted copy; verbatim feed copy needs no report.

    Feed copy (attr 1067 used as-is) is authoritative and reported nowhere. Tightened copy — the
    feed's 1067 was too long and the producer shortened it — is flagged so a human confirms the
    shortening and fixes 1067 at source. Fully generated copy (no usable 1067) is flagged for
    review with its source-language input.
    """
    if entry.origin == ORIGIN_FEED:
        return None
    if entry.origin == ORIGIN_TIGHTENED:
        return SourceIssue(
            gtin=gtin,
            field=f"generated_description.{language}",
            source="adjusted from TradeItemFeatureBenefit attr 1067",
            issue="content_adjusted",
            value=entry.source_input,
            detail=(
                f"1067 copy for {language} was too long and was shortened ({entry.provenance}); "
                "review the adjusted copy and tighten attr 1067 at the source."
            ),
        )
    return SourceIssue(
        gtin=gtin,
        field=f"generated_description.{language}",
        source="generated (usps: tagline + Eigenschappen)",
        issue="content_generated",
        value=entry.source_input,
        detail=(
            f"Tagline and Eigenschappen for {language} were generated ({entry.provenance}); "
            "review the copy before publishing."
        ),
    )


def merge_generated(
    products: list[ProductRecord],
    cache: GeneratedCache,
    languages: list[str],
    default_language: str,
    prompt_version: str,
) -> tuple[list[ProductRecord], list[SourceIssue]]:
    """Fold cached generated copy onto each record and report every generated value.

    Pure and network-free. For each product it overwrites ``product_name`` with the
    variation-combined (and, for a feed gap, cache-filled) title, sets ``generated_tagline``
    (from the feed message or the first cached USP), and — when the cache holds usable
    Eigenschappen for the current inputs — sets ``generated_description`` to the assembled
    three-part HTML. A stale or missing cache entry simply yields no description for that
    language; the run_plan E18 backstop handles the resulting gap. Called before
    ``diff_against_state`` so generated content is part of the content hash.

    Args:
        products: The parsed products.
        cache: The generated-copy cache.
        languages: The languages to assemble for.
        default_language: The fallback language for unit decoding.
        prompt_version: The active prompt version (gates cache reuse via the fingerprint).

    Returns:
        The products with generated fields materialised, and one :class:`SourceIssue` per
        generated value carrying the source-language input it was derived from.
    """
    merged: list[ProductRecord] = []
    issues: list[SourceIssue] = []
    for product in products:
        names = dict(product.product_name.values) if product.product_name else {}
        taglines: dict[str, str] = {}
        descriptions: dict[str, str] = {}
        variation = product.extras.get("product_variation")
        for language in languages:
            inputs = _gather_inputs(product, language)
            fingerprint = _fingerprint(inputs, language, prompt_version)
            entry = cache.get(product.gtin, language)
            if entry is not None and entry.input_fingerprint != fingerprint:
                entry = None  # stale — a feed edit superseded it

            base = names.get(language)
            if base is None and entry is not None and entry.product_name:
                base = entry.product_name
            if base is not None:
                names[language] = _combine_title(base, variation)

            if not inputs.marketing_message:
                issues.append(_missing_input_issue(product.gtin, language, inputs))

            if entry is not None and entry.usps:
                taglines[language] = entry.usps[0]
                descriptions[language] = _assemble_description(
                    entry.usps, product, language, default_language
                )
                issue = _content_issue(product.gtin, language, entry)
                if issue is not None:
                    issues.append(issue)

        update: dict[str, object] = {}
        if product.product_name is None or names != product.product_name.values:
            update["product_name"] = LocalisedText(values=names)
        if taglines:
            update["generated_tagline"] = LocalisedText(values=taglines)
        if descriptions:
            update["generated_description"] = LocalisedText(values=descriptions)
        merged.append(product.model_copy(update=update) if update else product)
    return merged, issues
