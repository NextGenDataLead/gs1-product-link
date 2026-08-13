"""Unit tests for the E23 mandatory-source-data rule.

The rule decides whether a SKU may publish at all, so the cases that matter are the ones where
"present" is ambiguous: a value in one language but not the other, an either-or group with one
member filled, and a whitespace-only cell that reads as filled to a human scanning a spreadsheet.
"""

from __future__ import annotations

import pytest

from lib.gdsn import GdsnSource
from lib.mandatory import missing_mandatory
from lib.records import LocalisedText, ProductRecord

LANGS = ["nl", "fr"]


def _product(**fields: object) -> ProductRecord:
    base: dict[str, object] = {
        "gtin": "08713195007359",
        "brand": "Noviplast",
        "product_name": LocalisedText(values={"nl": "haak", "fr": "crochet"}),
    }
    return ProductRecord.model_validate({**base, **fields})


def _map(**sources: GdsnSource) -> dict[str, GdsnSource]:
    return dict(sources)


def test_a_complete_product_has_no_gaps() -> None:
    gdsn_map = _map(
        product_name=GdsnSource(sheet="S", attribute="3301", localised=True, required=True),
        brand=GdsnSource(sheet="S", attribute="3336", required=True),
    )
    assert missing_mandatory(_product(), gdsn_map, LANGS) == []


def test_a_localised_field_missing_one_language_holds_the_whole_product() -> None:
    """The hold is per SKU: publishing nl while fr is missing leaves a product half-live."""
    gdsn_map = _map(
        product_name=GdsnSource(sheet="S", attribute="3301", localised=True, required=True)
    )
    product = _product(product_name=LocalisedText(values={"nl": "haak"}))

    gaps = missing_mandatory(product, gdsn_map, LANGS)

    assert [g.language for g in gaps] == ["fr"]
    assert gaps[0].label == "product_name.fr (attr 3301)"


def test_a_blank_language_value_counts_as_missing() -> None:
    gdsn_map = _map(
        product_name=GdsnSource(sheet="S", attribute="3301", localised=True, required=True)
    )
    product = _product(product_name=LocalisedText(values={"nl": "haak", "fr": "   "}))

    assert [g.language for g in missing_mandatory(product, gdsn_map, LANGS)] == ["fr"]


def test_a_non_localised_field_is_checked_once() -> None:
    gdsn_map = _map(net_content=GdsnSource(sheet="S", attribute="3510", required=True))
    product = _product(net_content=None)

    gaps = missing_mandatory(product, gdsn_map, LANGS)

    assert len(gaps) == 1  # not one per language
    assert gaps[0].language == ""
    assert gaps[0].label == "net_content (attr 3510)"


def test_an_either_or_group_is_satisfied_by_one_member() -> None:
    """1083 *or* 1067 — the case the group exists for. Neither is individually mandatory."""
    gdsn_map = _map(
        description_short=GdsnSource(
            sheet="S", attribute="1083", localised=True, required_group="marketing_copy"
        ),
        description_long=GdsnSource(
            sheet="S", attribute="1067", localised=True, required_group="marketing_copy"
        ),
    )
    only_1067 = _product(description_long=LocalisedText(values={"nl": "usp", "fr": "usp"}))

    assert missing_mandatory(only_1067, gdsn_map, LANGS) == []


def test_an_empty_either_or_group_is_reported_once_per_language_under_the_group_name() -> None:
    gdsn_map = _map(
        description_short=GdsnSource(
            sheet="S", attribute="1083", localised=True, required_group="marketing_copy"
        ),
        description_long=GdsnSource(
            sheet="S", attribute="1067", localised=True, required_group="marketing_copy"
        ),
    )

    gaps = missing_mandatory(_product(), gdsn_map, LANGS)

    assert [g.field for g in gaps] == ["marketing_copy", "marketing_copy"]
    assert [g.language for g in gaps] == ["nl", "fr"]
    # Both attributes are named, because either one fixes it.
    assert gaps[0].label == "marketing_copy.nl (attr 1083/1067)"


def test_a_group_missing_in_one_language_only_reports_that_language() -> None:
    gdsn_map = _map(
        description_short=GdsnSource(
            sheet="S", attribute="1083", localised=True, required_group="marketing_copy"
        ),
        description_long=GdsnSource(
            sheet="S", attribute="1067", localised=True, required_group="marketing_copy"
        ),
    )
    product = _product(description_short=LocalisedText(values={"nl": "copy"}))

    assert [g.language for g in missing_mandatory(product, gdsn_map, LANGS)] == ["fr"]


def test_unmarked_fields_are_never_required() -> None:
    """Mandatory-ness is declared per client; an unmarked field must not start blocking."""
    gdsn_map = _map(net_content=GdsnSource(sheet="S", attribute="3510"))

    assert missing_mandatory(_product(net_content=None), gdsn_map, LANGS) == []


def test_an_extras_field_can_be_required_too() -> None:
    """``gdsn_map`` targets record fields, but the lookup falls back to ``extras``."""
    gdsn_map = _map(material=GdsnSource(sheet="S", attribute="Material", required=True))

    assert missing_mandatory(_product(), gdsn_map, LANGS)[0].field == "material"
    assert missing_mandatory(_product(extras={"material": "PP"}), gdsn_map, LANGS) == []


def test_gaps_are_ordered_by_field_then_language() -> None:
    """Deterministic output: two runs over the same data report the same thing in one order."""
    gdsn_map = _map(
        product_name=GdsnSource(sheet="S", attribute="3301", localised=True, required=True),
        brand=GdsnSource(sheet="S", attribute="3336", required=True),
    )
    product = _product(product_name=LocalisedText(values={}), brand="")

    assert [g.label for g in missing_mandatory(product, gdsn_map, LANGS)] == [
        "product_name.nl (attr 3301)",
        "product_name.fr (attr 3301)",
        "brand (attr 3336)",
    ]


def test_required_and_required_group_together_is_a_config_error() -> None:
    """They answer different questions; naming both means the author meant one of them."""
    with pytest.raises(ValueError, match="not both"):
        GdsnSource(sheet="S", attribute="1083", required=True, required_group="marketing_copy")
