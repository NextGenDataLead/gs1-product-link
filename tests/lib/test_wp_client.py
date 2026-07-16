"""Tests for the WordPress REST API v2 client (IMPLEMENTATION_SPEC §4.4, §5.1, §6.1-6.2, §7).

Auth is HTTP Basic with an application password read from the environment. Multilingual
detection runs at construction, so ``make_client`` queues the detection probe responses
(matched by URL) and constructs the client *before* the test registers its own business
responses — otherwise a generic ``GET`` matcher would greedily satisfy the probe. All
HTTP is mocked with ``pytest-httpx``; retry backoff is made instant via injected ``sleep``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lib.config import WordPressConfig
from lib.errors import GtinMismatchError, MissingCredentialError, WordPressAPIError
from lib.wp_client import WordPressClient

APP_PASS_ENV = "TEST_WP_APP_PASS"
APP_PASS_VALUE = "abcd EFGH ijkl MNOP"  # WordPress app passwords are space-grouped
USERNAME = "automation-bot"

SITE = "https://staging.example.com"
PLL_PATH = "/wp-json/pll/v1/languages"
WPML_PATH = "/wp-json/sitepress-multilingual-cms/v1/languages"
PLL_URL = f"{SITE}{PLL_PATH}"
WPML_URL = f"{SITE}{WPML_PATH}"
DETECTION_PATHS = {PLL_PATH, WPML_PATH}
POST_TYPE = "noviplast"
TYPE_URL = f"{SITE}/wp-json/wp/v2/{POST_TYPE}"
MEDIA_URL = f"{SITE}/wp-json/wp/v2/media"
SLUG_QS = f"{TYPE_URL}?slug=p-1&context=edit"


def make_config(**overrides: object) -> WordPressConfig:
    params: dict[str, object] = {
        "site_url": SITE,
        "username": USERNAME,
        "app_password_env": APP_PASS_ENV,
        "post_type": POST_TYPE,
        "post_status": "publish",
        "languages": ["nl", "fr"],
    }
    params.update(overrides)
    return WordPressConfig(**params)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _credentials_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(APP_PASS_ENV, APP_PASS_VALUE)


def _queue_detection(httpx_mock: HTTPXMock, plugin: str) -> None:
    """Queue the plugin-detection probe responses consumed during construction."""
    if plugin == "polylang":
        httpx_mock.add_response(method="GET", url=PLL_URL, status_code=200, json=[{"slug": "nl"}])
    elif plugin == "wpml":
        httpx_mock.add_response(method="GET", url=PLL_URL, status_code=404)
        httpx_mock.add_response(method="GET", url=WPML_URL, status_code=200, json={})
    else:
        httpx_mock.add_response(method="GET", url=PLL_URL, status_code=404)
        httpx_mock.add_response(method="GET", url=WPML_URL, status_code=404)


def make_client(
    httpx_mock: HTTPXMock, *, plugin: str = "none", config: WordPressConfig | None = None
) -> tuple[WordPressClient, list[float]]:
    """Construct a client (consuming the detection probes) BEFORE business responses."""
    sleeps: list[float] = []
    _queue_detection(httpx_mock, plugin)
    client = WordPressClient(config or make_config(), sleep=sleeps.append)
    return client, sleeps


def _business_requests(httpx_mock: HTTPXMock) -> list[httpx.Request]:
    """All requests except the multilingual-detection probes."""
    return [r for r in httpx_mock.get_requests() if r.url.path not in DETECTION_PATHS]


# --- Multilingual detection (§4.4) -------------------------------------------


def test_detect_polylang(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock, plugin="polylang")
    assert client.multilingual_plugin == "polylang"


def test_detect_wpml(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock, plugin="wpml")
    assert client.multilingual_plugin == "wpml"


def test_detect_none(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock, plugin="none")
    assert client.multilingual_plugin == "none"


def test_detect_mismatch_warns(httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture) -> None:
    # Configured polylang, but the site probes as none -> WARNING, config stays authoritative.
    config = make_config(multilingual_plugin="polylang")
    with caplog.at_level(logging.WARNING, logger="lib.wp_client"):
        client, _ = make_client(httpx_mock, plugin="none", config=config)
    assert client.multilingual_plugin == "none"
    assert "configured as 'polylang'" in caplog.text


# --- Auth (§4.4) -------------------------------------------------------------


def test_basic_auth_header_sent(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="GET", json=[])

    client.find_by_slug(POST_TYPE, "p-1")

    req = _business_requests(httpx_mock)[0]
    token = base64.b64encode(f"{USERNAME}:{APP_PASS_VALUE}".encode()).decode("ascii")
    assert req.headers["Authorization"] == f"Basic {token}"


def test_missing_app_password_raises(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Construction's detection probe is the first API call; a missing secret fails there
    # before any HTTP request is issued, so no responses need queuing.
    monkeypatch.delenv(APP_PASS_ENV, raising=False)
    with pytest.raises(MissingCredentialError):
        WordPressClient(make_config())


# --- find_by_slug (§4.4) -----------------------------------------------------


def test_find_by_slug_returns_first(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="GET", json=[{"id": 42, "slug": "p-1"}])

    page = client.find_by_slug(POST_TYPE, "p-1")

    assert page is not None
    assert page["id"] == 42


def test_find_by_slug_empty_returns_none(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="GET", json=[])

    assert client.find_by_slug(POST_TYPE, "p-1") is None


def test_find_by_slug_404_returns_none(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="GET", status_code=404, text="no route")

    assert client.find_by_slug(POST_TYPE, "p-1") is None


# --- _find_by_meta_gtin (§6.1) -----------------------------------------------


def test_find_by_meta_gtin_returns_the_matching_page(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(
        method="GET",
        json=[
            {"id": 1, "slug": "other", "meta": {"gtin": "08713195000001"}},
            {"id": 2, "slug": "p-2", "meta": {"gtin": "08713195000002"}},
        ],
    )

    page = client._find_by_meta_gtin(POST_TYPE, "08713195000002")

    assert page is not None
    assert page["id"] == 2  # the match, not merely the first row


def test_find_by_meta_gtin_ignores_an_unfiltered_response(httpx_mock: HTTPXMock) -> None:
    """WP core drops unknown query params, so an un-enabled site returns *every* page.

    Taking ``pages[0]`` there adopts an arbitrary unrelated page: the E8/E11 guards then
    reject it and every would-be create fails as a bogus slug collision. Verified live
    against www.noviplast.nl — a query for a GTIN matching nothing returned 10 rows.
    """
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(
        method="GET",
        json=[
            {"id": 1347, "slug": "drian-sticks", "meta": {"gtin": ""}},
            {"id": 1341, "slug": "power-splash", "meta": {"gtin": ""}},
        ],
    )

    assert client._find_by_meta_gtin(POST_TYPE, "08713195000527") is None


def test_find_by_meta_gtin_tolerates_pages_without_meta(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(
        method="GET",
        json=[
            {"id": 1, "slug": "no-meta-key"},
            {"id": 2, "slug": "meta-not-a-dict", "meta": None},
            {"id": 3, "slug": "p-3", "meta": {"gtin": "08713195000003"}},
        ],
    )

    page = client._find_by_meta_gtin(POST_TYPE, "08713195000003")

    assert page is not None
    assert page["id"] == 3


def test_find_by_meta_gtin_empty_list_returns_none(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="GET", json=[])

    assert client._find_by_meta_gtin(POST_TYPE, "08713195000527") is None


# --- upsert_page idempotency (§6.1) ------------------------------------------


def test_upsert_creates_when_absent(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="GET", json=[])  # slug lookup: empty
    httpx_mock.add_response(method="GET", json=[])  # meta.gtin lookup: empty
    httpx_mock.add_response(
        method="POST", status_code=201, json={"id": 10, "slug": "p-1", "meta": {"gtin": "1"}}
    )

    page = client.upsert_page(
        POST_TYPE, "p-1", "Title", "Body", "nl", meta={"gtin": "1", "brand": "Novi"}
    )

    assert page["id"] == 10
    posts = [r for r in _business_requests(httpx_mock) if r.method == "POST"]
    assert len(posts) == 1
    assert posts[0].url.path == f"/wp-json/wp/v2/{POST_TYPE}"  # create at collection


def test_upsert_creates_when_site_does_not_filter_meta(httpx_mock: HTTPXMock) -> None:
    """A new product must still be created on a site whose REST ignores meta filtering.

    The regression this locks: WP core silently drops the meta_key/meta_value params, so
    the gtin lookup came back holding every existing page. Adopting the first one made
    _guard_gtin_match raise E11 ("slug collision with non-GTIN page"), so *every* new row
    errored against a real site — pointing at an unrelated page — and none were created.
    """
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="GET", json=[])  # slug lookup: no such page yet
    httpx_mock.add_response(  # meta.gtin lookup: unfiltered — real pages, none ours
        method="GET",
        json=[
            {"id": 1347, "slug": "drian-sticks", "meta": {"gtin": ""}},
            {"id": 1341, "slug": "power-splash", "meta": {"gtin": ""}},
        ],
    )
    httpx_mock.add_response(
        method="POST", status_code=201, json={"id": 99, "slug": "p-1", "meta": {"gtin": "1"}}
    )

    page = client.upsert_page(POST_TYPE, "p-1", "Title", "Body", "nl", meta={"gtin": "1"})

    assert page["id"] == 99  # created, not adopted and not raised on
    posts = [r for r in _business_requests(httpx_mock) if r.method == "POST"]
    assert len(posts) == 1
    assert posts[0].url.path == f"/wp-json/wp/v2/{POST_TYPE}"  # create at collection


def test_upsert_updates_when_found_same_id(httpx_mock: HTTPXMock) -> None:
    # §6.1: second identical call finds the page by slug and updates it -> same id.
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="GET", json=[{"id": 10, "slug": "p-1", "meta": {"gtin": "1"}}])
    httpx_mock.add_response(method="POST", status_code=200, json={"id": 10, "slug": "p-1"})

    page = client.upsert_page(POST_TYPE, "p-1", "Title", "Body", "nl", meta={"gtin": "1"})

    assert page["id"] == 10
    posts = [r for r in _business_requests(httpx_mock) if r.method == "POST"]
    assert posts[0].url.path == f"/wp-json/wp/v2/{POST_TYPE}/10"  # update by id


def test_upsert_content_change_keeps_id(httpx_mock: HTTPXMock) -> None:
    # §6.1: modify content -> same id, content updated in the write body.
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="GET", json=[{"id": 10, "slug": "p-1", "meta": {"gtin": "1"}}])
    httpx_mock.add_response(method="POST", status_code=200, json={"id": 10})

    client.upsert_page(POST_TYPE, "p-1", "Title", "NEW BODY", "nl", meta={"gtin": "1"})

    post = next(r for r in _business_requests(httpx_mock) if r.method == "POST")
    assert json.loads(post.content)["content"] == "NEW BODY"


def test_upsert_lookup_order_existing_id_first(httpx_mock: HTTPXMock) -> None:
    # existing_id resolves first: a direct GET-by-id, no slug/meta lookup, then update.
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(
        method="GET",
        url=f"{TYPE_URL}/10?context=edit",
        json={"id": 10, "slug": "p-1", "meta": {"gtin": "1"}},
    )
    httpx_mock.add_response(method="POST", status_code=200, json={"id": 10})

    client.upsert_page(POST_TYPE, "p-1", "T", "B", "nl", meta={"gtin": "1"}, existing_id=10)

    gets = [r for r in _business_requests(httpx_mock) if r.method == "GET"]
    assert len(gets) == 1  # only the by-id lookup; slug/meta lookups skipped


# --- Edge cases E8, E11 (§7) -------------------------------------------------


def test_e8_gtin_mismatch_raises_and_skips_write(httpx_mock: HTTPXMock) -> None:
    # E8: page found by slug carries a different meta.gtin -> raise, no write.
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="GET", json=[{"id": 99, "slug": "p-1", "meta": {"gtin": "999"}}])

    with pytest.raises(GtinMismatchError) as exc:
        client.upsert_page(POST_TYPE, "p-1", "T", "B", "nl", meta={"gtin": "1"})

    assert exc.value.gtin == "1"
    assert exc.value.existing_gtin == "999"
    assert exc.value.wp_page_id == 99
    assert all(r.method != "POST" for r in _business_requests(httpx_mock))  # no write


def test_e11_slug_collision_non_gtin_page_raises(httpx_mock: HTTPXMock) -> None:
    # E11 (proactive): slug found belongs to a page with no meta.gtin -> WordPressAPIError.
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="GET", json=[{"id": 7, "slug": "p-1", "meta": {}}])

    with pytest.raises(WordPressAPIError) as exc:
        client.upsert_page(POST_TYPE, "p-1", "T", "B", "nl", meta={"gtin": "1"})

    assert exc.value.status_code == 409
    assert all(r.method != "POST" for r in _business_requests(httpx_mock))


def test_e11_slug_collision_on_create_409_raises(httpx_mock: HTTPXMock) -> None:
    # E11 (server-reported): create returns 409, raised terminally, not retried.
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="GET", json=[])  # slug lookup empty
    httpx_mock.add_response(method="GET", json=[])  # meta lookup empty
    httpx_mock.add_response(method="POST", status_code=409, text="slug exists")

    with pytest.raises(WordPressAPIError) as exc:
        client.upsert_page(POST_TYPE, "p-1", "T", "B", "nl", meta={"gtin": "1"})

    assert exc.value.status_code == 409
    posts = [r for r in _business_requests(httpx_mock) if r.method == "POST"]
    assert len(posts) == 1  # not retried


# --- upload_media idempotency (§6.2) -----------------------------------------


def test_upload_media_uploads_when_new(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    img = tmp_path / "photo.png"
    img.write_bytes(b"PNGDATA")
    digest = hashlib.sha256(b"PNGDATA").hexdigest()

    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="GET", json=[])  # media slug lookup: empty
    httpx_mock.add_response(method="POST", url=MEDIA_URL, status_code=201, json={"id": 5})
    httpx_mock.add_response(method="POST", url=f"{MEDIA_URL}/5", status_code=200, json={"id": 5})

    media_id = client.upload_media(img, title="Photo")

    assert media_id == 5
    creates = [
        r
        for r in _business_requests(httpx_mock)
        if r.method == "POST" and r.url.path == "/wp-json/wp/v2/media"
    ]
    assert len(creates) == 1  # single multipart upload
    assert creates[0].content == b"PNGDATA"
    finalise = next(r for r in httpx_mock.get_requests() if r.url.path == "/wp-json/wp/v2/media/5")
    assert json.loads(finalise.content)["meta"] == {"content_sha256": digest}


def test_upload_media_reuses_when_hash_matches(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    # §6.2: identical content + title -> slug lookup hits, no re-upload.
    img = tmp_path / "photo.png"
    img.write_bytes(b"PNGDATA")
    digest = hashlib.sha256(b"PNGDATA").hexdigest()

    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(
        method="GET", json=[{"id": 5, "slug": "photo", "meta": {"content_sha256": digest}}]
    )

    media_id = client.upload_media(img, title="Photo")

    assert media_id == 5
    assert all(r.method != "POST" for r in _business_requests(httpx_mock))  # no upload


# --- Edge case E7 (§7): image fetch skip -------------------------------------


def test_download_image_404_returns_none(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="GET", url="https://cdn.example.com/x.jpg", status_code=404)

    assert client.download_image("https://cdn.example.com/x.jpg") is None


def test_download_image_timeout_returns_none(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock)
    httpx_mock.add_exception(httpx.ReadTimeout("slow"), url="https://cdn.example.com/x.jpg")

    assert client.download_image("https://cdn.example.com/x.jpg") is None


def test_e7_missing_image_still_creates_page(httpx_mock: HTTPXMock) -> None:
    # E7 end-to-end shape: download fails -> caller skips featured media -> page created.
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="GET", url="https://cdn.example.com/x.jpg", status_code=404)
    httpx_mock.add_response(method="GET", json=[])  # slug lookup
    httpx_mock.add_response(method="GET", json=[])  # meta lookup
    httpx_mock.add_response(method="POST", status_code=201, json={"id": 11})

    image = client.download_image("https://cdn.example.com/x.jpg")
    featured = None if image is None else 1
    page = client.upsert_page(
        POST_TYPE, "p-1", "T", "B", "nl", featured_media=featured, meta={"gtin": "1"}
    )

    assert page["id"] == 11
    post = next(r for r in _business_requests(httpx_mock) if r.method == "POST")
    assert "featured_media" not in json.loads(post.content)


# --- verify_url (§4.4, §5.1) -------------------------------------------------


def test_verify_url_true_on_2xx(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="HEAD", url=f"{SITE}/p/1", status_code=200)

    assert client.verify_url(f"{SITE}/p/1") is True


def test_verify_url_true_on_redirect(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="HEAD", url=f"{SITE}/p/1", status_code=301)

    assert client.verify_url(f"{SITE}/p/1") is True


def test_verify_url_raises_on_404(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="HEAD", url=f"{SITE}/p/1", status_code=404)

    with pytest.raises(WordPressAPIError) as exc:
        client.verify_url(f"{SITE}/p/1")
    assert exc.value.status_code == 404


# --- Retry policy (§5.1) -----------------------------------------------------


def test_retry_on_429_honours_retry_after(httpx_mock: HTTPXMock) -> None:
    client, sleeps = make_client(httpx_mock)
    httpx_mock.add_response(method="GET", status_code=429, headers={"Retry-After": "2"})
    httpx_mock.add_response(method="GET", json=[])

    client.find_by_slug(POST_TYPE, "p-1")

    assert sleeps == [2.0]


def test_retry_on_5xx_then_success(httpx_mock: HTTPXMock) -> None:
    client, sleeps = make_client(httpx_mock)
    httpx_mock.add_response(method="GET", status_code=500)
    httpx_mock.add_response(method="GET", status_code=503)
    httpx_mock.add_response(method="GET", json=[])

    client.find_by_slug(POST_TYPE, "p-1")

    assert sleeps == [0.5, 1.0]  # exponential base 0.5s


def test_5xx_exhaustion_raises(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock)
    for _ in range(3):
        httpx_mock.add_response(method="GET", status_code=500, text="boom")

    with pytest.raises(WordPressAPIError) as exc:
        client.find_by_slug(POST_TYPE, "p-1")
    assert exc.value.status_code == 500


def test_401_is_terminal_not_retried(httpx_mock: HTTPXMock) -> None:
    # Per §5.1, a WordPress 401 is terminal (no token refresh to attempt).
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="GET", status_code=401, text="unauthorized")

    with pytest.raises(WordPressAPIError) as exc:
        client.find_by_slug(POST_TYPE, "p-1")
    assert exc.value.status_code == 401
    assert len([r for r in _business_requests(httpx_mock) if r.method == "GET"]) == 1


def test_network_error_retried_then_status_zero(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock)
    for _ in range(3):
        httpx_mock.add_exception(httpx.ConnectError("no route"), url=SLUG_QS)

    with pytest.raises(WordPressAPIError) as exc:
        client.find_by_slug(POST_TYPE, "p-1")
    assert exc.value.status_code == 0  # network-error sentinel


# --- PII scrubbing DoD (§5.2) ------------------------------------------------


def test_secrets_never_appear_in_logs(
    httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
) -> None:
    # A 400 body carrying meta/token must be scrubbed; the app password and the derived
    # Basic-auth token must never surface in any log record.
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="GET", json=[])  # slug lookup empty
    httpx_mock.add_response(method="GET", json=[])  # meta lookup empty
    httpx_mock.add_response(
        method="POST",
        status_code=400,
        json={"code": "rest_invalid", "meta": {"gtin": "1"}, "token": "leaked-in-body"},
    )

    with (
        caplog.at_level(logging.DEBUG, logger="lib.wp_client"),
        pytest.raises(WordPressAPIError),
    ):
        client.upsert_page(POST_TYPE, "p-1", "T", "B", "nl", meta={"gtin": "1"})

    log_text = caplog.text
    assert APP_PASS_VALUE not in log_text
    assert "leaked-in-body" not in log_text
    assert "[REDACTED]" in log_text
