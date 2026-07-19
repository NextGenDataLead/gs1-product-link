"""Tests for scripts/build_video_map.py (Phase 9.5 media).

Read-only orchestration over ``lib.media_video``: drive ``main`` with a faked ``get_client``
and a temp working directory, asserting the printed draft, the coverage gate's exit codes, and
the issues file. Matching/validation logic itself is covered in ``tests/lib/test_media_video.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.config import (
    ClientConfig,
    ExportConfig,
    GS1Config,
    MediaConfig,
    WordPressConfig,
)
from lib.records import LocalisedText, ProductRecord
from scripts import build_video_map


def _make_config(media: MediaConfig | None) -> ClientConfig:
    return ClientConfig(
        client_id="acme",
        display_name="Acme BV",
        gs1=GS1Config(
            account_number_test="8720796420906",
            client_id_env_test="GS1_CID",
            client_secret_env_test="GS1_SEC",
        ),
        export=ExportConfig(path="input/acme.xlsx"),
        wordpress=WordPressConfig(
            site_url="https://wp.test", username="bot", app_password_env="WP_PASS"
        ),
        media=media,
    )


def _media(tmp_path: Path, *, map_path: str | None = None) -> MediaConfig:
    return MediaConfig(
        video_folders={"nl": str(tmp_path / "nl"), "fr": str(tmp_path / "fr")},
        video_map_path=map_path,
    )


def _product(gtin: str, nl: str) -> ProductRecord:
    return ProductRecord(gtin=gtin, brand="Acme", product_name=LocalisedText(values={"nl": nl}))


def _write_products(path: Path, products: list[ProductRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([p.model_dump(mode="json") for p in products]), encoding="utf-8")


def _make_folder(base: Path, name: str, files: list[str]) -> None:
    folder = base / name
    folder.mkdir(parents=True, exist_ok=True)
    for f in files:
        (folder / f).write_bytes(b"x")


def _patch_client(monkeypatch: pytest.MonkeyPatch, cfg: ClientConfig) -> None:
    monkeypatch.setattr(build_video_map, "get_client", lambda _cid: cfg)


# --- Draft mode --------------------------------------------------------------


def test_draft_lists_every_video_with_blank_gtin_and_hints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config(_media(tmp_path)))
    _make_folder(tmp_path, "nl", ["DrainSticks_NL.mpeg"])
    _make_folder(tmp_path, "fr", ["DrainSticks_FR.mpeg"])
    products = tmp_path / "products.json"
    _write_products(products, [_product("08713195001234", "reinigingssticks")])

    code = build_video_map.main(["acme", "--products", str(products)])

    out = capsys.readouterr().out
    assert code == 0
    assert "nl:" in out and "fr:" in out
    assert "DrainSticks_NL.mpeg" in out
    assert 'gtin: ""' in out
    assert "hint" in out.lower()


# --- Check mode --------------------------------------------------------------


def test_check_exits_1_on_unconfirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mapping = tmp_path / "mapping.yml"
    mapping.write_text('nl:\n  - {file: "A_NL.mpeg", gtin: ""}\n', encoding="utf-8")
    _patch_client(monkeypatch, _make_config(_media(tmp_path, map_path=str(mapping))))
    _make_folder(tmp_path, "nl", ["A_NL.mpeg"])
    _make_folder(tmp_path, "fr", [])
    monkeypatch.chdir(tmp_path)

    code = build_video_map.main(["acme", "--check"])

    assert code == 1
    assert "video_unconfirmed" in capsys.readouterr().err


def test_check_exits_0_when_all_confirmed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mapping = tmp_path / "mapping.yml"
    mapping.write_text('nl:\n  - {file: "A_NL.mpeg", gtin: "08713195001234"}\n', encoding="utf-8")
    _patch_client(monkeypatch, _make_config(_media(tmp_path, map_path=str(mapping))))
    _make_folder(tmp_path, "nl", ["A_NL.mpeg"])
    _make_folder(tmp_path, "fr", [])
    monkeypatch.chdir(tmp_path)

    assert build_video_map.main(["acme", "--check"]) == 0


def test_check_writes_video_map_issues_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mapping = tmp_path / "mapping.yml"
    mapping.write_text('nl:\n  - {file: "A_NL.mpeg", gtin: ""}\n', encoding="utf-8")
    _patch_client(monkeypatch, _make_config(_media(tmp_path, map_path=str(mapping))))
    _make_folder(tmp_path, "nl", ["A_NL.mpeg"])
    _make_folder(tmp_path, "fr", [])
    monkeypatch.chdir(tmp_path)

    build_video_map.main(["acme", "--check"])

    issues_path = tmp_path / "output" / "acme" / "data" / "video_map_issues.json"
    assert issues_path.exists()
    payload = json.loads(issues_path.read_text(encoding="utf-8"))
    assert any(item["issue"] == "video_unconfirmed" for item in payload)


def test_check_writes_empty_issues_when_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mapping = tmp_path / "mapping.yml"
    mapping.write_text('nl:\n  - {file: "A_NL.mpeg", gtin: "08713195001234"}\n', encoding="utf-8")
    _patch_client(monkeypatch, _make_config(_media(tmp_path, map_path=str(mapping))))
    _make_folder(tmp_path, "nl", ["A_NL.mpeg"])
    _make_folder(tmp_path, "fr", [])
    monkeypatch.chdir(tmp_path)

    build_video_map.main(["acme", "--check"])

    issues_path = tmp_path / "output" / "acme" / "data" / "video_map_issues.json"
    assert json.loads(issues_path.read_text(encoding="utf-8")) == []


# --- Usage errors ------------------------------------------------------------


def test_no_media_config_is_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config(media=None))
    products = tmp_path / "products.json"
    _write_products(products, [_product("08713195001234", "x")])

    code = build_video_map.main(["acme", "--products", str(products)])

    assert code == 2
    assert "no media config" in capsys.readouterr().err


def test_check_without_map_path_is_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config(_media(tmp_path, map_path=None)))
    _make_folder(tmp_path, "nl", [])
    _make_folder(tmp_path, "fr", [])

    code = build_video_map.main(["acme", "--check"])

    assert code == 2
    assert "video_map_path" in capsys.readouterr().err
