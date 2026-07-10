"""Tests for the GS1 Digital Link API v2 client (IMPLEMENTATION_SPEC §4.3, §5, §6.3).

All HTTP is mocked with ``pytest-httpx``; retry backoff is made instant by
injecting a no-op ``sleep``.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lib.errors import ConfigError, GS1APIError, MissingCredentialError
from lib.gs1_dl_client import (
    BulkEntry,
    GS1Config,
    GS1DigitalLinkClient,
    LinkInput,
    ResolverSettings,
)

TOKEN_ENV = "GS1_TOKEN_TEST"
TOKEN_VALUE = "SECRET-TOKEN-XYZ"
TEST_HOST = "https://gs1nl-api-acc.gs1.nl"

SAMPLE_LINK: LinkInput = {
    "link_type": "pip",
    "language": "nl",
    "link_title": "Product page",
    "target_url": "https://example.com/p/123",
    "default_link_type": True,
    "public": True,
    "media_type": "text/html",
}


def make_config(**overrides: object) -> GS1Config:
    params: dict[str, object] = {
        "account_number": "8712345000003",
        "token_env": TOKEN_ENV,
    }
    params.update(overrides)
    return GS1Config(**params)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN_VALUE)


def make_client(config: GS1Config | None = None) -> tuple[GS1DigitalLinkClient, list[float]]:
    sleeps: list[float] = []
    client = GS1DigitalLinkClient(config or make_config(), sleep=sleeps.append)
    return client, sleeps


# --- Happy paths & path anomalies --------------------------------------------


def test_upsert_posts_lowercase_path_and_camelcase_body(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", status_code=200)
    client, _ = make_client()

    client.upsert("8712345678905", "Test product", [SAMPLE_LINK])

    request = httpx_mock.get_requests()[0]
    assert request.url.path == "/digitallinkv2/v2/digitallink"
    assert request.headers["Authorization"] == f"Bearer {TOKEN_VALUE}"
    body = json.loads(request.content)
    assert body["accountNumber"] == "8712345000003"
    assert body["identificationKeyType"] == "Gtin"
    assert body["identificationKey"] == "08712345678905"  # zero-padded to 14
    assert body["resolverSettings"] == {"useGS1Resolver": True, "resolverDomainName": None}
    assert body["links"][0] == {
        "linkType": "pip",
        "language": "nl",
        "linkTitle": "Product page",
        "targetUrl": "https://example.com/p/123",
        "defaultLinkType": True,
        "public": True,
        "mediaType": "text/html",
    }
    assert body["applicationIdentifiers"] == []


def test_upsert_idempotent_body_then_single_get(httpx_mock: HTTPXMock) -> None:
    # §6.3: same input twice -> identical request bodies; GET returns one record.
    httpx_mock.add_response(method="POST", status_code=200)
    httpx_mock.add_response(method="POST", status_code=200)
    httpx_mock.add_response(
        method="GET",
        status_code=200,
        json={"identificationKey": "08712345678905", "isEnabled": True},
    )
    client, _ = make_client()

    client.upsert("8712345678905", "Test product", [SAMPLE_LINK])
    client.upsert("8712345678905", "Test product", [SAMPLE_LINK])
    record = client.get("8712345678905")

    posts = [r for r in httpx_mock.get_requests() if r.method == "POST"]
    assert posts[0].content == posts[1].content
    assert record == {"identificationKey": "08712345678905", "isEnabled": True}


def test_upsert_bulk_batches_into_groups(httpx_mock: HTTPXMock) -> None:
    for _ in range(3):
        httpx_mock.add_response(method="POST", status_code=200, json={"ok": True})
    client, _ = make_client(make_config(batch_size=2))
    entries: list[BulkEntry] = [
        {"gtin": f"000000000000{i}"[-13:], "item_description": f"p{i}", "links": [SAMPLE_LINK]}
        for i in range(5)
    ]

    result = client.upsert_bulk(entries)

    requests = httpx_mock.get_requests()
    assert all(r.url.path == "/digitallinkv2/v2/digitallinks" for r in requests)
    batch_sizes = [len(json.loads(r.content)) for r in requests]
    assert batch_sizes == [2, 2, 1]
    assert result["total"] == 5
    assert result["batches"] == 3
    assert result["status_codes"] == [200, 200, 200]


def test_get_uses_capital_l_path_and_zfill(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="GET", status_code=200, json={"identificationKey": "x"})
    client, _ = make_client()

    client.get("8712345678905")

    request = httpx_mock.get_requests()[0]
    assert request.url.path == "/digitallinkv2/v2/digitalLink/Gtin/08712345678905"


def test_get_missing_returns_none(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="GET", status_code=404, text="not found")
    client, _ = make_client()

    assert client.get("00000000000000") is None


def test_set_enabled_patches_activation_status(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="PATCH", status_code=204)
    client, _ = make_client()

    client.set_enabled("8712345678905", is_enabled=False)

    request = httpx_mock.get_requests()[0]
    assert request.method == "PATCH"
    assert request.url.path == "/digitallinkv2/v2/digitalLink/Gtin/08712345678905/activationStatus"
    assert json.loads(request.content) == {"isEnabled": False}


def test_validate_draft_path_has_no_v2_segment(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", status_code=200, json={"validationResult": {"isValid": True}}
    )
    client, _ = make_client()

    result = client.validate_draft("8712345678905")

    request = httpx_mock.get_requests()[0]
    assert request.url.path == "/digitallinkv2/digitalLink/validateDraft"
    assert result["validationResult"] == {"isValid": True}


# --- Retry policy (§4.3 / §5.1) ----------------------------------------------


def test_retry_on_429_honours_retry_after(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", status_code=429, headers={"Retry-After": "2"})
    httpx_mock.add_response(method="POST", status_code=200)
    client, sleeps = make_client()

    client.upsert("8712345678905", "p", [SAMPLE_LINK])

    assert len(httpx_mock.get_requests()) == 2
    assert sleeps == [2.0]


def test_retry_on_5xx_then_success(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", status_code=500)
    httpx_mock.add_response(method="POST", status_code=503)
    httpx_mock.add_response(method="POST", status_code=200)
    client, sleeps = make_client()

    client.upsert("8712345678905", "p", [SAMPLE_LINK])

    assert len(httpx_mock.get_requests()) == 3
    assert sleeps == [0.5, 1.0]  # exponential base 0.5s


def test_5xx_exhaustion_raises(httpx_mock: HTTPXMock) -> None:
    for _ in range(3):
        httpx_mock.add_response(method="POST", status_code=500, text="boom")
    client, _ = make_client()

    with pytest.raises(GS1APIError) as exc:
        client.upsert("8712345678905", "p", [SAMPLE_LINK])

    assert exc.value.status_code == 500
    assert len(httpx_mock.get_requests()) == 3


def test_429_exhaustion_raises(httpx_mock: HTTPXMock) -> None:
    for _ in range(5):
        httpx_mock.add_response(method="POST", status_code=429)
    client, _ = make_client()

    with pytest.raises(GS1APIError) as exc:
        client.upsert("8712345678905", "p", [SAMPLE_LINK])

    assert exc.value.status_code == 429
    assert len(httpx_mock.get_requests()) == 5


def test_network_error_retried_as_5xx(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("no route"))
    httpx_mock.add_exception(httpx.ConnectError("no route"))
    httpx_mock.add_response(method="POST", status_code=200)
    client, sleeps = make_client()

    client.upsert("8712345678905", "p", [SAMPLE_LINK])

    assert len(httpx_mock.get_requests()) == 3
    assert sleeps == [0.5, 1.0]


def test_network_error_exhaustion_raises(httpx_mock: HTTPXMock) -> None:
    for _ in range(3):
        httpx_mock.add_exception(httpx.ReadTimeout("slow"))
    client, _ = make_client()

    with pytest.raises(GS1APIError) as exc:
        client.upsert("8712345678905", "p", [SAMPLE_LINK])

    assert exc.value.status_code == 0  # network-error sentinel


# --- Error parsing (§5.1) ----------------------------------------------------


def test_400_populates_error_results(httpx_mock: HTTPXMock) -> None:
    error_body = [
        {
            "identifier": "08712345678905",
            "errors": [{"code": "GS1_INVALID_GTIN", "message": "GTIN checksum invalid"}],
        }
    ]
    httpx_mock.add_response(method="POST", status_code=400, json=error_body)
    client, _ = make_client()

    with pytest.raises(GS1APIError) as exc:
        client.upsert("8712345678905", "p", [SAMPLE_LINK])

    assert exc.value.status_code == 400
    assert exc.value.error_results == error_body
    assert len(httpx_mock.get_requests()) == 1  # 4xx not retried


def test_400_malformed_body_leaves_error_results_none(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", status_code=400, text="not json at all")
    client, _ = make_client()

    with pytest.raises(GS1APIError) as exc:
        client.upsert("8712345678905", "p", [SAMPLE_LINK])

    assert exc.value.error_results is None
    assert exc.value.response_body == "not json at all"


# --- Auth (§4.3) -------------------------------------------------------------


def test_401_bearer_falls_back_to_raw(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", status_code=401)
    httpx_mock.add_response(method="POST", status_code=200)
    client, _ = make_client()

    client.upsert("8712345678905", "p", [SAMPLE_LINK])

    requests = httpx_mock.get_requests()
    assert requests[0].headers["Authorization"] == f"Bearer {TOKEN_VALUE}"
    assert requests[1].headers["Authorization"] == TOKEN_VALUE  # raw fallback


def test_401_persisting_after_fallback_raises(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", status_code=401)
    httpx_mock.add_response(method="POST", status_code=401)
    client, _ = make_client()

    with pytest.raises(GS1APIError) as exc:
        client.upsert("8712345678905", "p", [SAMPLE_LINK])

    assert exc.value.status_code == 401
    assert len(httpx_mock.get_requests()) == 2


def test_raw_scheme_sends_bare_token(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", status_code=200)
    client, _ = make_client(make_config(auth_scheme="raw"))

    client.upsert("8712345678905", "p", [SAMPLE_LINK])

    assert httpx_mock.get_requests()[0].headers["Authorization"] == TOKEN_VALUE


def test_unknown_auth_scheme_raises_config_error() -> None:
    client, _ = make_client()

    with pytest.raises(ConfigError):
        client._auth_header("weird")


def test_missing_token_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    client, _ = make_client()

    with pytest.raises(MissingCredentialError):
        client.upsert("8712345678905", "p", [SAMPLE_LINK])


# --- PII scrubbing DoD (§5.2) ------------------------------------------------


def test_token_never_appears_in_logs(
    httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
) -> None:
    # The 400 body carries a sensitive-looking field; the ERROR log must scrub it,
    # and the request token must never surface in any log record.
    httpx_mock.add_response(
        method="POST",
        status_code=400,
        json=[
            {
                "identifier": "08712345678905",
                "errors": [{"code": "X", "message": "Y"}],
                "token": "leaked-in-body",
            }
        ],
    )
    client, _ = make_client()

    with (
        caplog.at_level(logging.DEBUG, logger="lib.gs1_dl_client"),
        pytest.raises(GS1APIError),
    ):
        client.upsert("8712345678905", "p", [SAMPLE_LINK])

    log_text = caplog.text
    assert TOKEN_VALUE not in log_text
    assert "leaked-in-body" not in log_text
    assert "[REDACTED]" in log_text


def test_resolver_settings_override_flows_into_body(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", status_code=200)
    config = make_config(
        resolver_settings=ResolverSettings(
            use_gs1_resolver=False, resolver_domain_name="id.example.com"
        )
    )
    client, _ = make_client(config)

    client.upsert("8712345678905", "p", [SAMPLE_LINK])

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["resolverSettings"] == {
        "useGS1Resolver": False,
        "resolverDomainName": "id.example.com",
    }
