"""Unit tests for GPC brick → category resolution (Phase 7.5)."""

from __future__ import annotations

from lib.categories import (
    coverage_report,
    distinct_bricks,
    resolve_category,
)
from lib.config import CategoryConfig
from lib.records import LocalisedText, ProductRecord

_TERMS = frozenset({"tuin", "keuken", "specials"})


def _product(gtin: str, brick: str | None) -> ProductRecord:
    return ProductRecord(
        gtin=gtin,
        brand="Noviplast",
        product_name=LocalisedText(values={"nl": "test"}),
        gpc_brick_code=brick,
    )


# --- resolve_category --------------------------------------------------------


def test_brick_map_resolves_when_no_override() -> None:
    res = resolve_category(
        _product("08713195000123", "10003865"),
        brick_category_map={"10003865": "tuin"},
        overrides={},
        allowed_terms=_TERMS,
    )
    assert res.term == "tuin"
    assert res.issue is None


def test_override_wins_over_brick_map() -> None:
    # Same brick maps to tuin, but this GTIN is the nutcracker → keuken.
    res = resolve_category(
        _product("08713195000123", "10003865"),
        brick_category_map={"10003865": "tuin"},
        overrides={"08713195000123": "keuken"},
        allowed_terms=_TERMS,
    )
    assert res.term == "keuken"
    assert res.issue is None


def test_unmapped_brick_warns_and_does_not_guess() -> None:
    res = resolve_category(
        _product("08713195000123", "99999999"),
        brick_category_map={"10003865": "tuin"},
        overrides={},
        allowed_terms=_TERMS,
    )
    assert res.term is None
    assert res.issue is not None
    assert res.issue.issue == "category_unmapped"
    assert res.issue.field == "category"
    assert res.issue.value == "99999999"  # the brick, verbatim — not a guessed term


def test_missing_brick_reports_brick_missing() -> None:
    res = resolve_category(
        _product("08713195000123", None),
        brick_category_map={"10003865": "tuin"},
        overrides={},
        allowed_terms=_TERMS,
    )
    assert res.term is None
    assert res.issue is not None
    assert res.issue.issue == "category_brick_missing"
    assert res.issue.value == ""


def test_override_matches_on_gtin14_normalisation() -> None:
    # A 13-digit override key must resolve a product whose GTIN-14 is the zero-padded form.
    res = resolve_category(
        _product("8713195000123", "10003865"),  # 13 digits → gtin14 = 08713195000123
        brick_category_map={},
        overrides={"08713195000123": "keuken"},
        allowed_terms=_TERMS,
    )
    assert res.term == "keuken"


def test_term_outside_allowed_treated_as_unresolved() -> None:
    # Defensive: a brick_category_map value not in allowed_terms does not resolve.
    res = resolve_category(
        _product("08713195000123", "10003865"),
        brick_category_map={"10003865": "not_a_term"},
        overrides={},
        allowed_terms=_TERMS,
    )
    assert res.term is None
    assert res.issue is not None
    assert res.issue.issue == "category_unmapped"


def test_override_outside_allowed_treated_as_unresolved() -> None:
    res = resolve_category(
        _product("08713195000123", "10003865"),
        brick_category_map={"10003865": "tuin"},
        overrides={"08713195000123": "not_a_term"},
        allowed_terms=_TERMS,
    )
    assert res.term is None
    assert res.issue is not None
    assert res.issue.issue == "category_unmapped"


# --- distinct_bricks ---------------------------------------------------------


def test_distinct_bricks_groups_gtins_and_skips_missing() -> None:
    products = [
        _product("08713195000001", "10003865"),
        _product("08713195000002", "10003865"),
        _product("08713195000003", "10006459"),
        _product("08713195000004", None),
    ]
    bricks = distinct_bricks(products)
    assert bricks == {
        "10003865": ["08713195000001", "08713195000002"],
        "10006459": ["08713195000003"],
    }


# --- coverage_report ---------------------------------------------------------


def _categories(brick_map: dict[str, str], overrides: dict[str, str]) -> CategoryConfig:
    return CategoryConfig(
        terms=["tuin", "keuken", "specials"],
        brick_category_map=brick_map,
        overrides=overrides,
    )


def test_coverage_report_buckets_mapped_override_only_and_unmapped() -> None:
    products = [
        _product("08713195000001", "10003865"),  # mapped
        _product("08713195000002", "10006459"),  # override-only (whole brick overridden)
        _product("08713195000003", "77777777"),  # unmapped
    ]
    categories = _categories(
        brick_map={"10003865": "tuin"},
        overrides={"08713195000002": "specials"},
    )
    report = coverage_report(products, categories)
    assert report.total_bricks == 3
    assert report.mapped == ["10003865"]
    assert report.override_only == ["10006459"]
    assert report.unmapped == {"77777777": ["08713195000003"]}
    assert report.is_complete is False


def test_coverage_report_complete_when_every_brick_resolves() -> None:
    products = [
        _product("08713195000001", "10003865"),
        _product("08713195000002", "10006459"),
    ]
    categories = _categories(
        brick_map={"10003865": "tuin", "10006459": "specials"},
        overrides={},
    )
    report = coverage_report(products, categories)
    assert report.unmapped == {}
    assert report.is_complete is True


def test_coverage_partial_override_leaves_brick_unmapped() -> None:
    # A brick where only *some* products have an override is still unmapped — for the rest.
    products = [
        _product("08713195000001", "10003865"),
        _product("08713195000002", "10003865"),
    ]
    categories = _categories(brick_map={}, overrides={"08713195000001": "tuin"})
    report = coverage_report(products, categories)
    assert report.unmapped == {"10003865": ["08713195000002"]}
    assert report.is_complete is False
