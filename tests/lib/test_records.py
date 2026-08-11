"""Unit tests for the canonical record schema (IMPLEMENTATION_SPEC §2, §4.9)."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from lib.errors import ExportParseError
from lib.records import (
    LocalisedText,
    Plan,
    PlanClassification,
    PlanRow,
    ProductRecord,
    RunOutcome,
    State,
    StateEntry,
    is_valid_target_path,
    parse_excel_row,
)


def _product(**overrides: object) -> ProductRecord:
    base: dict[str, object] = {
        "gtin": "08713195007359",
        "brand": "Noviplast",
        "product_name": LocalisedText(values={"nl": "Rugsteun", "fr": "Support arrière"}),
    }
    base.update(overrides)
    return ProductRecord(**base)


# --- LocalisedText -----------------------------------------------------------


def test_localised_get_hit_fallback_and_miss() -> None:
    text = LocalisedText(values={"nl": "Rugsteun"})

    assert text.get("nl") == "Rugsteun"
    assert text.get("fr", fallback="nl") == "Rugsteun"
    assert text.get("fr") is None


# --- ProductRecord validation ------------------------------------------------


@pytest.mark.parametrize("gtin", ["12345678", "8712345678905", "08713195007359"])
def test_gtin_pattern_accepts_valid_lengths(gtin: str) -> None:
    assert _product(gtin=gtin).gtin == gtin


@pytest.mark.parametrize("gtin", ["1234567", "abcdefgh", "087131950073591"])
def test_gtin_pattern_rejects_invalid(gtin: str) -> None:
    with pytest.raises(ValidationError):
        _product(gtin=gtin)


def test_gtin14_zero_pads() -> None:
    assert _product(gtin="8712345678905").gtin14 == "08712345678905"


def test_product_record_is_frozen() -> None:
    record = _product()
    with pytest.raises(ValidationError):
        record.brand = "Other"  # type: ignore[misc]


def test_state_is_mutable() -> None:
    # State/StateEntry are intentionally not frozen (§2.3).
    state = State(client_id="noviplast", entries={})
    state.entries["08713195007359"] = {}
    assert "08713195007359" in state.entries


# --- Plan types --------------------------------------------------------------


def test_plan_types_construct() -> None:
    row = PlanRow(
        gtin="08713195007359",
        language="nl",
        classification=PlanClassification.NEW,
        title="Rugsteun",
        slug="p-08713195007359",
        content_hash="abc",
        target_url="https://example.test/p/",
        product=_product(),
    )
    plan = Plan(
        client_id="noviplast",
        generated_at=datetime(2026, 7, 11, 12, 0, 0),
        total=1,
        counts={PlanClassification.NEW: 1},
        rows=[row],
    )
    assert plan.rows[0].classification is PlanClassification.NEW
    assert PlanClassification.CHANGED.value == "changed"


def test_state_entry_round_trips() -> None:
    entry = StateEntry(
        wp_page_id=42,
        wp_url="https://example.test/p/",
        wp_featured_media_id=None,
        content_hash="abc",
        gs1_link_set_hash="def",
        last_run=datetime(2026, 7, 11, 12, 0, 0),
    )
    assert StateEntry.model_validate_json(entry.model_dump_json()) == entry


def test_run_outcome_line_written_before_failed_call_still_loads() -> None:
    """A run log predating ``failed_call`` must keep loading, not become unreadable.

    ``ui.context.load_run`` counts a line that fails validation as ``unreadable`` rather than
    raising, so a *required* new field would silently hollow out every log already on disk
    instead of failing loudly. This is the same back-compat move ``StateEntry.title`` makes.
    """
    old_line = (
        '{"gtin": "08713195007717", "language": "fr", "ts": "2026-08-11T09:00:00Z", '
        '"status": "error", "wp_page_id": null, "wp_url": null, "wp_featured_media_id": null, '
        '"gs1_set": false, "qr_paths": [], '
        '"error": "WordPressAPIError(\'WordPress API error 403\')"}'
    )

    outcome = RunOutcome.model_validate_json(old_line)

    assert outcome.failed_call is None  # "not recorded", not a guess
    assert outcome.status == "error"


def test_run_outcome_round_trips_with_the_failing_call() -> None:
    outcome = RunOutcome(
        gtin="08713195007717",
        language="fr",
        ts=datetime(2026, 8, 11, 9, 0, 0),
        status="error",
        error="WordPressAPIError('WordPress API error 403 …')",
        failed_call="POST /wp-json/wp/v2/media (upload media clip-a1b2c3d4e5f6)",
    )

    assert RunOutcome.model_validate_json(outcome.model_dump_json()) == outcome


# --- Target-path helper ------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["gtin", "brand", "product_name.nl", "description_short.fr", "extras.hs_code"],
)
def test_valid_target_paths(path: str) -> None:
    assert is_valid_target_path(path)


@pytest.mark.parametrize("path", ["bogus", "foo.nl", "product_name", "extras"])
def test_invalid_target_paths(path: str) -> None:
    assert not is_valid_target_path(path)


# --- Flat parse_excel_row edge cases -----------------------------------------

_COLUMN_MAP = {
    "GTIN": "gtin",
    "Merk": "brand",
    "Productnaam NL": "product_name.nl",
    "Productnaam FR": "product_name.fr",
}


def test_parse_row_preserves_leading_zero_gtin() -> None:
    # E1: text GTIN with leading zeros is kept verbatim.
    row = {"GTIN": "08713195007359", "Merk": "Noviplast", "Productnaam NL": "Rugsteun"}
    record = parse_excel_row(row, _COLUMN_MAP, [], "nl")
    assert record.gtin == "08713195007359"


def test_parse_row_coerces_integer_gtin() -> None:
    # E2: openpyxl casts a bare-number cell to int; we coerce back to str.
    row = {"GTIN": 8712345678905, "Merk": "Noviplast", "Productnaam NL": "Rugsteun"}
    record = parse_excel_row(row, _COLUMN_MAP, [], "nl")
    assert record.gtin == "8712345678905"


def test_parse_row_assembles_nested_localised_text() -> None:
    row = {
        "GTIN": "08713195007359",
        "Merk": "Noviplast",
        "Productnaam NL": "Rugsteun",
        "Productnaam FR": "Support arrière",
    }
    record = parse_excel_row(row, _COLUMN_MAP, [], "nl")
    assert record.product_name.values == {"nl": "Rugsteun", "fr": "Support arrière"}


def test_parse_row_carries_extras_columns_by_name() -> None:
    row = {"GTIN": "08713195007359", "Merk": "Noviplast", "Productnaam NL": "x", "HS-code": "9403"}
    record = parse_excel_row(row, _COLUMN_MAP, ["HS-code"], "nl")
    assert record.extras == {"HS-code": "9403"}


def test_parse_row_missing_default_language_raises_with_gtin() -> None:
    # E5: GTIN present but no product_name in the default language.
    row = {"GTIN": "08713195007359", "Merk": "Noviplast", "Productnaam FR": "Support"}
    with pytest.raises(ExportParseError, match="08713195007359"):
        parse_excel_row(row, _COLUMN_MAP, [], "nl")


# --- JSON round-trip (DoD) ---------------------------------------------------


def test_product_record_json_round_trip_preserves_all_fields() -> None:
    record = _product(
        gpc_brick_code="10000248",
        net_content="400 MMT",
        image_url="https://cdn.test/img.tiff",
        category="outdoor",
        description_short=LocalisedText(values={"nl": "Kort", "fr": "Court"}),
        description_long=LocalisedText(values={"nl": "Lang", "fr": "Long"}),
        generated_tagline=LocalisedText(values={"nl": "Slogan", "fr": "Slogan FR"}),
        generated_description=LocalisedText(values={"nl": "<p>NL</p>", "fr": "<p>FR</p>"}),
        extras={"functional_name": "Rugsteun"},
    )

    restored = ProductRecord.model_validate_json(record.model_dump_json())

    assert restored == record


def test_generated_fields_default_to_none() -> None:
    record = _product()
    assert record.generated_tagline is None
    assert record.generated_description is None
