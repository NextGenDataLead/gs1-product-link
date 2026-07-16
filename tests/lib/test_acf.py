"""Tests for the ACF payload assembler (docs/clients/noviplast-page-adapter.md §3-§4.1)."""

from __future__ import annotations

import pytest

from lib.acf import build_acf_payload
from lib.records import LocalisedText, ProductRecord

_MAP = {"product_title": "description_short", "product_header_video_text": "description_short"}


def _product(**overrides: object) -> ProductRecord:
    base: dict[str, object] = {
        "gtin": "08713195007359",
        "brand": "Noviplast",
        "product_name": LocalisedText(values={"nl": "Rugsteun", "fr": "Support"}),
        "description_short": LocalisedText(
            values={"nl": "Steun voor je rug", "fr": "Support pour le dos"}
        ),
    }
    base.update(overrides)
    return ProductRecord(**base)


def test_localised_source_yields_this_languages_value() -> None:
    assert build_acf_payload(_product(), "nl", _MAP) == {
        "product_title": "Steun voor je rug",
        "product_header_video_text": "Steun voor je rug",
    }
    assert build_acf_payload(_product(), "fr", _MAP) == {
        "product_title": "Support pour le dos",
        "product_header_video_text": "Support pour le dos",
    }


def test_one_source_can_feed_several_acf_fields() -> None:
    """Noviplast's tagline is one value written to two fields, as on the live pages."""
    payload = build_acf_payload(_product(), "nl", _MAP)

    assert payload["product_title"] == payload["product_header_video_text"]


def test_missing_language_omits_the_field_rather_than_falling_back() -> None:
    """A French page must not be given Dutch text — that is the silent-wrong-content shape."""
    product = _product(description_short=LocalisedText(values={"nl": "Alleen NL"}))

    assert build_acf_payload(product, "fr", _MAP) == {}
    assert build_acf_payload(product, "nl", _MAP)["product_title"] == "Alleen NL"


def test_absent_source_field_is_warned_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    """A missing tagline must not stop the page being published with its title."""
    product = _product(description_short=None)

    with caplog.at_level("WARNING", logger="lib.acf"):
        payload = build_acf_payload(product, "nl", _MAP)

    assert payload == {}
    assert "no value for acf.product_title" in caplog.text
    assert "08713195007359" in caplog.text


def test_scalar_source_is_stringified() -> None:
    product = _product(net_content="5 H87")

    assert build_acf_payload(product, "nl", {"tech": "net_content"}) == {"tech": "5 H87"}


def test_extras_are_reachable_by_dotted_path() -> None:
    product = _product(extras={"functional_name": "microvezeldoek"})

    payload = build_acf_payload(product, "nl", {"fn": "extras.functional_name"})

    assert payload == {"fn": "microvezeldoek"}


def test_empty_map_yields_empty_payload() -> None:
    """A client with no acf_map renders from the body template — nothing to assemble."""
    assert build_acf_payload(_product(), "nl", {}) == {}
