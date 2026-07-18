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
from lib.generator import (
    GeneratedCache,
    GenerationResult,
    _combine_title,
    apply_result,
    load_cache,
    merge_generated,
    pending_requests,
    save_cache,
)
from lib.records import LocalisedText, ProductRecord

_NOW = datetime(2026, 7, 18, tzinfo=UTC)


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


def _result(*usps: str, product_name: str | None = None) -> GenerationResult:
    return GenerationResult(usps=list(usps), product_name=product_name)


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
    requests = pending_requests([_product()], cache, ["nl", "fr"], "v1")

    assert {r.language for r in requests} == {"nl", "fr"}
    assert requests[0].inputs.marketing_message == "Water voor je planten"


def test_pending_requests_skips_when_fingerprint_matches() -> None:
    cache = GeneratedCache(client_id="noviplast")
    product = _product()
    request = next(r for r in pending_requests([product], cache, ["nl"], "v1"))
    apply_result(cache, request, _result("Tagline", "Bullet"), provenance="cowork", now=_NOW)

    assert pending_requests([product], cache, ["nl"], "v1") == []


def test_pending_requests_flags_needs_name_for_missing_language() -> None:
    product = _product()  # only nl in product_name
    requests = pending_requests([product], GeneratedCache(client_id="noviplast"), ["fr"], "v1")

    assert requests[0].needs_name is True


# --- apply_result ------------------------------------------------------------


def test_apply_result_stores_entry() -> None:
    cache = GeneratedCache(client_id="noviplast")
    request = next(r for r in pending_requests([_product()], cache, ["nl"], "v1"))

    apply_result(cache, request, _result("Tagline", "Bullet"), provenance="cowork", now=_NOW)

    entry = cache.get("08713195007359", "nl")
    assert entry is not None
    assert entry.usps == ["Tagline", "Bullet"]
    assert entry.provenance == "cowork"


def test_apply_result_rejects_empty_usps() -> None:
    cache = GeneratedCache(client_id="noviplast")
    request = next(r for r in pending_requests([_product()], cache, ["nl"], "v1"))

    with pytest.raises(GeneratorError, match="empty generation result"):
        apply_result(cache, request, _result("   "), provenance="cowork", now=_NOW)


# --- merge_generated ---------------------------------------------------------


def _merge_one(product: ProductRecord, *usps: str, **kw: object) -> ProductRecord:
    """Generate for ``product`` (nl) and return the merged record."""
    cache = GeneratedCache(client_id="noviplast")
    request = next(r for r in pending_requests([product], cache, ["nl"], "v1"))
    apply_result(cache, request, _result(*usps), provenance="cowork", now=_NOW)
    merged, _ = merge_generated([product], cache, ["nl"], "nl", "v1")
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
        "<strong>Eigenschappen</strong><br />"
        "• Klem om te bundelen<br />• Op maat te knippen"
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
    request = next(r for r in pending_requests([product], cache, ["nl"], "v1"))
    apply_result(cache, request, _result("Tagline", "Bullet"), provenance="cowork", now=_NOW)

    _, issues = merge_generated([product], cache, ["nl"], "nl", "v1")

    generated = [i for i in issues if i.issue == "content_generated"]
    assert len(generated) == 1
    assert generated[0].field == "generated_description.nl"
    assert generated[0].value == "Water voor je planten"  # the source-language input


def test_merge_ignores_stale_entry_when_feed_changed() -> None:
    # A cached entry keyed on old inputs must not be used after a feed edit (supersession).
    cache = GeneratedCache(client_id="noviplast")
    old = _product(description_short=LocalisedText(values={"nl": "Oud"}))
    request = next(r for r in pending_requests([old], cache, ["nl"], "v1"))
    apply_result(cache, request, _result("Tagline", "Bullet"), provenance="cowork", now=_NOW)

    edited = _product(description_short=LocalisedText(values={"nl": "Nieuw"}))
    merged, _ = merge_generated([edited], cache, ["nl"], "nl", "v1")

    assert merged[0].generated_description is None  # stale entry ignored


def test_merge_flags_blank_marketing_message() -> None:
    product = _product(description_short=None)  # no 1083
    _, issues = merge_generated(
        [product], GeneratedCache(client_id="noviplast"), ["nl"], "nl", "v1"
    )

    flags = [i for i in issues if i.issue == "missing_generation_input"]
    assert len(flags) == 1
    assert flags[0].field == "description_short.nl"


def test_merge_combines_title_with_variation() -> None:
    product = _product(
        product_name=LocalisedText(values={"nl": "Emmer"}), extras={"product_variation": "Set"}
    )
    merged, _ = merge_generated(
        [product], GeneratedCache(client_id="noviplast"), ["nl"], "nl", "v1"
    )

    assert merged[0].product_name.get("nl") == "Emmer Set"


def test_merge_fills_missing_french_name_from_cache() -> None:
    product = _product(product_name=LocalisedText(values={"nl": "Bewateringpin"}))  # no fr
    cache = GeneratedCache(client_id="noviplast")
    request = next(r for r in pending_requests([product], cache, ["fr"], "v1"))
    assert request.needs_name is True
    apply_result(
        cache, request, _result("Slogan", "Puce", product_name="Pic d'arrosage"),
        provenance="cowork", now=_NOW,
    )

    merged, _ = merge_generated([product], cache, ["fr"], "nl", "v1")

    assert merged[0].product_name.get("fr") == "Pic d'arrosage"


# --- cache IO ----------------------------------------------------------------


def test_cache_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = GeneratedCache(client_id="noviplast")
    request = next(
        r for r in pending_requests([_product()], cache, ["nl"], "v1")
    )
    apply_result(cache, request, _result("Tagline", "Bullet"), provenance="cowork", now=_NOW)

    save_cache(cache)
    reloaded = load_cache("noviplast")

    assert reloaded == cache


def test_load_cache_absent_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert load_cache("noviplast").entries == {}


def test_load_cache_corrupt_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "output" / "noviplast" / "data" / "generated_cache.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(GeneratorError, match="corrupt"):
        load_cache("noviplast")
