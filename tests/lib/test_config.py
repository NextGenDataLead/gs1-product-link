"""Unit tests for the client config loader (IMPLEMENTATION_SPEC §2.4, §4.2)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from lib.config import GS1Config, get_client, load_clients
from lib.errors import ConfigError, ExportParseError
from lib.gs1_dl_client import GS1Config as ResolvedGS1Config


def _base_client() -> dict[str, Any]:
    return {
        "display_name": "Test Co",
        "gs1": {
            "account_number_test": "8720796420906",
            "client_id_env_test": "TEST_GS1_ID",
            "client_secret_env_test": "TEST_GS1_SECRET",
        },
        "export": {"path": "./input/test/products.xlsx"},
        "wordpress": {
            "site_url": "https://example.test",
            "username": "bot",
            "app_password_env": "TEST_WP_PASS",
        },
    }


def _write_config(
    tmp_path: Path, client: dict[str, Any], defaults: dict[str, Any] | None = None
) -> str:
    data: dict[str, Any] = {"version": 1, "clients": {"acme": client}}
    if defaults is not None:
        data["defaults"] = defaults
    path = tmp_path / "clients.yml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return str(path)


# --- Loading & defaults ------------------------------------------------------


def test_loads_example_config_with_defaults_applied() -> None:
    clients = load_clients("clients.example.yml")

    noviplast = clients["noviplast"]
    assert noviplast.display_name == "Noviplast B.V."
    assert noviplast.gs1.batch_size == 50  # inherited from defaults
    assert noviplast.wordpress.post_status == "publish"  # inherited from defaults
    assert noviplast.wordpress.multilingual_plugin == "polylang"  # client override


def test_get_client_returns_config(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _base_client())
    assert get_client("acme", path).display_name == "Test Co"


def test_get_client_unknown_id_raises(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _base_client())
    with pytest.raises(ConfigError, match="unknown client_id"):
        get_client("nope", path)


def test_schema_invalid_config_raises_config_error(tmp_path: Path) -> None:
    data = {"version": 2, "clients": {"acme": _base_client()}}  # version const is 1
    path = tmp_path / "clients.yml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid"):
        load_clients(path)


# --- Edge case E6 ------------------------------------------------------------


def test_e6_flat_invalid_column_target_raises(tmp_path: Path) -> None:
    client = _base_client()
    client["export"] = {"path": "x.xlsx", "column_map": {"Col": "not_a_field"}}
    path = _write_config(tmp_path, client)
    with pytest.raises(ExportParseError, match="not_a_field"):
        load_clients(path)


def test_e6_gdsn_invalid_field_raises(tmp_path: Path) -> None:
    client = _base_client()
    client["export"] = {
        "format": "gdsn",
        "path": "x.xlsx",
        "gdsn_map": {"bogus_field": {"sheet": "S", "attribute": "1"}},
    }
    path = _write_config(tmp_path, client)
    with pytest.raises(ExportParseError, match="bogus_field"):
        load_clients(path)


def test_e6_accepts_valid_targets(tmp_path: Path) -> None:
    client = _base_client()
    client["export"] = {
        "format": "gdsn",
        "path": "x.xlsx",
        "gdsn_map": {"product_name": {"sheet": "TradeItemDescription", "attribute": "3297"}},
    }
    path = _write_config(tmp_path, client)
    source = load_clients(path)["acme"].export.gdsn_map["product_name"]
    assert source.sheet == "TradeItemDescription"


# --- GS1Config.resolve bridge ------------------------------------------------


def test_resolve_returns_phase2_shape() -> None:
    cfg = GS1Config(
        account_number_test="8720796420906",
        client_id_env_test="ID_ENV",
        client_secret_env_test="SECRET_ENV",
    )
    resolved = cfg.resolve("test")

    assert isinstance(resolved, ResolvedGS1Config)
    assert resolved.account_number == "8720796420906"
    assert resolved.environment == "test"
    assert resolved.client_id_env == "ID_ENV"


def test_resolve_production_without_credentials_raises() -> None:
    cfg = GS1Config(
        account_number_test="8720796420906",
        client_id_env_test="ID_ENV",
        client_secret_env_test="SECRET_ENV",
    )
    with pytest.raises(ConfigError, match="production"):
        cfg.resolve("production")


# --- Lazy secrets ------------------------------------------------------------


def test_load_does_not_read_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear every candidate secret env var; loading must still succeed and only
    # carry the env-var *names*, never resolved values.
    for name in ("TEST_GS1_ID", "TEST_GS1_SECRET", "TEST_WP_PASS"):
        monkeypatch.delenv(name, raising=False)
    assert "TEST_GS1_ID" not in os.environ

    client = get_client("acme", _write_config(tmp_path, _base_client()))

    assert client.gs1.client_id_env_test == "TEST_GS1_ID"
    assert client.wordpress.app_password_env == "TEST_WP_PASS"
