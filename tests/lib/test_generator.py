"""Unit tests for the content-generator core (``lib.generator``).

Pure and network-free: exercises the cache, the request/result contract, and the deterministic
merge. No LLM is involved — a producer's output is simulated by constructing
:class:`~lib.generator.GenerationResult` directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lib.errors import GeneratorError
from lib.gdsn import GdsnSource
from lib.generator import (
    MODE_GENERATE,
    MODE_TIGHTEN,
    ORIGIN_GENERATED,
    ORIGIN_TIGHTENED,
    GeneratedCache,
    GenerationContext,
    GenerationResult,
    TranslatableField,
    _combine_title,
    apply_result,
    generation_context,
    load_cache,
    merge_generated,
    pending_requests,
    prefill_from_feed,
    save_cache,
    translation_gaps,
)
from lib.records import LocalisedText, ProductRecord

_NOW = datetime(2026, 7, 18, tzinfo=UTC)

#: The pilot client's `translate: true` set, in config order — mapped fields then extras.
#: `material` is language-agnostic in GS1, so it has no slot a filled value could go back into.
_TRANSLATABLE = [
    TranslatableField(field="product_name", source_label="TradeItemDescription attr 3301"),
    TranslatableField(field="description_short", source_label="MarketingInformation attr 1083"),
    TranslatableField(field="product_variation", source_label="TradeItemDescription attr 3332"),
    TranslatableField(
        field="material",
        source_label="BrickGPCCommercialData attr Material",
        has_language_slot=False,
    ),
]


def _ctx(*languages: str, translatable: list[TranslatableField] | None = None) -> GenerationContext:
    """A client context for the tests: the given languages, nl default, prompt v1."""
    return GenerationContext(
        languages=list(languages),
        default_language="nl",
        prompt_version="v1",
        translatable=_TRANSLATABLE if translatable is None else translatable,
    )


def _product(**overrides: object) -> ProductRecord:
    base: dict[str, object] = {
        "gtin": "08713195007359",
        "brand": "Noviplast",
        "product_name": LocalisedText(values={"nl": "Bewateringpin"}),
        "net_content": "6 H87",
        "description_short": LocalisedText(values={"nl": "Water voor je planten"}),
        "extras": {
            "dim_height": "350 MMT",
            "dim_width": "250 MMT",
            "dim_depth": "80 MMT",
            "material": "kunststof",
        },
    }
    base.update(overrides)
    return ProductRecord(**base)


def _result(
    *usps: str, translations: dict[str, str] | None = None, inferences: list[str] | None = None
) -> GenerationResult:
    return GenerationResult(
        usps=list(usps), translations=translations or {}, inferences=inferences or []
    )


# --- title combiner ----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "variation", "expected"),
    [
        ("Snoeischaar", "Snoeischaar", "Snoeischaar"),  # exact duplicate → dedup
        ("Snoeischaar", "snoeischaar", "Snoeischaar"),  # case-insensitive dedup
        ("Emmer", "Set", "Emmer Set"),  # genuine variation → appended
        ("Bewateringpin", None, "Bewateringpin"),  # no variation → unchanged
        ("Bewateringpin", "", "Bewateringpin"),  # empty variation → unchanged
    ],
)
def test_combine_title(name: str, variation: str | None, expected: str) -> None:
    assert _combine_title(name, variation) == expected


# --- pending_requests --------------------------------------------------------


def test_pending_requests_flags_every_language_when_cache_empty() -> None:
    cache = GeneratedCache(client_id="noviplast")
    requests = pending_requests([_product()], cache, _ctx("nl", "fr"))

    assert {r.language for r in requests} == {"nl", "fr"}
    assert requests[0].inputs.marketing_message == "Water voor je planten"


def test_pending_requests_skips_when_fingerprint_matches() -> None:
    cache = GeneratedCache(client_id="noviplast")
    product = _product()
    request = next(r for r in pending_requests([product], cache, _ctx("nl")))
    apply_result(
        cache,
        request,
        _result("Tagline", "Bullet"),
        origin=ORIGIN_GENERATED,
        provenance="in-session",
        now=_NOW,
    )

    assert pending_requests([product], cache, _ctx("nl")) == []


def test_pending_requests_asks_the_producer_to_fill_each_language_gap() -> None:
    product = _product()  # nl only, in product_name / description_short / material
    requests = pending_requests([product], GeneratedCache(client_id="noviplast"), _ctx("fr"))

    gaps = {gap.field: gap for gap in requests[0].translations}
    assert set(gaps) == {"product_name", "description_short", "material"}
    # Each gap carries the text to translate from, not just the field name: the producer works
    # from it in the same call it writes the copy.
    assert gaps["product_name"].source_language == "nl"
    assert gaps["product_name"].source_value == "Bewateringpin"
    assert gaps["description_short"].source_value == "Water voor je planten"


def test_a_value_present_in_this_language_is_not_a_gap() -> None:
    product = _product(product_name=LocalisedText(values={"nl": "Bewateringpin", "fr": "Pic"}))

    fields = {gap.field for gap in translation_gaps(product, "fr", _ctx("nl", "fr"))}

    assert "product_name" not in fields


def test_a_value_blank_in_every_language_is_never_filled() -> None:
    """The line between translating and inventing, and the reason this is defensible at all.

    Rendering a value the feed already holds into a second language is translation. Writing one
    that exists in no language would be invention — it stays a source finding for MyGS1 (E23).
    """
    product = _product(description_short=None, extras={})

    fields = {gap.field for gap in translation_gaps(product, "fr", _ctx("nl", "fr"))}

    assert fields == {"product_name"}  # the only value this product carries at all


def test_a_datapool_placeholder_is_not_something_to_translate() -> None:
    """`zzzanders` is the feed's way of saying "no value", and the generator already knows it.

    Reading it as a value to translate asks the producer to render a placeholder into French and
    then tells the operator to paste that back into MyGS1 — turning a blank into fabricated master
    data, which is the exact failure the guard rail exists to prevent. Found in a real run: one
    in-scope product carries it.
    """
    product = _product(extras={"material": "zzzanders"})

    fields = {gap.field for gap in translation_gaps(product, "fr", _ctx("nl", "fr"))}

    assert "material" not in fields


def test_the_context_selects_exactly_the_sources_marked_translate() -> None:
    """`localised` is not the selector — `translate` is, and only where a client set it.

    `logistics_name` and `marketing_name` are localised and consumed by nothing, so deriving the
    list from `localised` would spend producer tokens on values no page reads. It also has to pick
    up `material`, which is language-agnostic in the feed and so could never be selected that way.
    """
    context = generation_context(
        ["nl", "fr"],
        "nl",
        "v1",
        {
            "product_name": GdsnSource(sheet="S", attribute="3301", localised=True, translate=True),
            "brand": GdsnSource(sheet="S", attribute="3336"),
        },
        {
            "logistics_name": GdsnSource(sheet="S", attribute="3297", localised=True),
            "material": GdsnSource(sheet="B", attribute="Material", translate=True),
        },
    )

    assert [f.field for f in context.translatable] == ["product_name", "material"]
    assert context.translatable[0].source_label == "S attr 3301"
    # A per-language GDSN attribute has a slot the filled value can go back into; 4.012 has not.
    assert context.translatable[0].has_language_slot is True
    assert context.translatable[1].has_language_slot is False


def test_a_field_not_opted_in_is_never_filled() -> None:
    # `logistics_name`/`marketing_name` are carried and consumed by nothing; filling them would
    # be producer tokens spent on a value no page reads.
    product = _product()

    gaps = translation_gaps(product, "fr", _ctx("nl", "fr", translatable=[]))

    assert gaps == []


# --- apply_result ------------------------------------------------------------


def test_apply_result_stores_entry() -> None:
    cache = GeneratedCache(client_id="noviplast")
    request = next(r for r in pending_requests([_product()], cache, _ctx("nl")))

    apply_result(
        cache,
        request,
        _result("Tagline", "Bullet"),
        origin=ORIGIN_GENERATED,
        provenance="in-session",
        now=_NOW,
    )

    entry = cache.get("08713195007359", "nl")
    assert entry is not None
    assert entry.usps == ["Tagline", "Bullet"]
    assert entry.provenance == "in-session"


def test_apply_result_rejects_empty_usps() -> None:
    cache = GeneratedCache(client_id="noviplast")
    request = next(r for r in pending_requests([_product()], cache, _ctx("nl")))

    with pytest.raises(GeneratorError, match="empty generation result"):
        apply_result(
            cache,
            request,
            _result("   "),
            origin=ORIGIN_GENERATED,
            provenance="in-session",
            now=_NOW,
        )


def test_apply_result_stores_inferences() -> None:
    cache = GeneratedCache(client_id="noviplast")
    request = next(r for r in pending_requests([_product()], cache, _ctx("nl")))

    apply_result(
        cache,
        request,
        _result("Tagline", "Bullet", inferences=["snoerloos"]),
        origin=ORIGIN_GENERATED,
        provenance="in-session",
        now=_NOW,
    )

    entry = cache.get("08713195007359", "nl")
    assert entry is not None
    assert entry.inferences == ["snoerloos"]


def test_apply_result_defaults_inferences_to_empty() -> None:
    cache = GeneratedCache(client_id="noviplast")
    request = next(r for r in pending_requests([_product()], cache, _ctx("nl")))

    apply_result(
        cache,
        request,
        _result("Tagline"),
        origin=ORIGIN_GENERATED,
        provenance="in-session",
        now=_NOW,
    )

    entry = cache.get("08713195007359", "nl")
    assert entry is not None
    assert entry.inferences == []


# --- merge_generated ---------------------------------------------------------


def _merge_one(product: ProductRecord, *usps: str, **kw: object) -> ProductRecord:
    """Generate for ``product`` (nl) and return the merged record."""
    cache = GeneratedCache(client_id="noviplast")
    request = next(r for r in pending_requests([product], cache, _ctx("nl")))
    apply_result(
        cache, request, _result(*usps), origin=ORIGIN_GENERATED, provenance="in-session", now=_NOW
    )
    merged, _ = merge_generated([product], cache, _ctx("nl"))
    return merged[0]


def test_merge_assembles_the_three_part_description() -> None:
    merged = _merge_one(
        _product(), "Alle kabels perfect weggewerkt!", "Klem om te bundelen", "Op maat te knippen"
    )

    assert merged.generated_tagline is not None
    assert merged.generated_tagline.get("nl") == "Alle kabels perfect weggewerkt!"
    html = merged.generated_description.get("nl")
    assert html is not None
    assert "<p><strong>Alle kabels perfect weggewerkt!</strong></p>" in html
    # usps[1:] become Eigenschappen; usps[0] does not reappear as a bullet
    eigenschappen = (
        "<strong>Eigenschappen</strong><br />• Klem om te bundelen<br />• Op maat te knippen"
    )
    assert eigenschappen in html
    assert "• Alle kabels perfect weggewerkt!" not in html
    # Technische details is deterministic from feed data
    assert "<strong>Technische details</strong><br />• 6 Stuk" in html
    assert "Afmetingen: 350 × 250 × 80 Millimeter" in html
    assert "Materiaal: kunststof" in html


def test_merge_reports_one_generated_issue_with_source_input() -> None:
    cache = GeneratedCache(client_id="noviplast")
    product = _product()
    request = next(r for r in pending_requests([product], cache, _ctx("nl")))
    apply_result(
        cache,
        request,
        _result("Tagline", "Bullet"),
        origin=ORIGIN_GENERATED,
        provenance="in-session",
        now=_NOW,
    )

    _, issues = merge_generated([product], cache, _ctx("nl"))

    generated = [i for i in issues if i.issue == "content_generated"]
    assert len(generated) == 1
    assert generated[0].field == "generated_description.nl"
    assert generated[0].value == "Water voor je planten"  # the source-language input


def test_merge_reports_one_generation_inference_per_claim() -> None:
    cache = GeneratedCache(client_id="noviplast")
    product = _product()
    request = next(r for r in pending_requests([product], cache, _ctx("nl")))
    apply_result(
        cache,
        request,
        _result("Tagline", "Bullet", inferences=["snoerloos", "oplaadbaar"]),
        origin=ORIGIN_GENERATED,
        provenance="in-session",
        now=_NOW,
    )

    _, issues = merge_generated([product], cache, _ctx("nl"))

    inferred = [i for i in issues if i.issue == "generation_inference"]
    assert len(inferred) == 2
    assert {i.value for i in inferred} == {"snoerloos", "oplaadbaar"}
    assert all(i.field == "generated_description.nl" for i in inferred)


def test_merge_reports_no_inference_issue_when_none_declared() -> None:
    cache = GeneratedCache(client_id="noviplast")
    product = _product()
    request = next(r for r in pending_requests([product], cache, _ctx("nl")))
    apply_result(
        cache,
        request,
        _result("Tagline", "Bullet"),
        origin=ORIGIN_GENERATED,
        provenance="in-session",
        now=_NOW,
    )

    _, issues = merge_generated([product], cache, _ctx("nl"))

    assert [i for i in issues if i.issue == "generation_inference"] == []


def test_merge_ignores_stale_entry_when_feed_changed() -> None:
    # A cached entry keyed on old inputs must not be used after a feed edit (supersession).
    cache = GeneratedCache(client_id="noviplast")
    old = _product(description_short=LocalisedText(values={"nl": "Oud"}))
    request = next(r for r in pending_requests([old], cache, _ctx("nl")))
    apply_result(
        cache,
        request,
        _result("Tagline", "Bullet"),
        origin=ORIGIN_GENERATED,
        provenance="in-session",
        now=_NOW,
    )

    edited = _product(description_short=LocalisedText(values={"nl": "Nieuw"}))
    merged, _ = merge_generated([edited], cache, _ctx("nl"))

    assert merged[0].generated_description is None  # stale entry ignored


def test_merge_flags_a_marketing_message_blank_in_every_language() -> None:
    product = _product(description_short=None)  # no 1083 anywhere
    _, issues = merge_generated([product], GeneratedCache(client_id="noviplast"), _ctx("nl"))

    flags = [i for i in issues if i.issue == "missing_generation_input"]
    assert len(flags) == 1
    assert flags[0].field == "description_short.nl"


def test_a_marketing_message_the_feed_has_in_another_language_is_not_a_missing_input() -> None:
    """It is a pending translation, and reporting it as a datapool gap sends the wrong work.

    Before this, every French unit whose 1083 was Dutch-only was reported held — asking the
    operator to write French marketing copy into MyGS1 for a product that already had it in
    Dutch.
    """
    product = _product()  # 1083 in nl only
    _, issues = merge_generated([product], GeneratedCache(client_id="noviplast"), _ctx("nl", "fr"))

    assert [i for i in issues if i.issue == "missing_generation_input"] == []


def test_merge_combines_title_with_variation() -> None:
    product = _product(
        product_name=LocalisedText(values={"nl": "Emmer"}),
        extras_localised={"product_variation": LocalisedText(values={"nl": "Set"})},
    )
    merged, _ = merge_generated([product], GeneratedCache(client_id="noviplast"), _ctx("nl"))

    assert merged[0].product_name.get("nl") == "Emmer Set"


def _fill_one(product: ProductRecord, language: str, **translations: str) -> ProductRecord:
    """Generate for one language, answering its gaps, and return the merged record."""
    cache = GeneratedCache(client_id="noviplast")
    request = next(r for r in pending_requests([product], cache, _ctx(language)))
    apply_result(
        cache,
        request,
        _result("Slogan", "Puce", translations=translations),
        origin=ORIGIN_GENERATED,
        provenance="in-session",
        now=_NOW,
    )
    merged, _ = merge_generated([product], cache, _ctx(language))
    return merged[0]


def test_merge_fills_a_missing_name_from_the_cache() -> None:
    product = _product(product_name=LocalisedText(values={"nl": "Bewateringpin"}))  # no fr

    merged = _fill_one(product, "fr", product_name="Pic d'arrosage")

    assert merged.product_name.get("fr") == "Pic d'arrosage"


def test_merge_fills_a_missing_localised_extra() -> None:
    product = _product(extras_localised={"product_variation": LocalisedText(values={"nl": "Set"})})

    merged = _fill_one(product, "fr", product_variation="Ensemble", product_name="Pic")

    assert merged.extra("product_variation", "fr") == "Ensemble"
    # …and it suffixes the *translated* title, not the Dutch one, because the fill runs first.
    assert merged.product_name.get("fr") == "Pic Ensemble"


def test_merge_fills_a_language_agnostic_extra_so_the_page_stops_reading_dutch() -> None:
    """`Matériau: kunststof` on a French page is the failure this closes."""
    merged = _fill_one(_product(), "fr", material="plastique", product_name="Pic")

    assert merged.extra("material", "fr") == "plastique"
    description = merged.generated_description
    assert description is not None
    assert "Matériau: plastique" in (description.get("fr") or "")


def test_filling_a_language_agnostic_extra_keeps_the_language_it_was_authored_in() -> None:
    """Translating for French must not take the value off the Dutch page.

    A flat extra has one value and no language key of its own, so moving it into a per-language
    mapping silently dropped it out of every language but the filled one — the Dutch
    Technische-details block lost its `Materiaal:` line the moment French was translated.
    """
    product = _product(product_name=LocalisedText(values={"nl": "Bewateringpin", "fr": "Pic"}))
    cache = GeneratedCache(client_id="noviplast")
    for request in pending_requests([product], cache, _ctx("nl", "fr")):
        apply_result(
            cache,
            request,
            _result(
                "Slogan",
                "Puce",
                translations={"material": "plastique"} if request.language == "fr" else {},
            ),
            origin=ORIGIN_GENERATED,
            provenance="in-session",
            now=_NOW,
        )

    merged, _ = merge_generated([product], cache, _ctx("nl", "fr"))

    assert merged[0].extra("material", "nl") == "kunststof"
    assert merged[0].extra("material", "fr") == "plastique"
    description = merged[0].generated_description
    assert description is not None
    assert "Materiaal: kunststof" in (description.get("nl") or "")
    assert "Matériau: plastique" in (description.get("fr") or "")


def test_merge_fills_a_missing_marketing_message() -> None:
    merged = _fill_one(_product(), "fr", description_short="De l'eau pour vos plantes")

    assert merged.description_short is not None
    assert merged.description_short.get("fr") == "De l'eau pour vos plantes"
    assert merged.description_short.get("nl") == "Water voor je planten"  # feed value kept


def test_the_feed_wins_over_a_cached_translation() -> None:
    """Read-side half of the precedence rule; `_requested_translations` is the write-side half.

    A producer that answers for a language the feed already carries must never overwrite the
    datapool's own text — that is silent wrong content on a live page.
    """
    both = _product(product_name=LocalisedText(values={"nl": "Bewateringpin", "fr": "Pic"}))
    cache = GeneratedCache(client_id="noviplast")
    request = next(r for r in pending_requests([both], cache, _ctx("fr")))
    apply_result(
        cache,
        request,
        _result("Slogan", "Puce", translations={"product_name": "Autre chose"}),
        origin=ORIGIN_GENERATED,
        provenance="in-session",
        now=_NOW,
    )

    merged, _ = merge_generated([both], cache, _ctx("fr"))

    assert merged[0].product_name.get("fr") == "Pic"


def test_every_filled_value_is_reported_for_mygs1() -> None:
    product = _product(product_name=LocalisedText(values={"nl": "Bewateringpin"}))
    cache = GeneratedCache(client_id="noviplast")
    request = next(r for r in pending_requests([product], cache, _ctx("fr")))
    apply_result(
        cache,
        request,
        _result(
            "Slogan",
            "Puce",
            translations={"product_name": "Pic d'arrosage", "material": "plastique"},
        ),
        origin=ORIGIN_GENERATED,
        provenance="in-session",
        now=_NOW,
    )

    _, issues = merge_generated([product], cache, _ctx("fr"))

    translated = {i.field: i for i in issues if i.issue == "value_translated"}
    assert set(translated) == {"product_name.fr", "material.fr"}
    # The value IS the deliverable: it is what the operator pastes into MyGS1.
    assert translated["product_name.fr"].value == "Pic d'arrosage"
    assert translated["product_name.fr"].source == "TradeItemDescription attr 3301"
    assert "Bewateringpin" in translated["product_name.fr"].detail  # what it came from
    assert "add it to TradeItemDescription attr 3301 for fr" in translated["product_name.fr"].detail


def test_a_translated_value_with_no_gs1_slot_says_so_rather_than_sending_a_wasted_trip() -> None:
    _, issues = merge_generated(
        [_product()],
        _cache_with_translations(_product(), "fr", {"material": "plastique"}),
        _ctx("fr"),
    )

    material = next(i for i in issues if i.field == "material.fr")
    assert "language-agnostic in GS1" in material.detail
    assert "no fr slot" in material.detail
    # …and on `source`, because the report's table renders that column and not the detail: a row
    # reading only "attr Material" sends the operator hunting for a French field that is not there.
    assert material.source == "BrickGPCCommercialData attr Material — no per-language slot in GS1"


def _cache_with_translations(
    product: ProductRecord, language: str, translations: dict[str, str]
) -> GeneratedCache:
    """A cache holding one fresh entry for ``(product, language)`` with these translations."""
    cache = GeneratedCache(client_id="noviplast")
    request = next(r for r in pending_requests([product], cache, _ctx(language)))
    apply_result(
        cache,
        request,
        _result("Slogan", "Puce", translations=translations),
        origin=ORIGIN_GENERATED,
        provenance="in-session",
        now=_NOW,
    )
    return cache


def test_a_translation_the_request_did_not_ask_for_never_reaches_the_cache() -> None:
    both = _product(product_name=LocalisedText(values={"nl": "Bewateringpin", "fr": "Pic"}))
    cache = _cache_with_translations(both, "fr", {"product_name": "Autre chose"})

    entry = cache.get(both.gtin, "fr")
    assert entry is not None
    assert "product_name" not in entry.translations


def test_editing_the_source_language_supersedes_the_translation() -> None:
    """Without the source text in the fingerprint, a Dutch edit left the French fill looking fresh.

    The French entry's own inputs are all empty in that case, so nothing else in the fingerprint
    moves — the translation of a value that had since changed would have survived the edit.
    """
    product = _product(product_name=LocalisedText(values={"nl": "Bewateringpin"}))
    cache = _cache_with_translations(product, "fr", {"product_name": "Pic d'arrosage"})

    renamed = _product(product_name=LocalisedText(values={"nl": "Waterpin"}))
    merged, _ = merge_generated([renamed], cache, _ctx("fr"))

    assert merged[0].product_name.get("fr") is None  # stale entry ignored


# --- 1067 routing: verbatim / tighten / generate -----------------------------


def _with_1067(text: str) -> ProductRecord:
    return _product(description_long=LocalisedText(values={"nl": text}))


def test_prefill_uses_short_1067_verbatim() -> None:
    cache = GeneratedCache(client_id="noviplast")
    product = _with_1067("Speciaal voor kleine honden\nGemaakt van kunststof")

    prefill_from_feed([product], cache, _ctx("nl"), now=_NOW)

    entry = cache.get("08713195007359", "nl")
    assert entry is not None
    assert entry.origin == "feed"
    assert entry.usps == ["Speciaal voor kleine honden", "Gemaakt van kunststof"]
    # a verbatim-filled unit is no longer pending
    assert pending_requests([product], cache, _ctx("nl")) == []


def test_prefill_skips_long_1067() -> None:
    cache = GeneratedCache(client_id="noviplast")
    product = _with_1067("De Noviplast Hydro Jet is een handige oplossing " * 3)  # > 80 chars

    prefill_from_feed([product], cache, _ctx("nl"), now=_NOW)

    assert cache.get("08713195007359", "nl") is None  # left for the producer


def test_pending_request_mode_tighten_for_long_1067() -> None:
    product = _with_1067("De Noviplast Hydro Jet is een handige oplossing " * 3)
    requests = pending_requests([product], GeneratedCache(client_id="noviplast"), _ctx("nl"))

    assert requests[0].mode == MODE_TIGHTEN
    assert requests[0].candidates  # the 1067 text to shorten


def test_pending_request_mode_generate_without_1067() -> None:
    requests = pending_requests([_product()], GeneratedCache(client_id="noviplast"), _ctx("nl"))

    assert requests[0].mode == MODE_GENERATE


def test_feed_verbatim_copy_is_not_reported() -> None:
    cache = GeneratedCache(client_id="noviplast")
    product = _with_1067("Speciaal voor kleine honden\nGemaakt van kunststof")
    prefill_from_feed([product], cache, _ctx("nl"), now=_NOW)

    _, issues = merge_generated([product], cache, _ctx("nl"))

    assert [i for i in issues if i.issue in {"content_generated", "content_adjusted"}] == []


def test_tightened_copy_is_reported_as_adjusted() -> None:
    cache = GeneratedCache(client_id="noviplast")
    product = _with_1067("A very long feature benefit sentence " * 3)
    request = next(r for r in pending_requests([product], cache, _ctx("nl")))
    apply_result(
        cache,
        request,
        _result("Kort", "Bullet"),
        origin=ORIGIN_TIGHTENED,
        provenance="in-session",
        now=_NOW,
    )

    _, issues = merge_generated([product], cache, _ctx("nl"))

    adjusted = [i for i in issues if i.issue == "content_adjusted"]
    assert len(adjusted) == 1


# --- cache IO ----------------------------------------------------------------


def test_cache_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = GeneratedCache(client_id="noviplast")
    request = next(r for r in pending_requests([_product()], cache, _ctx("nl")))
    apply_result(
        cache,
        request,
        _result("Tagline", "Bullet"),
        origin=ORIGIN_GENERATED,
        provenance="in-session",
        now=_NOW,
    )

    save_cache(cache)
    reloaded = load_cache("noviplast")

    assert reloaded == cache


def test_load_cache_absent_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert load_cache("noviplast").entries == {}


def test_load_cache_corrupt_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "output" / "noviplast" / "data" / "generated_cache.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(GeneratorError, match="corrupt"):
        load_cache("noviplast")
