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
    ClientConfig,
    ExportConfig,
    GS1Config,
    WebsiteStatusConfig,
    WordPressConfig,
)
from lib.errors import ConfigError
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


def _product(gtin: str = GTIN_A) -> ProductRecord:
    return ProductRecord(
        gtin=gtin,
        brand="Acme",
        product_name=LocalisedText(values={"nl": "Rugsteun"}),
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
