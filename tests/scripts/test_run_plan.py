"""Tests for scripts/run_plan.py (IMPLEMENTATION_SPEC §8.2, §12 Phase 7).

run_plan is pure orchestration over ``lib.state.diff_against_state`` and the
website-status gate, so these tests drive ``main`` with a fake ``get_client`` and a
temp working directory and assert the emitted ``plan.json``, the stderr summary, the
gate filtering, and the exit codes. Classification logic itself is covered in
``tests/lib/test_state.py``; control-file parsing in ``tests/lib/test_website_status.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from lib.config import (
    CategoryConfig,
    ClientConfig,
    ExportConfig,
    GeneratorConfig,
    GS1Config,
    WebsiteStatusConfig,
    WordPressConfig,
)
from lib.errors import ConfigError
from lib.generator import (
    ORIGIN_GENERATED,
    GeneratedCache,
    GenerationResult,
    apply_result,
    pending_requests,
    save_cache,
)
from lib.records import LocalisedText, Plan, PlanClassification, ProductRecord, State, StateEntry
from lib.state import save_state
from scripts import run_plan

GTIN_A = "08713195007359"
GTIN_B = "08713195007360"
GTIN_C = "08713195007361"
GTIN_D = "08713195007362"

_STATUS_HEADER = ["Barcode", "Momenteel op Website", "Al in Gs1", "Link naar site"]


# --- Builders ----------------------------------------------------------------


def _make_config(**overrides: Any) -> ClientConfig:
    params: dict[str, Any] = {
        "client_id": "acme",
        "display_name": "Acme BV",
        "gs1": GS1Config(
            account_number_test="8720796420906",
            client_id_env_test="GS1_CID",
            client_secret_env_test="GS1_SEC",
            environment="test",
        ),
        "export": ExportConfig(path="input/acme.xlsx"),
        "wordpress": WordPressConfig(
            site_url="https://wp.test",
            username="bot",
            app_password_env="WP_PASS",
            post_type="product",
            default_language="nl",
            languages=["nl"],
            slug_pattern="p-{gtin}",
            target_url_pattern="{site_url}/{lang_segment}{post_type}/{slug}/",
        ),
    }
    params.update(overrides)
    return ClientConfig(**params)


def _product(gtin: str = GTIN_A, brick: str | None = None) -> ProductRecord:
    return ProductRecord(
        gtin=gtin,
        brand="Acme",
        product_name=LocalisedText(values={"nl": "Rugsteun"}),
        gpc_brick_code=brick,
    )


def _write_products(path: Path, products: list[ProductRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps([p.model_dump(mode="json") for p in products])
    path.write_text(payload, encoding="utf-8")


def _write_status(tmp_path: Path, rows: list[list[Any]]) -> str:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(_STATUS_HEADER)
    for row in rows:
        sheet.append(row)
    path = tmp_path / "website_status.xlsx"
    workbook.save(path)
    return str(path)


def _patch_client(monkeypatch: pytest.MonkeyPatch, cfg: ClientConfig) -> None:
    monkeypatch.setattr(run_plan, "get_client", lambda _cid: cfg)


def _read_plan() -> Plan:
    return Plan.model_validate(
        json.loads(Path("output/acme/plan.json").read_text(encoding="utf-8"))
    )


# --- Happy path & summary ----------------------------------------------------


def test_writes_plan_with_counts_and_exit_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config())
    products = tmp_path / "products.json"
    _write_products(products, [_product(GTIN_A), _product(GTIN_B)])

    code = run_plan.main(["acme", "--products", str(products)])

    assert code == 0
    plan = _read_plan()
    assert plan.total == 2
    assert plan.counts[PlanClassification.NEW] == 2
    assert {r.classification for r in plan.rows} == {PlanClassification.NEW}


def test_summary_line_to_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config())
    products = tmp_path / "products.json"
    _write_products(products, [_product(GTIN_A)])

    run_plan.main(["acme", "--products", str(products)])

    assert "1 new, 0 unchanged, 0 changed" in capsys.readouterr().err


def test_default_products_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config())
    _write_products(Path("output/acme/data/products.json"), [_product(GTIN_A)])

    assert run_plan.main(["acme"]) == 0
    assert _read_plan().total == 1


# --- Website-status gate -----------------------------------------------------


def test_gate_excludes_non_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    status_path = _write_status(
        tmp_path,
        [
            [GTIN_A, None, "x", None],  # eligible: not on site, in GS1
            [GTIN_B, "x", "x", None],  # already on website
            [GTIN_C, None, None, None],  # not yet in GS1
            # GTIN_D deliberately absent from the control file -> unknown
        ],
    )
    cfg = _make_config(website_status=WebsiteStatusConfig(path=status_path))
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, cfg)
    products = tmp_path / "products.json"
    _write_products(products, [_product(g) for g in (GTIN_A, GTIN_B, GTIN_C, GTIN_D)])

    code = run_plan.main(["acme", "--products", str(products)])

    assert code == 0
    plan = _read_plan()
    assert [r.gtin for r in plan.rows] == [GTIN_A]
    assert plan.counts[PlanClassification.NEW] == 1
    err = capsys.readouterr().err
    assert "1 new" in err
    assert "3 excluded" in err
    assert "1 already on website" in err
    assert "1 not yet in GS1" in err
    assert "1 not in control file" in err


def test_gate_joins_13_digit_barcode_to_14_digit_gtin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Control file carries the 13-digit barcode; the product GTIN is 14-digit.
    status_path = _write_status(tmp_path, [[GTIN_A.lstrip("0"), None, "*", None]])
    cfg = _make_config(website_status=WebsiteStatusConfig(path=status_path))
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, cfg)
    products = tmp_path / "products.json"
    _write_products(products, [_product(GTIN_A)])

    assert run_plan.main(["acme", "--products", str(products)]) == 0
    plan = _read_plan()
    assert [r.gtin for r in plan.rows] == [GTIN_A]


def test_missing_control_file_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _make_config(website_status=WebsiteStatusConfig(path=str(tmp_path / "missing.xlsx")))
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, cfg)
    products = tmp_path / "products.json"
    _write_products(products, [_product(GTIN_A)])

    assert run_plan.main(["acme", "--products", str(products)]) == 2
    assert "config error" in capsys.readouterr().err


# --- Classification against seeded state --------------------------------------


def test_unchanged_and_changed_from_seeded_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config())
    products = tmp_path / "products.json"
    _write_products(products, [_product(GTIN_A), _product(GTIN_B)])

    # First plan (empty state) yields NEW rows; use their hashes/URLs to seed state so
    # A re-plans UNCHANGED (matching hash) and B re-plans CHANGED (stale hash).
    run_plan.main(["acme", "--products", str(products)])
    first = _read_plan()
    row_a = next(r for r in first.rows if r.gtin == GTIN_A)
    row_b = next(r for r in first.rows if r.gtin == GTIN_B)
    save_state(
        State(
            client_id="acme",
            entries={
                GTIN_A: {"nl": _entry(row_a.content_hash, row_a.target_url)},
                GTIN_B: {"nl": _entry("stale-hash", row_b.target_url)},
            },
        )
    )

    run_plan.main(["acme", "--products", str(products)])
    plan = _read_plan()

    classifications = {r.gtin: r.classification for r in plan.rows}
    assert classifications[GTIN_A] is PlanClassification.UNCHANGED
    assert classifications[GTIN_B] is PlanClassification.CHANGED


def _entry(content_hash: str, wp_url: str) -> StateEntry:
    return StateEntry(
        wp_page_id=1,
        wp_url=wp_url,
        wp_featured_media_id=None,
        content_hash=content_hash,
        gs1_link_set_hash="g" * 64,
        last_run=datetime(2026, 7, 12, tzinfo=UTC),
    )


def test_corrupt_state_warns_replans_as_new_and_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """E19: a corrupt state file does not abort the plan — but the reset must be loud.

    Without the warning the operator sees only "1 new" where they expected "1 unchanged",
    with nothing to explain it, and confirming would rewrite live pages and resolver targets.
    """
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config())
    products = tmp_path / "products.json"
    _write_products(products, [_product(GTIN_A)])
    state_file = tmp_path / "output" / "acme" / "state.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text("{ truncated", encoding="utf-8")

    code = run_plan.main(["acme", "--products", str(products)])

    assert code == 0
    assert _read_plan().counts[PlanClassification.NEW] == 1
    err = capsys.readouterr().err
    assert "prior state was corrupt and has been reset" in err
    assert "rewrite live pages and resolver targets" in err
    assert list(state_file.parent.glob("state.json.corrupt.*"))  # bad file preserved


def test_healthy_state_does_not_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config())
    products = tmp_path / "products.json"
    _write_products(products, [_product(GTIN_A)])

    run_plan.main(["acme", "--products", str(products)])

    assert "corrupt" not in capsys.readouterr().err


# --- Category assignment (Phase 7.5) -----------------------------------------


def _categories(**kwargs: Any) -> CategoryConfig:
    kwargs.setdefault("terms", ["tuin", "keuken"])
    return CategoryConfig(**kwargs)


def test_assigns_category_from_brick_map(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _make_config(categories=_categories(brick_category_map={"10003865": "tuin"}))
    _patch_client(monkeypatch, cfg)
    products = tmp_path / "products.json"
    _write_products(products, [_product(GTIN_A, brick="10003865")])

    assert run_plan.main(["acme", "--products", str(products)]) == 0
    assert _read_plan().rows[0].product.category == "tuin"
    # The report is written even when it found nothing.
    issues = json.loads(Path("output/acme/data/category_issues.json").read_text(encoding="utf-8"))
    assert issues == []


def test_override_wins_in_run_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _make_config(
        categories=_categories(
            brick_category_map={"10003865": "tuin"}, overrides={GTIN_A: "keuken"}
        )
    )
    _patch_client(monkeypatch, cfg)
    products = tmp_path / "products.json"
    _write_products(products, [_product(GTIN_A, brick="10003865")])

    assert run_plan.main(["acme", "--products", str(products)]) == 0
    assert _read_plan().rows[0].product.category == "keuken"


def test_category_change_reclassifies_as_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The category is part of the content hash: planning it onto a product that had none
    # must reclassify the row as CHANGED, not UNCHANGED.
    monkeypatch.chdir(tmp_path)
    products = tmp_path / "products.json"
    _write_products(products, [_product(GTIN_A, brick="10003865")])

    _patch_client(monkeypatch, _make_config())  # no categories -> baseline hash, category None
    run_plan.main(["acme", "--products", str(products)])
    baseline = _read_plan().rows[0]
    assert baseline.product.category is None
    save_state(
        State(
            client_id="acme",
            entries={GTIN_A: {"nl": _entry(baseline.content_hash, baseline.target_url)}},
        )
    )

    cfg = _make_config(categories=_categories(brick_category_map={"10003865": "tuin"}))
    _patch_client(monkeypatch, cfg)
    run_plan.main(["acme", "--products", str(products)])
    row = _read_plan().rows[0]

    assert row.product.category == "tuin"
    assert row.classification is PlanClassification.CHANGED


def test_unmapped_brick_warns_and_leaves_category_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _make_config(categories=_categories(brick_category_map={"10003865": "tuin"}))
    _patch_client(monkeypatch, cfg)
    products = tmp_path / "products.json"
    _write_products(products, [_product(GTIN_A, brick="99999999")])

    code = run_plan.main(["acme", "--products", str(products)])

    assert code == 0
    row = _read_plan().rows[0]
    assert row.product.category is None  # never guessed
    assert "1 product(s) with unmapped category (left unset)" in capsys.readouterr().err
    issues = json.loads(Path("output/acme/data/category_issues.json").read_text(encoding="utf-8"))
    assert len(issues) == 1
    assert issues[0]["issue"] == "category_unmapped"


def test_no_category_report_without_categories_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config())
    products = tmp_path / "products.json"
    _write_products(products, [_product(GTIN_A, brick="10003865")])

    assert run_plan.main(["acme", "--products", str(products)]) == 0
    assert not Path("output/acme/data/category_issues.json").exists()


# --- Error paths (all exit 2) ------------------------------------------------


def test_missing_products_file_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config())

    assert run_plan.main(["acme", "--products", str(tmp_path / "nope.json")]) == 2
    assert "config error" in capsys.readouterr().err


def test_invalid_products_json_exit_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config())
    products = tmp_path / "products.json"
    products.write_text("{ not json", encoding="utf-8")

    assert run_plan.main(["acme", "--products", str(products)]) == 2


def test_unknown_client_exit_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    def _raise(_cid: str) -> ClientConfig:
        raise ConfigError("unknown client_id 'nope'")

    monkeypatch.setattr(run_plan, "get_client", _raise)

    assert run_plan.main(["nope"]) == 2


def test_missing_target_url_pattern_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    wordpress = WordPressConfig(
        site_url="https://wp.test",
        username="bot",
        app_password_env="WP_PASS",
        default_language="nl",
        languages=["nl"],
        slug_pattern="p-{gtin}",
        # target_url_pattern intentionally unset
    )
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config(wordpress=wordpress))
    products = tmp_path / "products.json"
    _write_products(products, [_product(GTIN_A)])

    assert run_plan.main(["acme", "--products", str(products)]) == 2
    assert "target_url_pattern" in capsys.readouterr().err


# --- Generated-content merge (generator SPEC, commit 7) ----------------------

_GEN_NOW = datetime(2026, 7, 19, tzinfo=UTC)


def _bilingual_wp() -> WordPressConfig:
    return WordPressConfig(
        site_url="https://wp.test",
        username="bot",
        app_password_env="WP_PASS",
        post_type="product",
        default_language="nl",
        languages=["nl", "fr"],
        slug_pattern="p-{gtin}",
        target_url_pattern="{site_url}/{lang_segment}{post_type}/{slug}/",
    )


def _cache_with(gtin: str, language: str, **result: Any) -> None:
    """Build and persist a generated_cache.json with one fresh entry for (gtin, language)."""
    cache = GeneratedCache(client_id="acme")
    request = pending_requests([_product(gtin)], cache, [language], "v1")[0]
    apply_result(
        cache,
        request,
        GenerationResult(**result),
        origin=ORIGIN_GENERATED,
        provenance="cowork",
        now=_GEN_NOW,
    )
    save_cache(cache)


def test_generated_content_reclassifies_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config(generator=GeneratorConfig(enabled=True)))
    products = tmp_path / "products.json"
    _write_products(products, [_product(GTIN_A)])

    # Baseline: plan once with no cache, then record state so the row would be UNCHANGED.
    run_plan.main(["acme", "--products", str(products)])
    row = next(r for r in _read_plan().rows if r.gtin == GTIN_A)
    save_state(
        State(client_id="acme", entries={GTIN_A: {"nl": _entry(row.content_hash, row.target_url)}})
    )

    # Now generated copy lands in the cache — it enters the hash and reclassifies the row.
    _cache_with(GTIN_A, "nl", usps=["Tagline", "Bullet"])
    run_plan.main(["acme", "--products", str(products)])

    row = next(r for r in _read_plan().rows if r.gtin == GTIN_A)
    assert row.classification is PlanClassification.CHANGED


def test_e18_cached_french_name_is_planned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _make_config(wordpress=_bilingual_wp(), generator=GeneratorConfig(enabled=True))
    _patch_client(monkeypatch, cfg)
    products = tmp_path / "products.json"
    _write_products(products, [_product(GTIN_A)])  # nl name only
    _cache_with(GTIN_A, "fr", usps=["Slogan"], product_name="Nom FR")  # French fill

    run_plan.main(["acme", "--products", str(products)])

    planned = {(r.gtin, r.language) for r in _read_plan().rows}
    assert (GTIN_A, "fr") in planned  # E18 no longer fires — the fr name came from the cache
    assert (GTIN_A, "nl") in planned


def test_e18_without_cache_still_skips_french(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _make_config(wordpress=_bilingual_wp(), generator=GeneratorConfig(enabled=True))
    _patch_client(monkeypatch, cfg)
    products = tmp_path / "products.json"
    _write_products(products, [_product(GTIN_A)])  # nl name only, no cache

    run_plan.main(["acme", "--products", str(products)])

    planned = {(r.gtin, r.language) for r in _read_plan().rows}
    assert (GTIN_A, "fr") not in planned  # E18 backstop: no fr name anywhere -> skipped
    assert (GTIN_A, "nl") in planned


def test_generated_issues_report_written_when_generator_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config(generator=GeneratorConfig(enabled=True)))
    products = tmp_path / "products.json"
    _write_products(products, [_product(GTIN_A)])  # no 1083 -> a missing_generation_input note

    run_plan.main(["acme", "--products", str(products)])

    report = Path("output/acme/data/generated_issues.json")
    assert report.exists()
    issues = json.loads(report.read_text(encoding="utf-8"))
    assert any(i["issue"] == "missing_generation_input" for i in issues)


def test_no_generated_issues_file_without_generator_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config())  # no generator block
    products = tmp_path / "products.json"
    _write_products(products, [_product(GTIN_A)])

    run_plan.main(["acme", "--products", str(products)])

    assert not Path("output/acme/data/generated_issues.json").exists()
