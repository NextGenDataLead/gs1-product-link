"""Which products the plan will hold, asked before there is a plan — E23, E24, E22.

Three of the plan's skip rules drop a **whole product** rather than one unit, and none of them
depends on prior state or on generated copy: mandatory source data missing (E23), no
client-confirmed video in every language (E24), and a blank hero image (E22). That makes them
decidable from configuration, the parsed products and the video map alone — which is what lets a
caller running *before* the plan ask "will this product be skipped whatever I do?" and stop paying
for work nobody will read.

``run_generate`` is that caller. It asked a producer for every in-scope unit a run would create or
change, and on the pilot client that was **74 units across 37 GTINs of which 10 GTINs could
publish** — 17 held for want of a confirmed video, 10 for missing mandatory data. So ~73% of the
batch was copy for products the plan then skipped. That is the failure ``run_generate._prepare``
already records one gate earlier ("224 requests emitted where 10 were in scope"): real tokens spent
on products nobody is publishing, and a content-review gate three times larger than the work in it,
which is the surest way to make a review gate go unread.

**Every predicate here is the plan's own.** :func:`lib.mandatory.missing_mandatory` over
:attr:`~lib.config.ExportConfig.all_sources` for E23, :func:`lib.media_video.fully_mapped_gtins`
for E24, and the same blank-``image_url`` test for E22. A hand-rolled second opinion about what
``required`` means is exactly how "E23 means blank dimensions" came to be believed, and E23 decides
whether a SKU may publish at all. :func:`lib.state.diff_against_state` still *applies* these rules;
this module says what they are going to decide.

**E23 is asked of what generation cannot fix.** The plan runs its mandatory check on the
*post-generation* records, so a value the feed carries in one language and the client has marked
``translate: true`` is no longer a gap by the time E23 is reached — the producer fills it. Asking
the pre-generation record alone would hold exactly the units whose gap the generation was going to
close, and a unit held for want of copy nobody was asked to write is a page that never publishes.
So a gap counts here only when :func:`lib.generator.translation_gaps` — the same function that
decides what the producer is asked to translate — offers nothing to derive it from.

**E18 and E21 are deliberately absent**, for the reason :func:`lib.state.classify_units` gives:
both are per unit and both sit downstream of generation. E21 holds a unit with no generated
tagline, which before generation is every unit; E18 holds a language with no ``product_name``,
which is one of the values the producer supplies. Asked here, each would hold the unit that was
about to close it.

Nothing here reads ``state.json``, so an idle call cannot quarantine a corrupt one (E19).
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from lib.generator import generation_context, translation_gaps
from lib.mandatory import missing_mandatory
from lib.media_video import canon_gtin, fully_mapped_gtins, load_video_map
from lib.records import SkipReason

if TYPE_CHECKING:
    from lib.config import ClientConfig
    from lib.gdsn import GdsnSource
    from lib.generator import GenerationContext
    from lib.mandatory import MandatoryGap
    from lib.records import ProductRecord


def confirmed_video_gtins(cfg: ClientConfig) -> frozenset[str] | None:
    """GTINs with a client-confirmed video in every language, or ``None`` when unrestricted.

    ``None`` disables the E24 hold entirely, which is what a client without
    ``media.restrict_to_mapped_gtins`` wants — not an empty set, which would hold everything.

    Raises:
        VideoMapError: If the configured video map cannot be read. ``run_plan`` lets that stop the
            run; a caller that must not fail over a diagnostic — the doctor — catches it.
    """
    media = cfg.media
    if media is None or not media.restrict_to_mapped_gtins or not media.video_map_path:
        return None
    return fully_mapped_gtins(load_video_map(Path(media.video_map_path)), cfg.wordpress.languages)


def held_units(
    cfg: ClientConfig, products: list[ProductRecord]
) -> dict[tuple[str, str], SkipReason]:
    """Every ``(GTIN, language)`` the plan will hold whatever a producer writes, and which rule.

    Keyed by unit rather than by product because the plan's unit of work is ``(GTIN, language)``,
    and a count in any other unit cannot be compared with the row counts beside it — the same
    reason :func:`lib.state.diff_against_state` records each of these three per language, even
    though all three drop the whole product.

    Args:
        cfg: The client config; supplies the mandatory sources, the video map and
            ``media.require_hero_image``.
        products: The products to ask about — already narrowed to this run's scope by
            :func:`lib.preflight.in_scope`.

    Returns:
        ``(gtin, language) -> reason`` for each held unit; empty when nothing is held. The reason
        is the first rule that fires, in ``diff_against_state``'s order (E23, E24, E22), so a
        product failing two is attributed the way the plan will attribute it.

    Raises:
        VideoMapError: See :func:`confirmed_video_gtins`.
    """
    video = confirmed_video_gtins(cfg)
    sources = cfg.export.all_sources
    languages = cfg.wordpress.languages
    require_hero = cfg.media is not None and cfg.media.require_hero_image
    # ``None`` for a client with no generator, because ``run_plan._generate_content`` returns the
    # records untouched for one: nothing is filled, so every gap is unfillable. ``translate`` is a
    # property of the *source*, not of the generator block, so reading it either way would let a
    # flag mean something here that it means nowhere else. ``prompt_version`` is part of a
    # fingerprint and never of a gap, so any value would do — this asks for the real one anyway.
    context = (
        generation_context(
            languages,
            cfg.wordpress.default_language,
            cfg.generator.prompt_version,
            cfg.export.gdsn_map,
            cfg.export.gdsn_extras,
        )
        if cfg.generator is not None
        else None
    )

    held: dict[tuple[str, str], SkipReason] = {}
    for product in products:
        if _unfillable_gaps(product, sources, languages, context):  # E23
            reason = SkipReason.MISSING_MANDATORY_FIELD
        elif video is not None and canon_gtin(product.gtin) not in video:  # E24
            reason = SkipReason.NO_CONFIRMED_VIDEO
        elif require_hero and not (product.image_url or "").strip():  # E22
            reason = SkipReason.BLANK_HERO_IMAGE
        else:
            continue
        held.update({(product.gtin, language): reason for language in languages})
    return held


def _unfillable_gaps(
    product: ProductRecord,
    sources: dict[str, GdsnSource],
    languages: list[str],
    context: GenerationContext | None,
) -> list[MandatoryGap]:
    """The E23 gaps generation cannot close — the ones that hold ``product`` for certain.

    A gap is closable only by *translation*: the producer never invents a value the feed holds
    nowhere, which is the line :func:`lib.generator.translation_gaps` draws and the whole reason
    filling anything is defensible. So a gap survives unless some field that would satisfy it is
    one the producer will be asked to render into that language.

    A language-agnostic gap (``language == ""``) always survives: the field has one slot, that
    slot is empty, and there is no sibling language to derive it from — which is what
    ``translation_gaps`` concludes too, reached without asking it about a language it has no
    meaning for.

    Args:
        product: The record as the feed defines it — before any generated value is merged in,
            which is the whole question being asked.
        sources: The client's :attr:`~lib.config.ExportConfig.all_sources`, both maps.
        languages: The configured site languages.
        context: The generation context, or ``None`` for a client with no generator — which
            closes nothing, so every gap survives.
    """
    gaps = missing_mandatory(product, sources, languages)
    if not gaps or context is None:
        return gaps

    # A ``required_group`` gap is named for the group, so it has to be resolved back to its members
    # before asking: any one member the producer translates satisfies the whole group.
    members: dict[str, set[str]] = defaultdict(set)
    for field, source in sources.items():
        if source.required_group:
            members[source.required_group].add(field)

    fillable: dict[str, set[str]] = {}
    unfillable: list[MandatoryGap] = []
    for gap in gaps:
        if not gap.language:
            unfillable.append(gap)
            continue
        if gap.language not in fillable:
            asked = translation_gaps(product, gap.language, context)
            fillable[gap.language] = {candidate.field for candidate in asked}
        if not fillable[gap.language] & members.get(gap.field, {gap.field}):
            unfillable.append(gap)
    return unfillable
