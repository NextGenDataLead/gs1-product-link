"""Tests for scripts/run_generate.py (generator SPEC, commit 4).

run_generate is the producer-agnostic spine: it prefills verbatim copy, computes the pending
gaps, and moves them through the cache via emit/ingest or an injected ``LLMClient``. No LLM is
involved here — the producer is simulated with a fake client or a hand-written results file.
Cache/contract logic itself is covered in ``tests/lib/test_generator.py``; these tests drive
``main`` and the seam over a temp working directory and a fake ``get_client``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from lib.config import ClientConfig, ExportConfig, GS1Config, WordPressConfig
from lib.generator import (
    ORIGIN_GENERATED,
    GeneratedCache,
    GenerationRequest,
    GenerationResult,
    load_cache,
    pending_requests,
    prefill_from_feed,
)
from lib.records import LocalisedText, ProductRecord
from scripts import run_generate

GTIN_A = "08713195007359"
GTIN_B = "08713195007360"
_NOW = datetime(2026, 7, 18, tzinfo=UTC)


# --- Builders ----------------------------------------------------------------


def _make_config(languages: list[str] | None = None) -> ClientConfig:
    return ClientConfig(
        client_id="noviplast",
        display_name="Noviplast",
        gs1=GS1Config(
            account_number_test="8720796420906",
            client_id_env_test="GS1_CID",
            client_secret_env_test="GS1_SEC",
        ),
        export=ExportConfig(path="input/noviplast.xlsx"),
        wordpress=WordPressConfig(
            site_url="https://wp.test",
            username="bot",
            app_password_env="WP_PASS",
            default_language="nl",
            languages=languages or ["nl"],
        ),
    )


def _product(
    gtin: str = GTIN_A, *, long_text: str | None = None, short_1067: str | None = None
) -> ProductRecord:
    extras: dict[str, str] = {"material": "kunststof"}
    kwargs: dict[str, Any] = {
        "gtin": gtin,
        "brand": "Noviplast",
        "product_name": LocalisedText(values={"nl": "Bewateringpin"}),
        "net_content": "6 H87",
        "description_short": LocalisedText(values={"nl": long_text or "Water voor je planten"}),
        "extras": extras,
    }
    if short_1067 is not None:
        kwargs["description_long"] = LocalisedText(values={"nl": short_1067})
    return ProductRecord(**kwargs)


def _write_products(client_id: str, products: list[ProductRecord]) -> None:
    path = Path("output") / client_id / "data" / "products.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [p.model_dump(mode="json") for p in products]
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_results(client_id: str, results: list[dict[str, Any]]) -> None:
    path = Path("output") / client_id / "data" / "generation_results.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"client_id": client_id, "results": results}), encoding="utf-8"
    )


def _patch_client(monkeypatch: pytest.MonkeyPatch, cfg: ClientConfig) -> None:
    monkeypatch.setattr(run_generate, "get_client", lambda _cid: cfg)


def _read_requests_file() -> run_generate.RequestsFile:
    path = Path("output/noviplast/data/generation_requests.json")
    return run_generate.RequestsFile.model_validate(json.loads(path.read_text(encoding="utf-8")))


class _FakeClient:
    """A producer that echoes a deterministic tagline + bullet per request."""

    def generate_copy(self, request: GenerationRequest) -> GenerationResult:
        return GenerationResult(usps=[f"Tagline {request.language}", "Bullet"])


# --- emit --------------------------------------------------------------------


def test_emit_writes_pending_requests_and_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config())
    _write_products("noviplast", [_product()])  # no 1067 -> generate

    code = run_generate.main(["noviplast"])

    assert code == 0
    payload = _read_requests_file()
    assert payload.client_id == "noviplast"
    assert len(payload.requests) == 1
    assert payload.requests[0].mode == "generate"


def test_emit_coverage_line_to_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config())
    _write_products("noviplast", [_product()])

    run_generate.main(["noviplast", "--emit"])

    err = capsys.readouterr().err
    assert "0/1 units cached" in err
    assert "1 pending (0 tighten, 1 generate)" in err


def test_emit_prefills_short_1067_verbatim_and_omits_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config())
    _write_products("noviplast", [_product(short_1067="Kort en krachtig")])

    run_generate.main(["noviplast"])

    # verbatim unit is not a pending request...
    assert _read_requests_file().requests == []
    # ...and its feed copy is persisted in the cache
    entry = load_cache("noviplast").get(GTIN_A, "nl")
    assert entry is not None
    assert entry.origin == "feed"
    assert entry.usps == ["Kort en krachtig"]


def test_emit_writes_empty_requests_file_when_nothing_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config())
    _write_products("noviplast", [_product(short_1067="Kort en krachtig")])

    run_generate.main(["noviplast"])

    assert Path("output/noviplast/data/generation_requests.json").exists()
    assert _read_requests_file().requests == []


# --- ingest ------------------------------------------------------------------


def test_ingest_applies_results_into_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config())
    _write_products("noviplast", [_product()])
    _write_results("noviplast", [{"gtin": GTIN_A, "language": "nl", "usps": ["Slogan", "Punt"]}])

    code = run_generate.main(["noviplast", "--ingest"])

    assert code == 0
    entry = load_cache("noviplast").get(GTIN_A, "nl")
    assert entry is not None
    assert entry.usps == ["Slogan", "Punt"]
    assert entry.origin == ORIGIN_GENERATED
    assert entry.provenance == "cowork"
    # coverage reflects the post-ingest state: the one gap is now cached
    assert "ingested 1 result(s), skipped 0; 1/1 units cached; 0 pending" in capsys.readouterr().err


def test_emit_then_ingest_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config())
    _write_products("noviplast", [_product()])

    run_generate.main(["noviplast", "--emit"])
    request = _read_requests_file().requests[0]
    _write_results(
        "noviplast",
        [
            {
                "gtin": request.gtin,
                "language": request.language,
                "usps": ["Slogan", "Punt"],
                "input_fingerprint": request.input_fingerprint,
            }
        ],
    )

    code = run_generate.main(["noviplast", "--ingest"])

    assert code == 0
    assert load_cache("noviplast").get(GTIN_A, "nl") is not None


def test_ingest_skips_result_with_no_pending_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config())
    _write_products("noviplast", [_product()])
    _write_results("noviplast", [{"gtin": GTIN_B, "language": "nl", "usps": ["X"]}])  # unknown gtin

    code = run_generate.main(["noviplast", "--ingest"])

    assert code == 0
    assert load_cache("noviplast").get(GTIN_B, "nl") is None
    assert "ingested 0 result(s), skipped 1" in capsys.readouterr().err


def test_ingest_skips_stale_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config())
    _write_products("noviplast", [_product()])
    _write_results(
        "noviplast",
        [
            {
                "gtin": GTIN_A,
                "language": "nl",
                "usps": ["Slogan"],
                "input_fingerprint": "stale-does-not-match",
            }
        ],
    )

    run_generate.main(["noviplast", "--ingest"])

    assert load_cache("noviplast").get(GTIN_A, "nl") is None  # stale copy not cached


def test_ingest_rejects_wrong_client_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config())
    _write_products("noviplast", [_product()])
    path = Path("output/noviplast/data/generation_results.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"client_id": "other", "results": []}), encoding="utf-8")

    code = run_generate.main(["noviplast", "--ingest"])

    assert code == 2
    assert "config error" in capsys.readouterr().err


def test_ingest_missing_results_file_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config())
    _write_products("noviplast", [_product()])

    assert run_generate.main(["noviplast", "--ingest"]) == 2


# --- the LLMClient seam ------------------------------------------------------


def test_run_producer_fills_cache_via_fake_client() -> None:
    cache = GeneratedCache(client_id="noviplast")
    product = _product()
    prefill_from_feed([product], cache, ["nl"], "v1", now=_NOW)
    requests = pending_requests([product], cache, ["nl"], "v1")

    filled = run_generate.run_producer(
        cache, requests, _FakeClient(), provenance="api:test", now=_NOW
    )

    assert filled == 1
    entry = cache.get(GTIN_A, "nl")
    assert entry is not None
    assert entry.usps == ["Tagline nl", "Bullet"]
    assert entry.origin == ORIGIN_GENERATED
    assert entry.provenance == "api:test"


# --- shared failure paths ----------------------------------------------------


def test_missing_products_file_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_client(monkeypatch, _make_config())

    assert run_generate.main(["noviplast"]) == 2
