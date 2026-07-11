"""Client for the GS1 NL Digital Link API v2.

Implements ``docs/IMPLEMENTATION_SPEC.md`` §4.3 (client shape), §5.1 (error
handling matrix), and §6.3 (upsert idempotency). The hosts, path prefix, and the
path-case anomalies (capital-L ``digitalLink`` for GET/PATCH, missing ``/v2/`` in
ValidateDraft) are preserved exactly as documented in ``PROJECT_HANDOVER.md`` §4.2.

The config object is a minimal :class:`GS1Config` defined here (Phase-2 scope);
the full ``clients.yml`` loader (``lib/config.py``) arrives in Phase 3 and may
supersede or re-export these types.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from http import HTTPStatus
from typing import Final, Literal, NotRequired, TypedDict, cast

import httpx
from pydantic import BaseModel, ConfigDict, Field

from lib.errors import ConfigError, GS1APIError, MissingCredentialError
from lib.logging_setup import scrub_response_body

_log = logging.getLogger(__name__)

# --- Constants ---------------------------------------------------------------

#: Path prefix shared by every endpoint except ValidateDraft (see §4.2).
PATH_PREFIX: Final = "/digitallinkv2/v2/"

#: Environment-to-host mapping (§4.3).
_HOSTS: Final[dict[str, str]] = {
    "test": "gs1nl-api-acc.gs1.nl",
    "production": "gs1nl-api.gs1.nl",
}

# Retry policy (§4.3 / §5.1). 429 and 5xx use independent attempt budgets.
_RETRY_429_MAX_ATTEMPTS: Final = 5
_RETRY_429_BASE_SECONDS: Final = 1.0
_RETRY_429_MAX_SECONDS: Final = 60.0
_RETRY_5XX_MAX_ATTEMPTS: Final = 3
_RETRY_5XX_BASE_SECONDS: Final = 0.5
_RETRY_5XX_MAX_SECONDS: Final = 30.0

#: Default per-operation timeouts (§4.3): connect 10s, read/write 30s.
_DEFAULT_TIMEOUT: Final = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)

#: Abbreviate error bodies to this many characters when logging (§4.3).
_ERROR_BODY_LOG_LIMIT: Final = 500

#: Sentinel status code used when an error originates below HTTP (network error).
_NETWORK_ERROR_STATUS: Final = 0

# HTTP status ranges used in retry classification (bounds are exclusive on max).
_HTTP_SUCCESS_MIN: Final = 200
_HTTP_SUCCESS_MAX: Final = 300
_HTTP_SERVER_ERROR_MIN: Final = 500
_HTTP_SERVER_ERROR_MAX: Final = 600

# OAuth2 client-credentials token endpoint, on the same host as the API (§4.2).
_TOKEN_PATH: Final = "/authorization/token"
#: Re-mint the token this many seconds before it expires.
_TOKEN_REFRESH_SKEW_SECONDS: Final = 60.0
#: Fallback token lifetime if the token response omits ``expires_in``.
_DEFAULT_TOKEN_TTL_SECONDS: Final = 3600.0


# --- Config (minimal, Phase-2 scope) -----------------------------------------


class ResolverSettings(BaseModel):
    """GS1 resolver configuration (mirrors ``resolverSettings`` in §4.2)."""

    model_config = ConfigDict(frozen=True)

    use_gs1_resolver: bool = True
    resolver_domain_name: str | None = None


class GS1Config(BaseModel):
    """The subset of client config the Digital Link client needs (§4.3).

    Auth is OAuth2 client-credentials: the client mints a short-lived JWT from the
    ``client_id``/``client_secret`` held in the named environment variables and
    sends it as a ``Bearer`` token. The values are environment-specific — this
    config holds the pair already resolved for one ``environment``.

    Attributes:
        account_number: The account Digital Links are created under (differs per
            environment; taken from the token's ``accountNumber`` claim).
        client_id_env: Name of the env var holding the OAuth2 client id.
        client_secret_env: Name of the env var holding the OAuth2 client secret.
        environment: Which GS1 NL environment to target (selects the host).
        resolver_settings: Resolver configuration sent on every upsert.
        batch_size: Entries per request to the bulk endpoint.
    """

    model_config = ConfigDict(frozen=True)

    account_number: str
    client_id_env: str
    client_secret_env: str
    environment: Literal["test", "production"] = "test"
    resolver_settings: ResolverSettings = Field(default_factory=ResolverSettings)
    batch_size: int = 50


# --- Wire shapes (TypedDict per §1: TypedDict for HTTP shapes) ----------------


class LinkInput(TypedDict):
    """One resolver link, request side (snake_case; mapped to camelCase wire)."""

    link_type: str
    language: str
    link_title: str
    target_url: str
    default_link_type: bool
    public: bool
    media_type: str


class AppIdentifier(TypedDict):
    """A GS1 Application Identifier qualifier, request side."""

    identifier: str
    template_variable: str


class BulkEntry(TypedDict):
    """One entry in a bulk upsert — the single-upsert inputs minus plumbing."""

    gtin: str
    item_description: str
    links: list[LinkInput]
    is_enabled: NotRequired[bool]
    application_identifiers: NotRequired[list[AppIdentifier]]


class DigitalLinkRecord(TypedDict, total=False):
    """GET response body (``AdvancedDigitalLinkResponse``), raw v2 wire keys.

    All fields are documented optional in the v2 schema, so this is ``total=False``
    and holds the parsed JSON verbatim (camelCase keys). The exact shape is
    confirmed against captured fixtures (§13.2) before the real-env DoD items.
    """

    accountNumber: str
    identificationKeyType: str
    identificationKey: str
    isEnabled: bool
    itemDescription: str
    digitalLinkUrl: str
    resolverSettings: dict[str, object]
    links: list[dict[str, object]]
    applicationIdentifiers: list[dict[str, object]]


class ValidateDraftResult(TypedDict, total=False):
    """ValidateDraft response body (``ValidateDigitalLinkDraftResponse``)."""

    availableApplicationIdentifiers: list[dict[str, object]]
    validationResult: dict[str, object]


class BulkResult(TypedDict):
    """Summary of a bulk upsert across all internally-issued batches.

    Attributes:
        total: Number of entries submitted.
        batches: Number of HTTP requests issued (``ceil(total / batch_size)``).
        status_codes: HTTP status per batch request, in order.
        responses: Parsed JSON body per batch (shape confirmed via fixtures).
    """

    total: int
    batches: int
    status_codes: list[int]
    responses: list[object]


class GS1DigitalLinkClient:
    """Synchronous client for the GS1 NL Digital Link API v2.

    Args:
        config: The GS1 configuration for one client.
        timeout: Override the default per-operation timeouts (for tests).
        sleep: Injectable sleep function so retry backoff is instant in tests.
    """

    def __init__(
        self,
        config: GS1Config,
        *,
        timeout: httpx.Timeout | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._host = _HOSTS[config.environment]
        self._base_url = f"https://{self._host}"
        self._http = httpx.Client(timeout=timeout or _DEFAULT_TIMEOUT)
        self._sleep = sleep
        self._token: str | None = None
        self._token_expiry: float = 0.0

    # -- Lifecycle ------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._http.close()

    def __enter__(self) -> GS1DigitalLinkClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- Auth -----------------------------------------------------------------

    def _auth_header(self) -> dict[str, str]:
        """Return the ``Authorization`` header with a valid Bearer token (§4.2).

        The token is minted from client credentials and cached until it nears
        expiry. It is never logged.
        """
        return {"Authorization": f"Bearer {self._get_token()}"}

    def _get_token(self) -> str:
        """Return a cached access token, minting or refreshing it as needed."""
        if (
            self._token is not None
            and time.monotonic() < self._token_expiry - _TOKEN_REFRESH_SKEW_SECONDS
        ):
            return self._token
        return self._mint_token()

    def _mint_token(self) -> str:
        """Mint a fresh access token from the GS1 Authorization API (§4.2).

        ``POST {base}/authorization/token`` with ``client_id`` / ``client_secret``
        headers returns ``{"access_token", "token_type", "expires_in"}``. The JWT is
        cached until shortly before ``expires_in`` elapses.

        Raises:
            MissingCredentialError: A credential environment variable is unset.
            ConfigError: The authorization API rejected the credentials (4xx).
            GS1APIError: The authorization API failed for another reason.
        """
        headers = {
            "client_id": _require_env(self.config.client_id_env),
            "client_secret": _require_env(self.config.client_secret_env),
        }
        try:
            resp = self._http.request("POST", self._base_url + _TOKEN_PATH, headers=headers)
        except (httpx.ConnectError, httpx.ReadTimeout) as exc:
            raise GS1APIError(_NETWORK_ERROR_STATUS, f"token network error: {exc!r}") from exc

        if resp.status_code != HTTPStatus.OK:
            body = scrub_response_body(resp.text)[:_ERROR_BODY_LOG_LIMIT]
            if HTTPStatus.BAD_REQUEST <= resp.status_code < _HTTP_SERVER_ERROR_MIN:
                raise ConfigError(
                    f"GS1 authorization rejected the credentials ({resp.status_code}): {body}"
                )
            raise GS1APIError(resp.status_code, resp.text)

        data = resp.json()
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise GS1APIError(resp.status_code, resp.text)
        expires_in = float(data.get("expires_in", _DEFAULT_TOKEN_TTL_SECONDS))
        self._token = token
        self._token_expiry = time.monotonic() + expires_in
        _log.info("GS1 access token minted (expires in %.0fs)", expires_in)
        return token

    # -- Public API -----------------------------------------------------------

    def upsert(
        self,
        gtin: str,
        item_description: str,
        links: list[LinkInput],
        is_enabled: bool = True,
        application_identifiers: list[AppIdentifier] | None = None,
    ) -> None:
        """Create or update the resolver target for one GTIN (§4.3).

        POST ``/digitallinkv2/v2/digitallink`` (lowercase). Idempotent: the same
        input twice yields the same server state.

        Raises:
            GS1APIError: On any non-2xx response after retries.
        """
        body = self._build_request_body(
            gtin, item_description, links, is_enabled, application_identifiers
        )
        self._request("POST", f"{PATH_PREFIX}digitallink", json_body=body, gtin=gtin)

    def upsert_bulk(self, entries: list[BulkEntry]) -> BulkResult:
        """Create or update many GTINs, batching into ``config.batch_size`` (§4.3).

        POST ``/digitallinkv2/v2/digitallinks`` with a JSON array body per batch.

        Raises:
            GS1APIError: On any non-2xx batch response after retries.
        """
        batch_size = self.config.batch_size
        status_codes: list[int] = []
        responses: list[object] = []
        for start in range(0, len(entries), batch_size):
            chunk = entries[start : start + batch_size]
            body = [
                self._build_request_body(
                    entry["gtin"],
                    entry["item_description"],
                    entry["links"],
                    entry.get("is_enabled", True),
                    entry.get("application_identifiers"),
                )
                for entry in chunk
            ]
            resp = self._request(
                "POST",
                f"{PATH_PREFIX}digitallinks",
                json_body=body,
                gtin=f"bulk[{len(chunk)}]",
            )
            status_codes.append(resp.status_code)
            responses.append(_safe_json(resp))
        return {
            "total": len(entries),
            "batches": len(status_codes),
            "status_codes": status_codes,
            "responses": responses,
        }

    def get(self, gtin: str) -> DigitalLinkRecord | None:
        """Fetch the current Digital Link entry for a GTIN, or ``None`` (§4.3).

        GET ``/digitallinkv2/v2/digitalLink/Gtin/{gtin14}`` — note the capital-L
        ``digitalLink``, preserved exactly.

        Not-found behaviour: 404 returns ``None``. The v2 docs list only 200/400/500
        (no 404), so the empirical not-found status is confirmed against fixtures
        (§13.2); until then 404 → ``None`` and 400/500 → :class:`GS1APIError`.

        Raises:
            GS1APIError: On a non-2xx, non-404 response after retries.
        """
        path = f"{PATH_PREFIX}digitalLink/Gtin/{gtin.zfill(14)}"
        resp = self._request("GET", path, gtin=gtin, not_found_ok=True)
        if resp.status_code == HTTPStatus.NOT_FOUND:
            _log.info("GS1 GET %s -> 404 not found", gtin)
            return None
        return cast(DigitalLinkRecord, resp.json())

    def set_enabled(self, gtin: str, is_enabled: bool) -> None:
        """Toggle ``isEnabled`` without rewriting the full record (§4.3).

        PATCH ``/digitallinkv2/v2/digitalLink/Gtin/{gtin14}/activationStatus``
        (capital-L ``digitalLink``). Success is 204 No Content.

        Raises:
            GS1APIError: On any non-2xx response after retries.
        """
        path = f"{PATH_PREFIX}digitalLink/Gtin/{gtin.zfill(14)}/activationStatus"
        self._request("PATCH", path, json_body={"isEnabled": is_enabled}, gtin=gtin)

    def validate_draft(
        self,
        gtin: str,
        application_identifiers: list[AppIdentifier] | None = None,
    ) -> ValidateDraftResult:
        """Dry-run validation of a draft record (§4.3).

        POST ``/digitallinkv2/digitalLink/validateDraft`` — the only endpoint
        without a ``/v2/`` segment, preserved exactly.

        Raises:
            GS1APIError: On any non-2xx response after retries.
        """
        body = {
            "identificationKey": gtin.zfill(14),
            "identificationKeyType": "Gtin",
            "applicationIdentifiers": [_ai_to_wire(ai) for ai in (application_identifiers or [])],
        }
        resp = self._request(
            "POST", "/digitallinkv2/digitalLink/validateDraft", json_body=body, gtin=gtin
        )
        return cast(ValidateDraftResult, resp.json())

    # -- Internals ------------------------------------------------------------

    def _build_request_body(
        self,
        gtin: str,
        item_description: str,
        links: list[LinkInput],
        is_enabled: bool,
        application_identifiers: list[AppIdentifier] | None,
    ) -> dict[str, object]:
        """Build a ``CreateOrUpdateRequest`` body (§4.2)."""
        return {
            "accountNumber": self.config.account_number,
            "identificationKeyType": "Gtin",
            "identificationKey": gtin.zfill(14),
            "isEnabled": is_enabled,
            "itemDescription": item_description,
            "resolverSettings": {
                "useGS1Resolver": self.config.resolver_settings.use_gs1_resolver,
                "resolverDomainName": self.config.resolver_settings.resolver_domain_name,
            },
            "links": [_link_to_wire(link) for link in links],
            "applicationIdentifiers": [_ai_to_wire(ai) for ai in (application_identifiers or [])],
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: object = None,
        gtin: str,
        not_found_ok: bool = False,
    ) -> httpx.Response:
        """Issue one HTTP call with the retry policy in §4.3 / §5.1.

        Args:
            method: HTTP method.
            path: Path (including any leading prefix), appended to the base URL.
            json_body: JSON-serialisable request body, if any.
            gtin: GTIN (or bulk marker) used only for logging.
            not_found_ok: When True, a 404 is returned rather than raised (GET).

        Returns:
            The successful (2xx) or, when ``not_found_ok``, 404 response.

        Raises:
            GS1APIError: On a terminal non-success response or exhausted retries.
        """
        url = self._base_url + path
        endpoint = f"{method} {path}"
        token_refreshed = False
        attempts_429 = 0
        attempts_5xx = 0

        while True:
            headers = {"Content-Type": "application/json", **self._auth_header()}
            started = time.monotonic()
            try:
                resp = self._http.request(method, url, json=json_body, headers=headers)
            except (httpx.ConnectError, httpx.ReadTimeout) as exc:
                attempts_5xx += 1
                if attempts_5xx >= _RETRY_5XX_MAX_ATTEMPTS:
                    _log.error("GS1 %s (%s) network error, giving up: %r", endpoint, gtin, exc)
                    raise GS1APIError(_NETWORK_ERROR_STATUS, f"network error: {exc!r}") from exc
                backoff = _backoff_5xx(attempts_5xx)
                _log.warning(
                    "GS1 %s (%s) network error, retry %d/%d in %.1fs: %r",
                    endpoint,
                    gtin,
                    attempts_5xx,
                    _RETRY_5XX_MAX_ATTEMPTS,
                    backoff,
                    exc,
                )
                self._sleep(backoff)
                continue

            status = resp.status_code
            elapsed_ms = (time.monotonic() - started) * 1000

            if _HTTP_SUCCESS_MIN <= status < _HTTP_SUCCESS_MAX:
                _log.info("GS1 %s (%s) -> %d in %.0fms", endpoint, gtin, status, elapsed_ms)
                return resp

            if status == HTTPStatus.NOT_FOUND and not_found_ok:
                return resp

            # 401 usually means the cached token expired: re-mint once and retry.
            if status == HTTPStatus.UNAUTHORIZED and not token_refreshed:
                token_refreshed = True
                self._token = None
                _log.warning("GS1 %s (%s) -> 401; refreshing token and retrying", endpoint, gtin)
                continue

            if status == HTTPStatus.TOO_MANY_REQUESTS:
                attempts_429 += 1
                if attempts_429 >= _RETRY_429_MAX_ATTEMPTS:
                    raise self._api_error(resp, endpoint, gtin, final=True)
                backoff = _backoff_429(attempts_429, _retry_after_seconds(resp))
                _log.warning(
                    "GS1 %s (%s) -> 429, retry %d/%d in %.1fs",
                    endpoint,
                    gtin,
                    attempts_429,
                    _RETRY_429_MAX_ATTEMPTS,
                    backoff,
                )
                self._sleep(backoff)
                continue

            if _HTTP_SERVER_ERROR_MIN <= status < _HTTP_SERVER_ERROR_MAX:
                attempts_5xx += 1
                if attempts_5xx >= _RETRY_5XX_MAX_ATTEMPTS:
                    raise self._api_error(resp, endpoint, gtin, final=True)
                backoff = _backoff_5xx(attempts_5xx)
                _log.warning(
                    "GS1 %s (%s) -> %d, retry %d/%d in %.1fs",
                    endpoint,
                    gtin,
                    status,
                    attempts_5xx,
                    _RETRY_5XX_MAX_ATTEMPTS,
                    backoff,
                )
                self._sleep(backoff)
                continue

            # Any other 4xx (incl. 400/401 after fallback/403/409): raise now.
            raise self._api_error(resp, endpoint, gtin, final=True)

    def _api_error(
        self, resp: httpx.Response, endpoint: str, gtin: str, *, final: bool
    ) -> GS1APIError:
        """Build a :class:`GS1APIError` from a failing response, logging it scrubbed."""
        body = resp.text
        error_results = _parse_error_results(body)
        request_id = _request_id(resp)
        if final:
            _log.error(
                "GS1 %s (%s) -> %d; body=%s",
                endpoint,
                gtin,
                resp.status_code,
                scrub_response_body(body)[:_ERROR_BODY_LOG_LIMIT],
            )
        return GS1APIError(
            status_code=resp.status_code,
            response_body=body,
            error_results=error_results,
            request_id=request_id,
        )


# --- Module helpers ----------------------------------------------------------


def _require_env(name: str) -> str:
    """Read an environment variable, raising a typed error if it is unset."""
    try:
        return os.environ[name]
    except KeyError as exc:
        raise MissingCredentialError(f"Environment variable {name!r} is not set") from exc


def _link_to_wire(link: LinkInput) -> dict[str, object]:
    return {
        "linkType": link["link_type"],
        "language": link["language"],
        "linkTitle": link["link_title"],
        "targetUrl": link["target_url"],
        "defaultLinkType": link["default_link_type"],
        "public": link["public"],
        "mediaType": link["media_type"],
    }


def _ai_to_wire(ai: AppIdentifier) -> dict[str, object]:
    return {"identifier": ai["identifier"], "templateVariable": ai["template_variable"]}


def _backoff_429(attempt: int, retry_after: float | None) -> float:
    if retry_after is not None:
        return min(retry_after, _RETRY_429_MAX_SECONDS)
    return min(_RETRY_429_BASE_SECONDS * 2.0 ** (attempt - 1), _RETRY_429_MAX_SECONDS)


def _backoff_5xx(attempt: int) -> float:
    return min(_RETRY_5XX_BASE_SECONDS * 2.0 ** (attempt - 1), _RETRY_5XX_MAX_SECONDS)


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """Parse a ``Retry-After`` header expressed in seconds; ignore HTTP-dates."""
    value = resp.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_error_results(body: str) -> list[dict[str, object]] | None:
    """Parse a standard v2 ``ErrorResult[]`` body, else ``None`` (§5.1)."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(data, list) and all(
        isinstance(item, dict) and "identifier" in item and "errors" in item for item in data
    ):
        return cast(list[dict[str, object]], data)
    return None


def _request_id(resp: httpx.Response) -> str | None:
    for header in ("x-request-id", "request-id", "x-correlation-id"):
        value: str | None = resp.headers.get(header)
        if value:
            return value
    return None


def _safe_json(resp: httpx.Response) -> object:
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError):
        return resp.text
