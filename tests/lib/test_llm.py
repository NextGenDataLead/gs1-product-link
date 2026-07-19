"""Tests for the Anthropic API backend (``lib.llm``).

The client is exercised over a mocked Messages API (``pytest-httpx``): the request shape (model,
temperature, forced tool, auth headers, rendered inputs) is asserted, canned tool results are parsed
into :class:`~lib.generator.GenerationResult`, and the failure paths (missing key, HTTP error,
missing/malformed tool call, retry) each raise the expected typed error. No real network is touched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from lib.config import GeneratorConfig
from lib.errors import GeneratorError, LLMAPIError, MissingCredentialError
from lib.generator import MODE_TIGHTEN, GenerationInputs, GenerationRequest
from lib.llm import ANTHROPIC_VERSION, AnthropicClient, load_voice_template

_KEY_ENV = "TEST_ANTHROPIC_KEY"
_VOICE = "You are Noviplast's copywriter. Write terse Dutch taglines."


def _config(**overrides: Any) -> GeneratorConfig:
    params: dict[str, Any] = {
        "enabled": True,
        "model": "claude-sonnet-5",
        "prompt_version": "v1",
        "api_key_env": _KEY_ENV,
        "max_tokens": 512,
    }
    params.update(overrides)
    return GeneratorConfig(**params)


def _request(
    *, mode: str = "generate", needs_name: bool = False, candidates: list[str] | None = None
) -> GenerationRequest:
    return GenerationRequest(
        gtin="04895069002951",
        language="nl",
        inputs=GenerationInputs(
            functional_name="voegstrijker",
            marketing_message="Het perfecte gereedschap voor alle elastische voegen",
            net_content="4 H87",
        ),
        input_fingerprint="fp-1",
        needs_name=needs_name,
        mode=mode,
        candidates=candidates or [],
    )


def _tool_response(usps: list[str], product_name: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"usps": usps}
    if product_name is not None:
        payload["product_name"] = product_name
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5",
        "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": "tu_1", "name": "produce_copy", "input": payload}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


# --- happy path & request shape ----------------------------------------------


def test_generate_copy_parses_tool_result_and_sends_expected_request(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_KEY_ENV, "sk-test-123")
    httpx_mock.add_response(json=_tool_response(["Voor gladde voegen", "Op alle voegen"]))

    with AnthropicClient(_config(), _VOICE, sleep=lambda _: None) as client:
        result = client.generate_copy(_request())

    assert result.usps == ["Voor gladde voegen", "Op alle voegen"]

    request = httpx_mock.get_requests()[-1]
    assert request.headers["x-api-key"] == "sk-test-123"
    assert request.headers["anthropic-version"] == ANTHROPIC_VERSION
    body = json.loads(request.content)
    assert body["model"] == "claude-sonnet-5"
    assert body["temperature"] == 0
    assert body["max_tokens"] == 512
    assert body["system"] == _VOICE
    assert body["tool_choice"] == {"type": "tool", "name": "produce_copy"}
    assert body["tools"][0]["name"] == "produce_copy"
    # the request inputs are rendered into the user message
    user_message = body["messages"][0]["content"]
    assert "Het perfecte gereedschap voor alle elastische voegen" in user_message
    assert "04895069002951" in user_message


def test_tighten_request_renders_candidates(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_KEY_ENV, "sk-test")
    httpx_mock.add_response(json=_tool_response(["Kort", "Bullet"]))

    with AnthropicClient(_config(), _VOICE, sleep=lambda _: None) as client:
        client.generate_copy(
            _request(mode=MODE_TIGHTEN, candidates=["Een hele lange zin die ingekort moet worden"])
        )

    body = json.loads(httpx_mock.get_requests()[-1].content)
    assert "Een hele lange zin die ingekort moet worden" in body["messages"][0]["content"]


def test_needs_name_result_carries_product_name(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_KEY_ENV, "sk-test")
    httpx_mock.add_response(json=_tool_response(["Slogan"], product_name="Lisse-joints"))

    with AnthropicClient(_config(), _VOICE, sleep=lambda _: None) as client:
        result = client.generate_copy(_request(needs_name=True))

    assert result.product_name == "Lisse-joints"


# --- failure paths -----------------------------------------------------------


def test_missing_key_raises_missing_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_KEY_ENV, raising=False)
    client = AnthropicClient(_config(), _VOICE, sleep=lambda _: None)

    with pytest.raises(MissingCredentialError, match=_KEY_ENV):
        client.generate_copy(_request())


def test_http_error_raises_llm_api_error(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_KEY_ENV, "sk-test")
    httpx_mock.add_response(status_code=400, text="bad request")

    with (
        AnthropicClient(_config(), _VOICE, sleep=lambda _: None) as client,
        pytest.raises(LLMAPIError) as exc_info,
    ):
        client.generate_copy(_request())

    assert exc_info.value.status_code == 400


def test_response_without_tool_use_raises(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_KEY_ENV, "sk-test")
    httpx_mock.add_response(
        json={"content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn"}
    )

    with (
        AnthropicClient(_config(), _VOICE, sleep=lambda _: None) as client,
        pytest.raises(LLMAPIError, match="no produce_copy tool call"),
    ):
        client.generate_copy(_request())


def test_malformed_tool_input_raises(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_KEY_ENV, "sk-test")
    httpx_mock.add_response(json=_tool_response([]))  # empty usps violates min_length=1

    with (
        AnthropicClient(_config(), _VOICE, sleep=lambda _: None) as client,
        pytest.raises(LLMAPIError, match="malformed produce_copy input"),
    ):
        client.generate_copy(_request())


def test_retries_on_429_then_succeeds(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_KEY_ENV, "sk-test")
    httpx_mock.add_response(status_code=429)
    httpx_mock.add_response(json=_tool_response(["Tagline"]))

    with AnthropicClient(_config(), _VOICE, sleep=lambda _: None) as client:
        result = client.generate_copy(_request())

    assert result.usps == ["Tagline"]
    assert len(httpx_mock.get_requests()) == 2


# --- voice template loader ---------------------------------------------------


def test_load_voice_template_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "prompts" / "acme" / "generation.v3.md"
    path.parent.mkdir(parents=True)
    path.write_text("voice text", encoding="utf-8")

    assert load_voice_template("acme", "v3") == "voice text"


def test_load_voice_template_missing_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(GeneratorError, match="no voice template"):
        load_voice_template("acme", "v9")
