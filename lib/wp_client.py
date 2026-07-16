"""Client for the WordPress REST API v2.

Implements ``docs/IMPLEMENTATION_SPEC.md`` §4.4 (client shape), §5.1 (the WordPress
error-handling matrix), §6.1/§6.2 (upsert/media idempotency), and the edge cases E7
(image 404 → featured media skipped), E8 (mismatched ``meta.gtin`` → skip row), and
E11 (slug collision with a non-GTIN page → human intervention).

Auth is HTTP Basic with an application password (``PROJECT_HANDOVER.md`` §4.4): the
username comes from config and the password is resolved lazily from the environment
variable named in ``app_password_env`` and sent as an ``Authorization: Basic`` header.
The header is never logged — :func:`lib.logging_setup.scrub_headers` redacts it and
:func:`lib.logging_setup.scrub_response_body` redacts the WordPress ``meta.*`` subtree.

The client mirrors ``lib/gs1_dl_client.py``: a single ``_request`` retry loop with
independent 429/5xx budgets (minus the OAuth token dance — per §5.1 a WordPress
``401`` is terminal), and an ``_api_error`` builder that logs failures scrubbed.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import mimetypes
import os
import re
import time
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path
from typing import Final, Literal, TypedDict, cast

import httpx

from lib.config import WordPressConfig
from lib.errors import GtinMismatchError, MissingCredentialError, WordPressAPIError
from lib.logging_setup import scrub_response_body
from lib.multilingual import MultilingualAdapter, make_adapter

_log = logging.getLogger(__name__)

# --- Constants ---------------------------------------------------------------

#: REST base for posts of any (custom) post type (§4.4). Post types must be
#: registered with ``show_in_rest => true`` to appear here.
_WP_API_PREFIX: Final = "/wp-json/wp/v2"
#: Media collection endpoint.
_MEDIA_PATH: Final = f"{_WP_API_PREFIX}/media"
#: Polylang detection route — a 200 means the plugin is active (§4.4).
_PLL_LANGUAGES_PATH: Final = "/wp-json/pll/v1/languages"
#: WPML detection route — its presence means WPML is active (§4.4).
_WPML_PROBE_PATH: Final = "/wp-json/sitepress-multilingual-cms/v1/languages"

#: Post ``meta`` key holding the GTIN — the idempotency key for ``upsert_page`` (§6.1).
_GTIN_META_KEY: Final = "gtin"
#: Media ``meta`` key holding the SHA-256 of the uploaded bytes (§6.2).
_CONTENT_HASH_META_KEY: Final = "content_sha256"

# Retry policy (§5.1). 429 and 5xx use independent attempt budgets, matching the
# GS1 client. A WordPress 401/403 is terminal (no token refresh to attempt).
_RETRY_429_MAX_ATTEMPTS: Final = 5
_RETRY_429_BASE_SECONDS: Final = 1.0
_RETRY_429_MAX_SECONDS: Final = 60.0
_RETRY_5XX_MAX_ATTEMPTS: Final = 3
_RETRY_5XX_BASE_SECONDS: Final = 0.5
_RETRY_5XX_MAX_SECONDS: Final = 30.0

#: Default per-operation timeouts (§4.4): read 60s (WordPress renders can be slow).
_DEFAULT_TIMEOUT: Final = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)

#: Abbreviate error bodies to this many characters when logging.
_ERROR_BODY_LOG_LIMIT: Final = 500

#: Sentinel status code used when an error originates below HTTP (network error).
_NETWORK_ERROR_STATUS: Final = 0

# HTTP status ranges used in retry/verify classification (bounds exclusive on max).
_HTTP_SUCCESS_MIN: Final = 200
_HTTP_SUCCESS_MAX: Final = 300
_HTTP_REDIRECT_MAX: Final = 400
_HTTP_SERVER_ERROR_MIN: Final = 500
_HTTP_SERVER_ERROR_MAX: Final = 600


# --- Wire shapes (TypedDict per §1: TypedDict for HTTP shapes) ----------------


class WordPressPage(TypedDict, total=False):
    """A WordPress post/page as returned by the REST API (``context=edit``).

    ``title`` and ``content`` are ``{"rendered": ..., "raw": ...}`` objects; ``raw`` is
    only present under ``context=edit``, which the client always requests on lookups so
    ``meta`` and raw content are readable. All fields are optional (``total=False``).
    """

    id: int
    slug: str
    status: str
    type: str
    link: str
    title: dict[str, str]
    content: dict[str, str]
    parent: int
    featured_media: int
    meta: dict[str, object]


class WordPressMedia(TypedDict, total=False):
    """A WordPress media attachment as returned by the REST API."""

    id: int
    slug: str
    source_url: str
    title: dict[str, str]
    meta: dict[str, object]


MultilingualPlugin = Literal["polylang", "wpml", "none"]


class WordPressClient:
    """Synchronous client for the WordPress REST API v2 (§4.4).

    Detects the site's multilingual plugin at construction and selects the matching
    :class:`lib.multilingual.MultilingualAdapter`.

    Args:
        config: The WordPress target configuration for one client.
        timeout: Override the default per-operation timeouts (for tests).
        sleep: Injectable sleep function so retry backoff is instant in tests.
    """

    def __init__(
        self,
        config: WordPressConfig,
        *,
        timeout: httpx.Timeout | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._base_url = config.site_url.rstrip("/")
        self._username = config.username
        self._http = httpx.Client(timeout=timeout or _DEFAULT_TIMEOUT)
        self._sleep = sleep
        self.multilingual_plugin: MultilingualPlugin = self.detect_multilingual_plugin()
        self._adapter: MultilingualAdapter = make_adapter(self.multilingual_plugin)

    # -- Lifecycle ------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._http.close()

    def __enter__(self) -> WordPressClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- Auth -----------------------------------------------------------------

    def _auth_header(self) -> dict[str, str]:
        """Return the HTTP Basic ``Authorization`` header (§4.4).

        The application password is read lazily from the environment on every request
        so a missing secret surfaces as :class:`MissingCredentialError` at first use
        (edge E15). The header value is never logged.

        Raises:
            MissingCredentialError: The application-password env var is unset.
        """
        password = _require_env(self.config.app_password_env)
        token = base64.b64encode(f"{self._username}:{password}".encode()).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    # -- Public API -----------------------------------------------------------

    def detect_multilingual_plugin(self) -> MultilingualPlugin:
        """Detect which multilingual plugin the site runs (§4.4).

        Probes the Polylang route first, then WPML; a present route (200) wins.
        Returns ``"none"`` when neither responds. If the configured plugin disagrees
        with what was detected, logs a WARNING — the config value stays authoritative
        for adapter selection, but the mismatch is worth surfacing.

        Returns:
            ``"polylang"``, ``"wpml"``, or ``"none"``.
        """
        if self._probe(_PLL_LANGUAGES_PATH):
            detected: MultilingualPlugin = "polylang"
        elif self._probe(_WPML_PROBE_PATH):
            detected = "wpml"
        else:
            detected = "none"
        configured = self.config.multilingual_plugin
        if configured not in ("none", detected):
            _log.warning(
                "WP multilingual plugin configured as %r but detected %r", configured, detected
            )
        return detected

    def find_by_slug(self, post_type: str, slug: str) -> WordPressPage | None:
        """Return the page with ``slug`` under ``post_type``, or ``None`` (§4.4).

        Raises:
            WordPressAPIError: On a non-2xx response other than 404, after retries.
        """
        pages = self._get_list(
            f"{_WP_API_PREFIX}/{post_type}",
            params={"slug": slug, "context": "edit"},
            label=f"{post_type}?slug={slug}",
        )
        return cast(WordPressPage, pages[0]) if pages else None

    def upsert_page(  # noqa: PLR0913 — mirrors the §4.4 signature verbatim
        self,
        post_type: str,
        slug: str,
        title: str,
        content: str,
        language: str,
        featured_media: int | None = None,
        parent: int | None = None,
        meta: dict[str, object] | None = None,
        existing_id: int | None = None,
    ) -> WordPressPage:
        """Create or update one product page, idempotently (§6.1).

        Lookup order: (1) ``existing_id``, (2) ``slug``, (3) ``meta.gtin``. On a match
        the page is updated in place (same id, content replaced); with no match a new
        page is created carrying ``meta.gtin``. Callers must always set ``meta['gtin']``
        — it is the idempotency key.

        Args:
            post_type: The (custom) post type slug.
            slug: The page slug (typically GTIN-derived).
            title: The page title.
            content: The page HTML content.
            language: The page's language code (set on WordPress when Polylang is active).
            featured_media: Media id for the featured image, if any.
            parent: Parent page id, if any.
            meta: Post meta; must include ``gtin``.
            existing_id: A known page id to update directly, if available.

        Returns:
            The created or updated :class:`WordPressPage`.

        Raises:
            GtinMismatchError: The matched page's ``meta.gtin`` differs from the row's
                GTIN (edge E8) — the row must be logged and skipped.
            WordPressAPIError: The matched-or-target slug belongs to a non-GTIN page
                (edge E11, a 409-class collision needing human intervention), or any
                other non-2xx response after retries.
        """
        gtin = _meta_gtin(meta)
        found = self._lookup_existing(post_type, slug, gtin, existing_id)
        if found is not None:
            self._guard_gtin_match(found, gtin)
            return self._write_page(
                post_type,
                title,
                content,
                language,
                slug,
                featured_media,
                parent,
                meta,
                page_id=found["id"],
            )
        return self._write_page(
            post_type, title, content, language, slug, featured_media, parent, meta, page_id=None
        )

    def upload_media(self, file_path: str | Path, title: str | None = None) -> int:
        """Upload a media file, idempotently by content hash + slug (§6.2).

        Computes the SHA-256 of the file bytes and derives a deterministic slug from
        ``title`` (or the filename). If a media item already exists at that slug with a
        matching stored hash, its id is returned without re-uploading; otherwise the
        bytes are uploaded and the id returned.

        Edge E7 (a source ``image_url`` that 404s or times out) is handled *before*
        this method by the run loop via :meth:`download_image`, which returns ``None``
        so the caller skips featured media and still creates the page.

        Args:
            file_path: Path to the local media file to upload.
            title: Optional media title; also seeds the idempotency slug.

        Returns:
            The WordPress media id.

        Raises:
            WordPressAPIError: On a non-2xx response after retries.
        """
        path = Path(file_path)
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        slug = _media_slug(title, path)

        existing = self._find_media_by_slug(slug)
        if existing is not None and _media_hash(existing) == digest:
            _log.info("WP media %r unchanged (sha256 match), reusing id %s", slug, existing["id"])
            return existing["id"]
        return self._create_media(path, data, title, slug, digest)

    def download_image(self, url: str) -> bytes | None:
        """Fetch an image URL, returning ``None`` if it is unavailable (edge E7).

        A 404, any other non-2xx status, or a network timeout yields ``None`` (logged
        as a WARNING) so the caller skips featured media and still publishes the page.
        This is the primitive the Phase-6 run loop composes with :meth:`upload_media`.

        Args:
            url: The source image URL from the export.

        Returns:
            The image bytes on success, or ``None`` when the image cannot be fetched.
        """
        try:
            resp = self._http.request("GET", url)
        except (httpx.ConnectError, httpx.ReadTimeout) as exc:
            _log.warning("WP image fetch failed for %s: %r (skipping featured media)", url, exc)
            return None
        if _HTTP_SUCCESS_MIN <= resp.status_code < _HTTP_SUCCESS_MAX:
            return resp.content
        _log.warning("WP image fetch for %s -> %d (skipping featured media)", url, resp.status_code)
        return None

    def verify_url(self, url: str) -> bool:
        """Return whether ``url`` resolves to a 2xx/3xx response via HEAD (§4.4, §5.1).

        Args:
            url: The absolute URL to verify (e.g. a published page's public link).

        Returns:
            ``True`` for a 2xx/3xx status.

        Raises:
            WordPressAPIError: For any other status or a network error (no retry).
        """
        try:
            resp = self._http.request("HEAD", url)
        except (httpx.ConnectError, httpx.ReadTimeout) as exc:
            _log.error("WP verify_url network error for %s: %r", url, exc)
            raise WordPressAPIError(
                _NETWORK_ERROR_STATUS, f"verify_url network error: {exc!r}"
            ) from exc
        if _HTTP_SUCCESS_MIN <= resp.status_code < _HTTP_REDIRECT_MAX:
            return True
        _log.error("WP verify_url %s -> %d", url, resp.status_code)
        raise WordPressAPIError(resp.status_code, resp.text)

    def link_translations(self, translations: dict[str, int]) -> None:
        """Link per-language page ids as translations of one another (§4.5).

        Delegates to the multilingual adapter selected at construction. A no-op when
        the site has no multilingual plugin.

        Args:
            translations: Mapping of language code to the page id in that language.
        """
        self._adapter.link_translations(self, translations)

    # -- Lookup internals -----------------------------------------------------

    def _lookup_existing(
        self, post_type: str, slug: str, gtin: str | None, existing_id: int | None
    ) -> WordPressPage | None:
        """Resolve the existing page by id, then slug, then ``meta.gtin`` (§6.1)."""
        if existing_id is not None:
            page = self._get_page(post_type, existing_id)
            if page is not None:
                return page
        page = self.find_by_slug(post_type, slug)
        if page is not None:
            return page
        if gtin is not None:
            return self._find_by_meta_gtin(post_type, gtin)
        return None

    def _guard_gtin_match(self, found: WordPressPage, gtin: str | None) -> None:
        """Enforce the GTIN-ownership guard on a matched page (edges E8, E11).

        Raises:
            GtinMismatchError: The page carries a different ``meta.gtin`` (E8).
            WordPressAPIError: The page carries no ``meta.gtin`` — a collision with a
                non-GTIN page (E11), reported as a 409 needing human intervention.
        """
        if gtin is None:
            return
        existing_gtin = _meta_gtin(found.get("meta"))
        page_id = found.get("id", 0)
        if existing_gtin is None:
            _log.error(
                "WP page %s at this slug has no meta.gtin (non-GTIN page); refusing to "
                "overwrite (E11)",
                page_id,
            )
            raise WordPressAPIError(
                int(HTTPStatus.CONFLICT),
                f"slug collision with non-GTIN WordPress page {page_id}",
            )
        if existing_gtin != gtin:
            _log.error(
                "WP page %s has meta.gtin %r != row GTIN %r; skipping (E8)",
                page_id,
                existing_gtin,
                gtin,
            )
            raise GtinMismatchError(gtin, existing_gtin, page_id)

    def _get_page(self, post_type: str, page_id: int) -> WordPressPage | None:
        """GET one page by id (``context=edit``); ``None`` if it 404s (stale id)."""
        try:
            resp = self._request(
                "GET",
                f"{_WP_API_PREFIX}/{post_type}/{page_id}",
                params={"context": "edit"},
                label=f"{post_type}/{page_id}",
            )
        except WordPressAPIError as exc:
            if exc.status_code == HTTPStatus.NOT_FOUND:
                return None
            raise
        return cast(WordPressPage, resp.json())

    def _find_by_meta_gtin(self, post_type: str, gtin: str) -> WordPressPage | None:
        """GET the page whose ``meta.gtin`` equals ``gtin``, or ``None`` (§6.1).

        The ``meta_key``/``meta_value`` params are **not** core WordPress REST features:
        core silently drops unknown query params rather than erroring, so a site without a
        ``rest_{post_type}_query`` enabler answers this request with an unfiltered page of
        *every* post. Never trust the server to have filtered — verify ``meta.gtin`` on the
        way out. Taking ``pages[0]`` on an unfiltered list returns an arbitrary unrelated
        page, which the E8/E11 guards in :meth:`_guard_gtin_match` then reject, turning
        every would-be create into a bogus "slug collision" error.

        Returning ``None`` when nothing matches is also the right fallback on a site with no
        enabler: the caller creates the page instead of adopting the wrong one.
        """
        pages = self._get_list(
            f"{_WP_API_PREFIX}/{post_type}",
            params={"meta_key": _GTIN_META_KEY, "meta_value": gtin, "context": "edit"},
            label=f"{post_type}?meta.gtin={gtin}",
        )
        for page in pages:
            meta = page.get("meta")
            if isinstance(meta, dict) and _meta_gtin(meta) == gtin:
                return cast(WordPressPage, page)
        return None

    def _find_media_by_slug(self, slug: str) -> WordPressMedia | None:
        """GET the media item at ``slug``, or ``None`` (§6.2)."""
        items = self._get_list(
            _MEDIA_PATH, params={"slug": slug, "context": "edit"}, label=f"media?slug={slug}"
        )
        return cast(WordPressMedia, items[0]) if items else None

    def _get_list(
        self, path: str, *, params: dict[str, str], label: str
    ) -> list[dict[str, object]]:
        """GET a collection endpoint, returning the JSON list (``[]`` on 404)."""
        try:
            resp = self._request("GET", path, params=params, label=label)
        except WordPressAPIError as exc:
            if exc.status_code == HTTPStatus.NOT_FOUND:
                return []
            raise
        data = resp.json()
        return cast(list[dict[str, object]], data) if isinstance(data, list) else []

    # -- Write internals ------------------------------------------------------

    def _write_page(  # noqa: PLR0913 — assembles the full §4.4 create/update body
        self,
        post_type: str,
        title: str,
        content: str,
        language: str,
        slug: str,
        featured_media: int | None,
        parent: int | None,
        meta: dict[str, object] | None,
        *,
        page_id: int | None,
    ) -> WordPressPage:
        """POST a create (``page_id is None``) or update to the post-type endpoint."""
        body: dict[str, object] = {
            "title": title,
            "content": content,
            "status": self.config.post_status,
            "slug": slug,
        }
        if meta is not None:
            body["meta"] = meta
        if featured_media is not None:
            body["featured_media"] = featured_media
        if parent is not None:
            body["parent"] = parent
        if self.multilingual_plugin == "polylang":
            body["lang"] = language

        if page_id is None:
            path = f"{_WP_API_PREFIX}/{post_type}"
            label = f"create {post_type} {slug}"
        else:
            path = f"{_WP_API_PREFIX}/{post_type}/{page_id}"
            label = f"update {post_type}/{page_id}"
        resp = self._request("POST", path, json_body=body, label=label)
        return cast(WordPressPage, resp.json())

    def _create_media(
        self, path: Path, data: bytes, title: str | None, slug: str, digest: str
    ) -> int:
        """Upload media bytes, then set its slug/title and content-hash meta (§6.2)."""
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        headers = {
            "Content-Type": mime,
            "Content-Disposition": f'attachment; filename="{path.name}"',
        }
        resp = self._request(
            "POST", _MEDIA_PATH, content=data, extra_headers=headers, label=f"upload media {slug}"
        )
        media = cast(WordPressMedia, resp.json())
        media_id = media["id"]
        update_body: dict[str, object] = {
            "slug": slug,
            "meta": {_CONTENT_HASH_META_KEY: digest},
        }
        if title is not None:
            update_body["title"] = title
        self._request(
            "POST",
            f"{_MEDIA_PATH}/{media_id}",
            json_body=update_body,
            label=f"finalise media {media_id}",
        )
        return media_id

    def _probe(self, path: str) -> bool:
        """Return whether a GET to ``path`` succeeds (2xx) — used for plugin detection."""
        try:
            self._request("GET", path, label=f"detect {path}")
        except WordPressAPIError:
            return False
        return True

    # -- HTTP core ------------------------------------------------------------

    def _request(  # noqa: PLR0913 — request knobs kept explicit and keyword-only
        self,
        method: str,
        path: str,
        *,
        label: str,
        params: dict[str, str] | None = None,
        json_body: object = None,
        content: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Issue one HTTP call with the retry policy in §5.1.

        Args:
            method: HTTP method.
            path: Path (with any leading prefix), appended to the base URL.
            label: Short label used only for logging.
            params: Query-string parameters.
            json_body: JSON-serialisable request body, if any.
            content: Raw byte body (media upload), mutually exclusive with ``json_body``.
            extra_headers: Additional headers (e.g. ``Content-Disposition``).

        Returns:
            The successful (2xx) response.

        Raises:
            WordPressAPIError: On a terminal non-2xx response or exhausted retries.
                Callers that treat 404 as not-found (lookups) catch this.
        """
        url = self._base_url + path
        endpoint = f"{method} {path}"
        attempts_429 = 0
        attempts_5xx = 0

        while True:
            headers = dict(self._auth_header())
            if json_body is not None:
                headers["Content-Type"] = "application/json"
            if extra_headers:
                headers.update(extra_headers)
            started = time.monotonic()
            try:
                resp = self._send(method, url, params, json_body, content, headers)
            except (httpx.ConnectError, httpx.ReadTimeout) as exc:
                attempts_5xx += 1
                if attempts_5xx >= _RETRY_5XX_MAX_ATTEMPTS:
                    _log.error("WP %s (%s) network error, giving up: %r", endpoint, label, exc)
                    raise WordPressAPIError(
                        _NETWORK_ERROR_STATUS, f"network error: {exc!r}"
                    ) from exc
                backoff = _backoff_5xx(attempts_5xx)
                _log.warning(
                    "WP %s (%s) network error, retry %d/%d in %.1fs: %r",
                    endpoint,
                    label,
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
                _log.info("WP %s (%s) -> %d in %.0fms", endpoint, label, status, elapsed_ms)
                return resp

            if status == HTTPStatus.TOO_MANY_REQUESTS:
                attempts_429 += 1
                if attempts_429 >= _RETRY_429_MAX_ATTEMPTS:
                    raise self._api_error(resp, endpoint, label, final=True)
                backoff = _backoff_429(attempts_429, _retry_after_seconds(resp))
                _log.warning(
                    "WP %s (%s) -> 429, retry %d/%d in %.1fs",
                    endpoint,
                    label,
                    attempts_429,
                    _RETRY_429_MAX_ATTEMPTS,
                    backoff,
                )
                self._sleep(backoff)
                continue

            if _HTTP_SERVER_ERROR_MIN <= status < _HTTP_SERVER_ERROR_MAX:
                attempts_5xx += 1
                if attempts_5xx >= _RETRY_5XX_MAX_ATTEMPTS:
                    raise self._api_error(resp, endpoint, label, final=True)
                backoff = _backoff_5xx(attempts_5xx)
                _log.warning(
                    "WP %s (%s) -> %d, retry %d/%d in %.1fs",
                    endpoint,
                    label,
                    status,
                    attempts_5xx,
                    _RETRY_5XX_MAX_ATTEMPTS,
                    backoff,
                )
                self._sleep(backoff)
                continue

            # Any other 4xx (400/401/403/404/409): terminal per §5.1.
            raise self._api_error(resp, endpoint, label, final=True)

    def _send(  # noqa: PLR0913 — thin transport shim; args mirror httpx.request
        self,
        method: str,
        url: str,
        params: dict[str, str] | None,
        json_body: object,
        content: bytes | None,
        headers: dict[str, str],
    ) -> httpx.Response:
        """Issue the underlying HTTP request (json xor raw content)."""
        if content is not None:
            return self._http.request(method, url, params=params, content=content, headers=headers)
        if json_body is not None:
            return self._http.request(method, url, params=params, json=json_body, headers=headers)
        return self._http.request(method, url, params=params, headers=headers)

    def _api_error(
        self, resp: httpx.Response, endpoint: str, label: str, *, final: bool
    ) -> WordPressAPIError:
        """Build a :class:`WordPressAPIError`, logging it scrubbed (§5.2)."""
        body = resp.text
        error = WordPressAPIError(resp.status_code, body)
        if final:
            if resp.status_code == HTTPStatus.NOT_FOUND:
                _log.info("WP %s (%s) -> not found (404)", endpoint, label)
            else:
                _log.error(
                    "WP %s (%s) -> %d; body=%s",
                    endpoint,
                    label,
                    resp.status_code,
                    scrub_response_body(body)[:_ERROR_BODY_LOG_LIMIT],
                )
        return error


# --- Module helpers ----------------------------------------------------------


def _require_env(name: str) -> str:
    """Read an environment variable, raising a typed error if it is unset."""
    try:
        return os.environ[name]
    except KeyError as exc:
        raise MissingCredentialError(f"Environment variable {name!r} is not set") from exc


def _meta_gtin(meta: dict[str, object] | None) -> str | None:
    """Extract a string ``gtin`` from a post ``meta`` mapping, if present."""
    if not meta:
        return None
    value = meta.get(_GTIN_META_KEY)
    return str(value) if value is not None and value != "" else None


def _media_hash(media: WordPressMedia) -> str | None:
    """Read the stored content SHA-256 from a media item's ``meta``, if present."""
    meta = media.get("meta")
    if not isinstance(meta, dict):
        return None
    value = meta.get(_CONTENT_HASH_META_KEY)
    return str(value) if isinstance(value, str) and value else None


def _media_slug(title: str | None, path: Path) -> str:
    """Derive a deterministic media slug from the title (or filename) (§6.2)."""
    source = title if title else path.stem
    slug = re.sub(r"[^a-z0-9]+", "-", source.lower()).strip("-")
    return slug or "media"


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
