"""Content-generator core: cache, request/result contract, and deterministic merge.

The generator writes the copy WordPress shows for a product — the tagline and the
``Eigenschappen`` benefit bullets — while everything else on the page stays deterministic
assembly. This module is **producer-agnostic and network-free**: it defines the cache that
stores generated copy between runs, the :class:`GenerationRequest`/:class:`GenerationResult`
contract both producers (the in-session producer and the headless API backend) fill, and
the pure :func:`merge_generated` step that folds cached copy onto :class:`ProductRecord`
before classification (mirroring ``run_plan._assign_categories``). See
``docs/clients/democlient-generator-spec.md``.

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
from typing import Final, NamedTuple, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lib.errors import GeneratorError
from lib.gdsn import SCALAR_SEPARATOR, GdsnSource, source_label
from lib.records import LocalisedText, ProductRecord, SourceIssue
from lib.units import decode_net_content, decode_unit

_log = logging.getLogger(__name__)

CACHE_FILENAME: Final = "generated_cache.json"

#: Default prompt version — part of every cache fingerprint. Lives here (not in a script) so
#: ``run_generate`` and, later, ``run_plan`` fingerprint identically; bumping it invalidates
#: cached copy. Moves into ``GeneratorConfig`` when the API backend lands.
DEFAULT_PROMPT_VERSION: Final = "v1"

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

#: The ``gdsn_map`` field :attr:`GenerationInputs.marketing_message` comes from. Named so the
#: "is this a missing input or a pending translation?" test can ask about the right field.
_MARKETING_MESSAGE_FIELD: Final = "description_short"

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


class TranslatableField(BaseModel):
    """One source a client has opted into language-gap filling with ``translate: true``.

    Derived from ``clients.yml`` rather than listed in code, so which values are worth filling
    stays a property of the client's page — the same reasoning that keeps ``required`` and
    ``in_matrix`` in config.
    """

    model_config = ConfigDict(frozen=True)

    #: The ``gdsn_map`` field name or ``gdsn_extras`` key.
    field: str
    #: The source in the words MyGS1 uses, e.g. ``"MarketingInformation attr 1083"``. What the
    #: operator searches for when putting the filled value back.
    source_label: str
    #: Whether GS1 has a per-language slot for this attribute at all. ``False`` for a
    #: language-agnostic one (attr 4.012 Material): the translation still fixes the page, but it
    #: cannot be put back into the datapool, and a work queue that says it can wastes a trip.
    has_language_slot: bool = True


class TranslationGap(BaseModel):
    """One value the feed carries in another language but not in this one.

    Carries the source text, not just its name: the producer translates from it in the same call
    it writes the copy, and it enters the fingerprint so editing the source language supersedes
    the translation.
    """

    model_config = ConfigDict(frozen=True)

    field: str
    source_language: str
    source_value: str
    source_label: str
    has_language_slot: bool = True


class GenerationContext(BaseModel):
    """The client facts every generation step needs, gathered once by the caller.

    Bundled rather than passed as four more arguments to each of ``prefill_from_feed`` /
    ``pending_requests`` / ``merge_generated``: they are one fact — "what this client publishes,
    in which languages, and what may be filled" — and they must always travel together. Build it
    with :func:`generation_context`.
    """

    model_config = ConfigDict(frozen=True)

    languages: list[str]
    default_language: str
    prompt_version: str
    #: Sources marked ``translate: true``, in config order. Empty means nothing is ever filled.
    translatable: list[TranslatableField] = Field(default_factory=list)


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
    #: Field → the other language's text this unit's translations are derived from. In the
    #: fingerprint because it is a real input: without it, editing the Dutch 1083 left the French
    #: entry looking fresh, so the translation of a value that had changed survived the edit.
    translation_sources: dict[str, str] = Field(default_factory=dict)


class GenerationResult(BaseModel):
    """The copy a producer generated for one ``(gtin, language)``.

    ``usps`` is one ranked list: ``usps[0]`` is the tagline (the page headline, the header-video
    caption, and the description's opening line), and ``usps[1:]`` are the Eigenschappen bullets.
    The Technische-details block is not here — it is assembled deterministically from feed data
    (net content, dimensions, material). ``translations`` answers the request's
    :class:`TranslationGap` list: field name → the value rendered in this language.
    """

    model_config = ConfigDict(frozen=True)

    usps: list[str] = Field(min_length=1)
    #: Field → the value translated into this request's language. Only fields the request asked
    #: for are kept; see :func:`apply_result`.
    translations: dict[str, str] = Field(default_factory=dict)
    #: Claims the producer wrote that go *beyond* the literal feed text (e.g. "snoerloos"
    #: inferred from "batterie rechargeable"). Reported as ``generation_inference`` findings
    #: so a human verifies each before publishing. Never participates in the fingerprint.
    inferences: list[str] = Field(default_factory=list)


class GenerationRequest(BaseModel):
    """One unit of copy to generate: a ``(gtin, language)`` plus its inputs and fingerprint.

    ``translations`` lists the values the feed carries in another language and not this one, each
    with the text to translate from. It replaced a single ``needs_name`` flag: the title was never
    the only value that goes missing in one language, it was only the one that stopped a page
    publishing.
    """

    model_config = ConfigDict(frozen=True)

    gtin: str
    language: str
    inputs: GenerationInputs
    input_fingerprint: str
    translations: list[TranslationGap] = Field(default_factory=list)
    mode: str = MODE_GENERATE  # MODE_TIGHTEN to shorten a long 1067, else MODE_GENERATE
    candidates: list[str] = Field(default_factory=list)  # the 1067 USPs to tighten (MODE_TIGHTEN)


class LLMClient(Protocol):
    """A copy producer: turns one :class:`GenerationRequest` into a :class:`GenerationResult`.

    The seam both producers satisfy — the headless API backend (``lib.llm``) and test fakes —
    so ``scripts/run_generate.py`` can drive either through one loop. The Protocol takes no
    network dependency itself; determinism still comes from the cache, so a producer is only
    ever called for the gaps :func:`pending_requests` reports.
    """

    def generate_copy(self, request: GenerationRequest) -> GenerationResult:
        """Produce copy for ``request`` (its ``mode`` says tighten a 1067 or generate from 1083)."""
        ...


class CacheEntry(BaseModel):
    """A stored generation for one ``(gtin, language)``.

    ``input_fingerprint`` gates reuse; ``provenance`` and ``source_input`` are audit metadata
    (which producer made it, and the source-language text it was derived from) surfaced in the
    generated-content report. ``source_input`` never participates in the fingerprint.
    """

    model_config = ConfigDict(frozen=True)

    usps: list[str]
    #: Field → the value this language was missing, as the producer rendered it. Defaulted so a
    #: cache written before the field existed still loads; those entries are stale anyway, since
    #: the fingerprint gained ``translation_sources`` in the same change.
    translations: dict[str, str] = Field(default_factory=dict)
    origin: str  # ORIGIN_FEED | ORIGIN_TIGHTENED | ORIGIN_GENERATED
    input_fingerprint: str
    provenance: str
    source_input: str
    generated_at: datetime
    #: Claims written beyond the literal feed text, surfaced as ``generation_inference``
    #: findings in the generated-content report. Never participates in the fingerprint.
    inferences: list[str] = Field(default_factory=list)


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
    deterministic across runs. The producer/model is deliberately excluded: the in-session and
    API backends are interchangeable producers of the same logical copy, so switching between them
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


def _without_placeholders(value: str | None) -> str | None:
    """``value`` minus any placeholder or empty slot; ``None`` when no real slot is left.

    ``Material`` repeats in the feed and the parser joins its slots, so testing the whole string
    for a ``zzz…`` prefix stopped answering the question it was asked: ``kunststof, zzzanders``
    reads as an ordinary material, which would put the junk on the page *and* report it in §4 as
    a value to paste into MyGS1 — a blank turned into fabricated master data, the exact failure
    the placeholder rule exists to prevent.

    Slots are dropped by the same rule whether or not the value contains a placeholder. Skipping
    the walk for a value that has none would be the cheaper branch, but it would make an empty
    slot survive in ``a, , b`` and vanish in ``a, , zzzanders`` — one input's rendering deciding
    another's. Nothing legitimate is at risk from always walking: ``sep.join(s.split(sep))`` is an
    identity, so a value whose slots are all real comes back unchanged, punctuation included, and
    a single-slot value that merely *contains* a comma is never split into anything droppable.
    """
    if value is None:
        return None
    kept = [
        part for part in value.split(SCALAR_SEPARATOR) if part.strip() and not _is_placeholder(part)
    ]
    return SCALAR_SEPARATOR.join(kept) or None


def _material(product: ProductRecord, language: str, fallback: str) -> str | None:
    """The product's material for ``language``, or ``None`` when absent or a placeholder.

    Falls back to the default language: until the producer has translated it, a French page
    showing the Dutch word beats one showing no material at all.
    """
    return _without_placeholders(product.extra("material", language, fallback))


def generation_context(  # noqa: PLR0913 — each argument is a distinct client fact
    languages: list[str],
    default_language: str,
    prompt_version: str,
    gdsn_map: dict[str, GdsnSource],
    gdsn_extras: dict[str, GdsnSource],
) -> GenerationContext:
    """Bundle the client facts the generation steps need, reading ``translate`` from config.

    Args:
        languages: The site's configured languages, in config order.
        default_language: The language the feed is authored in.
        prompt_version: The active prompt version (part of every fingerprint).
        gdsn_map: The client's ``export.gdsn_map``.
        gdsn_extras: The client's ``export.gdsn_extras``.

    Returns:
        The context. ``translatable`` holds every source marked ``translate: true``, mapped fields
        before extras, each carrying the MyGS1 words for its attribute.
    """
    translatable = [
        TranslatableField(
            field=name,
            source_label=source_label(src),
            # A per-language GDSN attribute has a slot the filled value can go back into; a
            # language-agnostic one (attr 4.012 Material) does not.
            has_language_slot=src.localised,
        )
        for sources in (gdsn_map, gdsn_extras)
        for name, src in sources.items()
        if src.translate
    ]
    return GenerationContext(
        languages=list(languages),
        default_language=default_language,
        prompt_version=prompt_version,
        translatable=translatable,
    )


def _carried(values: dict[str, str]) -> dict[str, str]:
    """Drop the languages whose "value" is blank, and any placeholder slot within the rest.

    A ``zzz…`` placeholder is the feed saying it has no value — :func:`_material` already reads it
    that way for the prompt. Reading it as text to translate would ask the producer to render a
    placeholder into French and then tell the operator to paste that into MyGS1, turning a blank
    into fabricated master data.

    Cleaned rather than merely filtered, because a repeated attribute can be part real and part
    placeholder: ``kunststof, zzzanders`` is a language the feed *does* carry, and what it carries
    is ``kunststof``. See :func:`_without_placeholders`.
    """
    cleaned = {lang: _without_placeholders(text) for lang, text in values.items()}
    return {lang: text for lang, text in cleaned.items() if text and text.strip()}


def _field_values(product: ProductRecord, field: str, default_language: str) -> dict[str, str]:
    """Every language ``product`` carries ``field`` in, whatever shape it is stored in.

    A language-agnostic extra counts as carrying its one value in the default language: that is
    the language the feed is authored in, and treating it as language-less would mean no other
    language could ever be seen as missing it.
    """
    localised = product.extras_localised.get(field)
    if localised is not None:
        return _carried(localised.values)
    flat = product.extras.get(field)
    if flat is not None:
        return _carried({default_language: flat})
    value = getattr(product, field, None)
    if isinstance(value, LocalisedText):
        return _carried(value.values)
    return {}


def translation_gaps(
    product: ProductRecord, language: str, context: GenerationContext
) -> list[TranslationGap]:
    """Every translatable value ``product`` lacks in ``language`` but carries in another.

    **Never fills from nothing.** A field blank in every configured language yields no gap: there
    is nothing to derive from, so it stays a source finding for MyGS1 (E23) rather than becoming
    invented product data. That line is the whole reason this is defensible — rendering a value
    the feed already holds into a second language is translation; writing one that exists nowhere
    is invention, and this tool does not do it.

    The source language is the default language when it carries the value, else the first
    configured language that does — so the translation is made from the feed's authored text
    rather than from another translation, wherever there is a choice.

    Args:
        product: The record to inspect (pre-merge — the feed's own values).
        language: The language being generated for.
        context: The client context; its ``translatable`` list is what may be filled at all.

    Returns:
        One gap per missing value, in ``context.translatable`` order. Empty when nothing is missing,
        nothing is translatable, or every candidate is blank everywhere.
    """
    gaps: list[TranslationGap] = []
    for candidate in context.translatable:
        values = _field_values(product, candidate.field, context.default_language)
        if language in values:
            continue
        ranked = [context.default_language, *context.languages]
        source_language = next((lang for lang in ranked if lang in values), None)
        if source_language is None:
            # Blank in every configured language — the guard rail. There is nothing to derive
            # from, so this stays a source finding for MyGS1 (E23) rather than becoming an
            # invented value. Also covers a value carried only in a language nobody publishes.
            continue
        gaps.append(
            TranslationGap(
                field=candidate.field,
                source_language=source_language,
                source_value=values[source_language],
                source_label=candidate.source_label,
                has_language_slot=candidate.has_language_slot,
            )
        )
    return gaps


def _gather_inputs(
    product: ProductRecord, language: str, context: GenerationContext, gaps: list[TranslationGap]
) -> GenerationInputs:
    """Assemble the generation inputs for one ``(gtin, language)`` from the record."""
    return GenerationInputs(
        # Named for the producer's vocabulary — the prompt and the content-generator skill both
        # call it the functional name — but read from ``product_name``, which *is* attr 3301. It
        # used to read an ``extras.functional_name`` declared against that same attribute, with
        # the mapped field as a fallback: one value under two names, and this was the read that
        # made the duplication look load-bearing. The default-language fallback is the part worth
        # keeping, and is now a stated rule rather than a side effect of how extras were stored: a
        # unit with no name in its own language is seeded with the Dutch one, because that is what
        # the copy describes.
        functional_name=product.product_name.get(language, context.default_language),
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
        material=_material(product, language, context.default_language),
        translation_sources={gap.field: gap.source_value for gap in gaps},
    )


def _prepare_unit(
    product: ProductRecord, language: str, context: GenerationContext
) -> tuple[GenerationInputs, list[TranslationGap], str]:
    """The inputs, translation gaps, and fingerprint for one ``(gtin, language)``.

    One function because the three are computed together everywhere: the gaps feed the inputs,
    and the inputs are what the fingerprint is over.
    """
    gaps = translation_gaps(product, language, context)
    inputs = _gather_inputs(product, language, context, gaps)
    return inputs, gaps, _fingerprint(inputs, language, context.prompt_version)


def _feature_candidates(inputs: GenerationInputs) -> list[str]:
    """Split the joined 1067 feature/benefit text into candidate USPs (newline-separated)."""
    if not inputs.feature_benefit:
        return []
    return [line.strip() for line in inputs.feature_benefit.split("\n") if line.strip()]


def _all_short(candidates: list[str]) -> bool:
    """Whether every candidate USP is short enough to use verbatim."""
    return all(len(c) <= MAX_VERBATIM_USP_CHARS for c in candidates)


def _is_fresh(cache: GeneratedCache, gtin: str, language: str, fingerprint: str) -> bool:
    """Whether the cache already holds an entry matching the current input fingerprint."""
    entry = cache.get(gtin, language)
    return entry is not None and entry.input_fingerprint == fingerprint


def prefill_from_feed(
    products: list[ProductRecord],
    cache: GeneratedCache,
    context: GenerationContext,
    *,
    now: datetime,
) -> None:
    """Fill the cache in place for units whose 1067 USPs are short enough to use verbatim.

    Deterministic and network-free: when the feed carries feature/benefit copy (attr 1067) and
    every entry is within :data:`MAX_VERBATIM_USP_CHARS`, that copy *is* the ranked USP list, so no
    producer is needed. Longer 1067 and absent 1067 are left for :func:`pending_requests`. Skips
    units already fresh in the cache. Run this before ``pending_requests``.

    A unit prefilled here still records its translation gaps on the request, so the entry says
    which values were missing — but it fills none of them: no producer ran, and the feed's 1067
    is copy, not a translation of anything.
    """
    for product in products:
        for language in context.languages:
            inputs, gaps, fingerprint = _prepare_unit(product, language, context)
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
                translations=gaps,
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
    context: GenerationContext,
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
        context: The client context — languages, prompt version, and what may be translated.

    Returns:
        The pending requests, each carrying its inputs, fingerprint, mode, 1067 candidates, and
        the language gaps the producer must translate.
    """
    requests: list[GenerationRequest] = []
    for product in products:
        for language in context.languages:
            inputs, gaps, fingerprint = _prepare_unit(product, language, context)
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
                    translations=gaps,
                    mode=mode,
                    candidates=candidates,
                )
            )
    return requests


def _clean_bullets(bullets: list[str]) -> list[str]:
    """Strip each bullet and drop the empties."""
    return [stripped for stripped in (b.strip() for b in bullets) if stripped]


def _requested_translations(request: GenerationRequest, result: GenerationResult) -> dict[str, str]:
    """The result's translations, narrowed to the fields the request actually asked for.

    The write-side half of "the feed always wins": a producer that volunteers a value for a
    language the feed already carries has it dropped here, before it can reach the cache.
    :func:`merge_generated` enforces the same rule again on read, because one guard on a path
    this quiet is one deploy away from being the only guard.
    """
    asked = {gap.field for gap in request.translations}
    return {
        field: value.strip()
        for field, value in result.translations.items()
        if field in asked and value.strip()
    }


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
        provenance: Which producer made it, e.g. ``"api:claude-sonnet-5"`` or ``"in-session"``.
        now: The generation timestamp (injected for determinism).

    Raises:
        GeneratorError: If the result has no usable USPs after cleaning.
    """
    usps = _clean_bullets(result.usps)
    if not usps:
        raise GeneratorError(
            f"empty generation result for {request.gtin}/{request.language}: usps={result.usps!r}"
        )
    source_input = (
        request.inputs.feature_benefit
        or request.inputs.marketing_message
        or request.inputs.functional_name
        or ""
    )
    entry = CacheEntry(
        usps=usps,
        translations=_requested_translations(request, result),
        origin=origin,
        input_fingerprint=request.input_fingerprint,
        provenance=provenance,
        source_input=source_input,
        generated_at=now,
        inferences=list(result.inferences),
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

    Net content and dimensions decode their unit codes per language. Material is one
    language-agnostic value in the feed (attr 4.012 has no ``LanguageCode`` pair), so it renders
    in whatever language it was authored in until the generator has translated it — with
    ``translate: true`` set on it, this reads the translation once one exists, and falls back to
    the authored word until then.
    """
    bullets: list[str] = []
    net_content = decode_net_content(product.net_content, language, fallback_language=fallback)
    if net_content:
        bullets.append(net_content)
    dimensions = _dimensions_bullet(product, language, fallback)
    if dimensions:
        bullets.append(dimensions)
    material = _material(product, language, fallback)
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


def _inference_issues(gtin: str, language: str, entry: CacheEntry) -> list[SourceIssue]:
    """One :class:`SourceIssue` per claim the producer inferred beyond the feed text.

    Inferences are honest-but-derived claims (e.g. "snoerloos" from "batterie rechargeable"):
    plausible, but not literally in attr 1083/1067. Each is surfaced so a human confirms it is
    true for this product before the copy goes live — a claim that turns out false is a defect
    to fix at source, exactly like the other generated-content findings.
    """
    return [
        SourceIssue(
            gtin=gtin,
            field=f"generated_description.{language}",
            source="inferred beyond feed copy (attr 1083/1067)",
            issue="generation_inference",
            value=claim,
            detail=(
                f"'{claim}' was inferred for {language} beyond the literal feed copy; "
                "verify it is true for this product before publishing."
            ),
        )
        for claim in entry.inferences
    ]


def _translated_issue(gtin: str, language: str, gap: TranslationGap, value: str) -> SourceIssue:
    """Report one value the tool rendered into a language the feed did not carry it in.

    The point of the finding is the feedback loop, not the confession: ``value`` is the text to
    paste into MyGS1, so the next export carries it for real and the tool stops writing it. A
    language-agnostic attribute has nowhere to paste it, and the detail says so rather than
    sending the operator looking for a field that does not exist.
    """
    destination = (
        f"add it to {gap.source_label} for {language} in MyGS1"
        if gap.has_language_slot
        else f"{gap.source_label} is language-agnostic in GS1, so there is no {language} slot "
        "to hold this — it stays a page-only translation"
    )
    # The caveat rides on `source` as well as in the detail, because the report's table renders
    # the source and not the detail: a row saying only "attr Material" sends the operator into
    # MyGS1 looking for a French field that does not exist.
    source = (
        gap.source_label
        if gap.has_language_slot
        else f"{gap.source_label} — no per-language slot in GS1"
    )
    return SourceIssue(
        gtin=gtin,
        field=f"{gap.field}.{language}",
        source=source,
        issue="value_translated",
        value=value,
        detail=(
            f"{gap.field} was written for {language} by translating the {gap.source_language} "
            f"value ('{gap.source_value}'), which the feed carries; {destination}."
        ),
    )


class _UnitRead(NamedTuple):
    """What one ``(gtin, language)`` contributed: its fresh cache entry, if any, and its gaps."""

    entry: CacheEntry | None
    gaps: list[TranslationGap]
    inputs: GenerationInputs


def _read_cache(
    product: ProductRecord, cache: GeneratedCache, context: GenerationContext
) -> tuple[dict[str, _UnitRead], dict[str, dict[str, str]], list[SourceIssue]]:
    """Read one product's cache entries, collecting its filled values and their findings.

    Returns the per-language reads, the filled values as ``field → language → value``, and one
    ``value_translated`` finding per filled value plus one ``missing_generation_input`` per
    language whose marketing message is blank **and not about to be filled** — a value the feed
    carries in another language is not a missing input, it is a pending translation.
    """
    reads: dict[str, _UnitRead] = {}
    filled: dict[str, dict[str, str]] = {}
    issues: list[SourceIssue] = []
    for language in context.languages:
        inputs, gaps, fingerprint = _prepare_unit(product, language, context)
        entry = cache.get(product.gtin, language)
        if entry is not None and entry.input_fingerprint != fingerprint:
            entry = None  # stale — a feed edit superseded it
        reads[language] = _UnitRead(entry, gaps, inputs)

        for gap in gaps:
            value = entry.translations.get(gap.field) if entry is not None else None
            if value:
                filled.setdefault(gap.field, {})[language] = value
                issues.append(_translated_issue(product.gtin, language, gap, value))

        pending = {gap.field for gap in gaps}
        if not inputs.marketing_message and _MARKETING_MESSAGE_FIELD not in pending:
            issues.append(_missing_input_issue(product.gtin, language, inputs))
    return reads, filled, issues


def _apply_translations(
    product: ProductRecord, filled: dict[str, dict[str, str]], default_language: str
) -> ProductRecord:
    """Return ``product`` with each filled value merged in, the feed's own values winning.

    The read-side half of "the feed always wins". :func:`_requested_translations` is the other and
    the one every real input hits first, so no current input reaches this one — it is kept as the
    guard that still holds if the write side ever changes, not as live logic.
    """
    if not filled:
        return product
    update: dict[str, object] = {}
    localised_extras = dict(product.extras_localised)
    for field, by_language in filled.items():
        current = _stored_values(product, field, default_language)
        merged = {**by_language, **current}  # feed values overwrite the filled ones
        if field in type(product).model_fields:
            update[field] = LocalisedText(values=merged)
        else:
            localised_extras[field] = LocalisedText(values=merged)
    if localised_extras != product.extras_localised:
        update["extras_localised"] = localised_extras
    return product.model_copy(update=update) if update else product


def _stored_values(product: ProductRecord, field: str, default_language: str) -> dict[str, str]:
    """The record's own per-language values for ``field``, in the shape they are stored in.

    A flat extra counts as carrying its one value in the default language — the same reading
    :func:`_field_values` uses to decide it is missing elsewhere. Returning nothing for it instead
    dropped the authored value the moment another language was filled: translating `material` into
    French took `Materiaal: kunststof` off the *Dutch* page, because the per-language mapping that
    replaced the flat value held only French.
    """
    localised = product.extras_localised.get(field)
    if localised is not None:
        return dict(localised.values)
    flat = product.extras.get(field)
    if flat is not None:
        return {default_language: flat}
    value = getattr(product, field, None)
    return dict(value.values) if isinstance(value, LocalisedText) else {}


def merge_generated(
    products: list[ProductRecord],
    cache: GeneratedCache,
    context: GenerationContext,
) -> tuple[list[ProductRecord], list[SourceIssue]]:
    """Fold cached generated copy and filled language gaps onto each record, reporting both.

    Pure and network-free. For each product it fills every translatable value the feed lacks in a
    configured language, overwrites ``product_name`` with the variation-combined title, sets
    ``generated_tagline`` (the first cached USP), and — when the cache holds usable Eigenschappen
    for the current inputs — sets ``generated_description`` to the assembled three-part HTML. A
    stale or missing cache entry simply yields no description for that language; the run_plan E18
    backstop handles the resulting gap. Called before ``diff_against_state`` so generated content
    is part of the content hash.

    The fill runs before the title is combined and before the description is assembled, so a
    translated variation suffixes the translated title and a translated material reaches the
    Technische-details block, rather than each being one step too late.

    Args:
        products: The parsed products.
        cache: The generated-copy cache.
        context: The client context — languages, default language, prompt version, and what
            may be translated.

    Returns:
        The products with generated and filled fields materialised, and one :class:`SourceIssue`
        per generated value and per filled value.
    """
    merged: list[ProductRecord] = []
    issues: list[SourceIssue] = []
    for product in products:
        reads, filled, product_issues = _read_cache(product, cache, context)
        issues.extend(product_issues)
        record = _apply_translations(product, filled, context.default_language)
        record, copy_issues = _apply_copy(record, reads, context)
        issues.extend(copy_issues)
        merged.append(record)
    return merged, issues


def _apply_copy(
    product: ProductRecord, reads: dict[str, _UnitRead], context: GenerationContext
) -> tuple[ProductRecord, list[SourceIssue]]:
    """Combine each language's title and assemble its generated copy from the cache reads."""
    issues: list[SourceIssue] = []
    names = dict(product.product_name.values) if product.product_name else {}
    taglines: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    for language in context.languages:
        entry = reads[language].entry
        base = names.get(language)
        if base is not None:
            variation = product.extra("product_variation", language)
            names[language] = _combine_title(base, variation)
        if entry is None or not entry.usps:
            continue
        taglines[language] = entry.usps[0]
        descriptions[language] = _assemble_description(
            entry.usps, product, language, context.default_language
        )
        issue = _content_issue(product.gtin, language, entry)
        if issue is not None:
            issues.append(issue)
        issues.extend(_inference_issues(product.gtin, language, entry))

    update: dict[str, object] = {}
    if product.product_name is None or names != product.product_name.values:
        update["product_name"] = LocalisedText(values=names)
    if taglines:
        update["generated_tagline"] = LocalisedText(values=taglines)
    if descriptions:
        update["generated_description"] = LocalisedText(values=descriptions)
    return (product.model_copy(update=update) if update else product), issues
