"""Headless Anthropic Messages API backend for the content generator (generator SPEC, commit 6).

:class:`AnthropicClient` implements the :class:`lib.generator.LLMClient` protocol over the Messages
API using **sync httpx** (IMPLEMENTATION_SPEC §1: sync httpx only — no ``anthropic`` SDK), so
``scripts/run_generate.py --backend api`` can fill the cache unattended. It shares the cache and
contract seam with the Cowork-native producer: same :class:`~lib.generator.GenerationRequest` in,
same :class:`~lib.generator.GenerationResult` out, same versioned voice template
(``prompts/{client}/generation.{prompt_version}.md``). Determinism comes from ``temperature=0``, a
pinned model, and the versioned prompt; the cache fingerprint excludes the model, so the two
producers are interchangeable.

The model answers through one forced tool call (:data:`_PRODUCE_COPY_TOOL`), so the result is a
strict, parseable ``{"usps": [...], "product_name"?: "..."}`` rather than free text. The API key is
read lazily from the env var named in the client's :class:`~lib.config.GeneratorConfig`, raising
:class:`~lib.errors.MissingCredentialError` only when a call is made (as ``lib.wp_client`` does).
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path
from typing import Any, Final, cast

import httpx
from pydantic import ValidationError

from lib.config import GeneratorConfig
from lib.errors import GeneratorError, LLMAPIError, MissingCredentialError
from lib.generator import MODE_TIGHTEN, GenerationRequest, GenerationResult

_log = logging.getLogger(__name__)

#: The Anthropic Messages endpoint and the pinned API version header.
MESSAGES_URL: Final = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION: Final = "2023-06-01"
_DEFAULT_TIMEOUT: Final = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)

#: Retry budget for 429/5xx, mirroring ``lib.wp_client``/``lib.gs1_dl_client`` at a smaller scale.
_RETRY_MAX_ATTEMPTS: Final = 4
_RETRY_BASE_SECONDS: Final = 1.0
_RETRY_MAX_SECONDS: Final = 30.0

#: Longest error body kept on a raised :class:`LLMAPIError` (bodies are unbounded otherwise).
_MAX_ERROR_BODY: Final = 500

#: The forced tool the model answers through — a strict schema keeps the result parseable.
_TOOL_NAME: Final = "produce_copy"
_PRODUCE_COPY_TOOL: Final[dict[str, Any]] = {
    "name": _TOOL_NAME,
    "description": (
        "Return the product's ranked USP list: usps[0] is the tagline, usps[1:] are the "
        "Eigenschappen benefit bullets. Include product_name only when asked to translate it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "usps": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": "Ranked USPs; [0] the tagline, the rest Eigenschappen bullets.",
            },
            "product_name": {
                "type": "string",
                "description": "Translated product name — only when the request needs a name.",
            },
        },
        "required": ["usps"],
    },
}


# --- Voice template ----------------------------------------------------------


def voice_template_path(client_id: str, prompt_version: str) -> Path:
    """Return the versioned voice template path for a client."""
    return Path("prompts") / client_id / f"generation.{prompt_version}.md"


def load_voice_template(client_id: str, prompt_version: str) -> str:
    """Load a client's few-shot voice template for a prompt version.

    Args:
        client_id: The client whose voice to load.
        prompt_version: The active prompt version (selects the file).

    Returns:
        The template text (used as the Messages API system prompt).

    Raises:
        GeneratorError: If the template for this version is missing or empty. A version bump
            without its voice file must fail loudly, not silently fall back to a stale voice.
    """
    path = voice_template_path(client_id, prompt_version)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GeneratorError(
            f"no voice template for {client_id!r} prompt_version {prompt_version!r} at {path}"
        ) from exc
    if not text.strip():
        raise GeneratorError(f"voice template at {path} is empty")
    return text


# --- Payload & parsing (pure) ------------------------------------------------


def _render_request(request: GenerationRequest) -> str:
    """Render one request into the deterministic user message (fixed field order)."""
    inputs = request.inputs
    lines = [f"GTIN {request.gtin}, language: {request.language}.", f"Mode: {request.mode}."]
    if request.mode == MODE_TIGHTEN:
        lines.append("Shorten and rank these existing feature/benefit lines (keep their meaning):")
        lines.extend(f"  - {candidate}" for candidate in request.candidates)
    else:
        lines.append(
            "Write from the marketing message below; if it is blank, write minimally from the "
            "functional name."
        )
    if request.needs_name:
        lines.append(
            f"The feed has no {request.language} name — also return product_name translated into "
            f"{request.language}."
        )
    lines.append("Inputs:")
    lines.append(f"  - functional name: {inputs.functional_name or '(none)'}")
    lines.append(f"  - marketing message (1083): {inputs.marketing_message or '(none)'}")
    if inputs.feature_benefit:
        lines.append(f"  - feature/benefit (1067): {inputs.feature_benefit}")
    context: list[str] = []
    if inputs.net_content:
        context.append(f"net content {inputs.net_content}")
    dims = [inputs.dim_height, inputs.dim_width, inputs.dim_depth]
    if all(dims):
        context.append(f"dimensions {inputs.dim_height} × {inputs.dim_width} × {inputs.dim_depth}")
    if inputs.material:
        context.append(f"material {inputs.material}")
    if context:
        lines.append(
            "Context (do NOT put these in usps — the specs block is added separately): "
            + ", ".join(context)
        )
    return "\n".join(lines)


def _build_payload(
    config: GeneratorConfig, voice: str, request: GenerationRequest
) -> dict[str, Any]:
    """Assemble the Messages API request body for one generation (temperature 0, forced tool)."""
    return {
        "model": config.model,
        "max_tokens": config.max_tokens,
        "temperature": 0,
        "system": voice,
        "messages": [{"role": "user", "content": _render_request(request)}],
        "tools": [_PRODUCE_COPY_TOOL],
        "tool_choice": {"type": "tool", "name": _TOOL_NAME},
    }


def _parse_result(data: dict[str, Any]) -> GenerationResult:
    """Extract the ``produce_copy`` tool call from a Messages API response.

    Raises:
        LLMAPIError: If the response carries no ``produce_copy`` tool call, or its input fails the
            :class:`~lib.generator.GenerationResult` contract (e.g. empty ``usps``).
    """
    for block in data.get("content", []):
        if (
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("name") == _TOOL_NAME
        ):
            payload = block.get("input") or {}
            try:
                return GenerationResult(
                    usps=payload["usps"], product_name=payload.get("product_name")
                )
            except (KeyError, ValidationError) as exc:
                raise LLMAPIError(
                    HTTPStatus.OK,
                    json.dumps(payload)[:_MAX_ERROR_BODY],
                    f"malformed produce_copy input: {exc}",
                ) from exc
    raise LLMAPIError(
        HTTPStatus.OK,
        json.dumps(data)[:_MAX_ERROR_BODY],
        "Anthropic response had no produce_copy tool call",
    )


def _is_retryable(status: int) -> bool:
    """Whether an HTTP status warrants a retry (429 or any 5xx)."""
    return status == HTTPStatus.TOO_MANY_REQUESTS or status >= HTTPStatus.INTERNAL_SERVER_ERROR


def _backoff(attempt: int) -> float:
    """Exponential backoff seconds for a 1-indexed attempt, capped."""
    return min(_RETRY_BASE_SECONDS * 2.0 ** (attempt - 1), _RETRY_MAX_SECONDS)


def _require_env(name: str) -> str:
    """Read an environment variable, raising a typed error if it is unset."""
    try:
        return os.environ[name]
    except KeyError as exc:
        raise MissingCredentialError(f"Environment variable {name!r} is not set") from exc


# --- The client --------------------------------------------------------------


class AnthropicClient:
    """Synchronous Anthropic Messages API producer implementing :class:`lib.generator.LLMClient`.

    Args:
        config: The client's generator config (model, key env var, max tokens).
        voice_template: The system prompt / few-shot voice for the active prompt version.
        timeout: Override the default httpx timeouts (tests).
        sleep: Injectable sleep so retry backoff is instant in tests.
        base_url: Override the Messages endpoint (tests).
    """

    def __init__(
        self,
        config: GeneratorConfig,
        voice_template: str,
        *,
        timeout: httpx.Timeout | None = None,
        sleep: Callable[[float], None] = time.sleep,
        base_url: str = MESSAGES_URL,
    ) -> None:
        self.config = config
        self._voice = voice_template
        self._http = httpx.Client(timeout=timeout or _DEFAULT_TIMEOUT)
        self._sleep = sleep
        self._url = base_url

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._http.close()

    def __enter__(self) -> AnthropicClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def generate_copy(self, request: GenerationRequest) -> GenerationResult:
        """Produce copy for one ``(gtin, language)`` via one forced-tool Messages call.

        Raises:
            MissingCredentialError: The configured API-key env var is unset.
            LLMAPIError: The call failed (transport, non-success status) or returned no usable
                ``produce_copy`` result.
        """
        payload = _build_payload(self.config, self._voice, request)
        data = self._request(payload)
        return _parse_result(data)

    def _headers(self) -> dict[str, str]:
        """Build the request headers; the key is read lazily so a missing secret surfaces here."""
        return {
            "x-api-key": _require_env(self.config.api_key_env),
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST to the Messages API with retry on 429/5xx and transport errors."""
        for attempt in range(1, _RETRY_MAX_ATTEMPTS + 1):
            try:
                response = self._http.post(self._url, headers=self._headers(), json=payload)
            except httpx.HTTPError as exc:
                if attempt >= _RETRY_MAX_ATTEMPTS:
                    raise LLMAPIError(
                        0, str(exc)[:_MAX_ERROR_BODY], f"Anthropic API request failed: {exc}"
                    ) from exc
                self._sleep(_backoff(attempt))
                continue
            if response.status_code == HTTPStatus.OK:
                return cast("dict[str, Any]", response.json())
            if _is_retryable(response.status_code) and attempt < _RETRY_MAX_ATTEMPTS:
                _log.warning(
                    "Anthropic API %s on attempt %d; retrying", response.status_code, attempt
                )
                self._sleep(_backoff(attempt))
                continue
            raise LLMAPIError(response.status_code, response.text[:_MAX_ERROR_BODY])
        raise LLMAPIError(0, "", "Anthropic API retries exhausted")  # pragma: no cover
