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
from lib.wp_client import (
    _PLL_LANGUAGES_PATH,
    _WPML_PROBE_PATH,
    WordPressClient,
)

APP_PASS_ENV = "TEST_WP_APP_PASS"
APP_PASS_VALUE = "abcd EFGH ijkl MNOP"  # WordPress app passwords are space-grouped
USERNAME = "automation-bot"

SITE = "https://staging.example.com"
# Imported, not duplicated: hardcoding these let the WPML probe path drift out of sync with
# lib/ and go unnoticed — detection silently returned "none" on a real WPML site.
PLL_PATH = _PLL_LANGUAGES_PATH
WPML_PATH = _WPML_PROBE_PATH
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


def test_detect_mismatch_warns_and_config_wins(
    httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
) -> None:
    """An explicit config value beats a failed probe.

    A probe can fail for reasons unrelated to the site's real setup — a renamed route, a
    plugin version change, an admin-gated endpoint — and letting it override a configured
    plugin swaps in NoOpAdapter, which links nothing and raises nothing. Pages then publish,
    report ok, and are silently never linked to their translations. (This is not
    hypothetical: the WPML probe path was wrong, so a real WPML site detected as "none".)
    """
    config = make_config(multilingual_plugin="polylang")
    with caplog.at_level(logging.WARNING, logger="lib.wp_client"):
        client, _ = make_client(httpx_mock, plugin="none", config=config)
    assert client.multilingual_plugin == "polylang"  # configured value, not the probe's
    assert "configured as 'polylang'" in caplog.text
    assert "using the configured value" in caplog.text


def test_config_none_defers_to_detection(httpx_mock: HTTPXMock) -> None:
    """`none` means "work it out" — the probe supplies the value."""
    config = make_config(multilingual_plugin="none")
    client, _ = make_client(httpx_mock, plugin="polylang", config=config)
    assert client.multilingual_plugin == "polylang"


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


# --- Multilingual write path (§3.1 of the page-adapter doc) ------------------


def _wpml_config(**overrides: object) -> WordPressConfig:
    return make_config(multilingual_plugin="wpml", default_language="nl", **overrides)


def test_lookups_are_scoped_to_the_language_on_a_multilingual_site(
    httpx_mock: HTTPXMock,
) -> None:
    """Unscoped, a slug lookup answers for the default language only — and clobbers it.

    Both languages share the GTIN-derived slug and the same meta.gtin, so an unscoped
    lookup for the fr row returns the *nl* page, the E8 guard passes (same GTIN), and the
    nl page is overwritten with French — no fr page created, row reports ok. Verified live:
    ?slug=p-X returned the nl page while the fr page was invisible without &lang=fr.
    """
    client, _ = make_client(httpx_mock, plugin="wpml", config=_wpml_config())
    httpx_mock.add_response(method="GET", json=[])  # slug lookup
    httpx_mock.add_response(method="GET", json=[])  # meta.gtin lookup
    httpx_mock.add_response(method="POST", status_code=201, json={"id": 7, "slug": "p-1"})

    client.upsert_page(POST_TYPE, "p-1", "T", "", "fr", meta={"gtin": "1"})

    gets = [r for r in _business_requests(httpx_mock) if r.method == "GET"]
    assert len(gets) == 2
    for request in gets:
        assert "lang=fr" in str(request.url)


def test_create_carries_the_lang_param_but_update_does_not(httpx_mock: HTTPXMock) -> None:
    """?lang= on create keeps the slug; on update it is neither needed nor this call's job."""
    client, _ = make_client(httpx_mock, plugin="wpml", config=_wpml_config())
    # Create: nothing found.
    httpx_mock.add_response(method="GET", json=[])
    httpx_mock.add_response(method="GET", json=[])
    httpx_mock.add_response(method="POST", status_code=201, json={"id": 7, "slug": "p-1"})
    # Update: found by slug.
    httpx_mock.add_response(method="GET", json=[{"id": 7, "slug": "p-1", "meta": {"gtin": "1"}}])
    httpx_mock.add_response(method="POST", json={"id": 7, "slug": "p-1"})

    client.upsert_page(POST_TYPE, "p-1", "T", "", "fr", meta={"gtin": "1"})
    client.upsert_page(POST_TYPE, "p-1", "T2", "", "fr", meta={"gtin": "1"})

    posts = [r for r in _business_requests(httpx_mock) if r.method == "POST"]
    create, update = posts[0], posts[1]
    assert create.url.path == f"/wp-json/wp/v2/{POST_TYPE}"
    assert "lang=fr" in str(create.url)
    assert update.url.path == f"/wp-json/wp/v2/{POST_TYPE}/7"
    assert "lang=" not in str(update.url)


def test_acf_is_written_in_a_second_call_never_on_create(httpx_mock: HTTPXMock) -> None:
    """?lang= and acf in one create silently drop the acf — 201, fields empty, no error.

    So the create body must not carry acf, and the values go in a follow-up call.
    """
    client, _ = make_client(httpx_mock, plugin="wpml", config=_wpml_config())
    httpx_mock.add_response(method="GET", json=[])
    httpx_mock.add_response(method="GET", json=[])
    httpx_mock.add_response(method="POST", status_code=201, json={"id": 7, "slug": "p-1"})
    httpx_mock.add_response(
        method="POST", json={"id": 7, "slug": "p-1", "acf": {"product_title": "Tagline"}}
    )

    page = client.upsert_page(
        POST_TYPE, "p-1", "T", "", "fr", meta={"gtin": "1"}, acf={"product_title": "Tagline"}
    )

    posts = [r for r in _business_requests(httpx_mock) if r.method == "POST"]
    assert len(posts) == 2
    assert b"acf" not in posts[0].content  # create body carries no acf
    assert json.loads(posts[1].content) == {"acf": {"product_title": "Tagline"}}
    assert posts[1].url.path == f"/wp-json/wp/v2/{POST_TYPE}/7"
    # The returned page is the ACF response, so it reflects what was written.
    assert page["acf"] == {"product_title": "Tagline"}  # type: ignore[typeddict-item]


def test_no_acf_call_when_no_acf_given(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock, plugin="wpml", config=_wpml_config())
    httpx_mock.add_response(method="GET", json=[])
    httpx_mock.add_response(method="GET", json=[])
    httpx_mock.add_response(method="POST", status_code=201, json={"id": 7, "slug": "p-1"})

    client.upsert_page(POST_TYPE, "p-1", "T", "body", "nl", meta={"gtin": "1"})

    assert len([r for r in _business_requests(httpx_mock) if r.method == "POST"]) == 1


def test_single_language_site_sends_no_lang_param(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock, plugin="none")
    httpx_mock.add_response(method="GET", json=[])
    httpx_mock.add_response(method="GET", json=[])
    httpx_mock.add_response(method="POST", status_code=201, json={"id": 7, "slug": "p-1"})

    client.upsert_page(POST_TYPE, "p-1", "T", "body", "nl", meta={"gtin": "1"})

    for request in _business_requests(httpx_mock):
        assert "lang=" not in str(request.url)


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


# --- set_page_status (§4.4) --------------------------------------------------


def test_set_page_status_sends_only_status(httpx_mock: HTTPXMock) -> None:
    # The point of not routing through _write_page: a status change must not resend
    # title/content/slug, and must not pick up config.post_status ("publish") — which
    # would make drafting a page silently re-publish it.
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(
        method="GET",
        url=f"{TYPE_URL}/7?context=edit",
        json={"id": 7, "status": "publish", "meta": {"gtin": "1"}},
    )
    httpx_mock.add_response(method="POST", json={"id": 7, "status": "draft"})

    page = client.set_page_status(POST_TYPE, 7, gtin="1", status="draft")

    assert page == {"id": 7, "status": "draft"}
    posted = next(r for r in _business_requests(httpx_mock) if r.method == "POST")
    assert json.loads(posted.content) == {"status": "draft"}


def test_set_page_status_is_noop_when_already_at_status(httpx_mock: HTTPXMock) -> None:
    # Idempotent: re-running run_unpublish must not rewrite an already-drafted page.
    # No POST is registered, so pytest-httpx errors if one is issued.
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(
        method="GET",
        url=f"{TYPE_URL}/7?context=edit",
        json={"id": 7, "status": "draft", "meta": {"gtin": "1"}},
    )

    page = client.set_page_status(POST_TYPE, 7, gtin="1", status="draft")

    assert page == {"id": 7, "status": "draft", "meta": {"gtin": "1"}}
    assert all(r.method != "POST" for r in _business_requests(httpx_mock))


def test_set_page_status_returns_none_when_page_is_gone(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="GET", url=f"{TYPE_URL}/7?context=edit", status_code=404)

    assert client.set_page_status(POST_TYPE, 7, gtin="1", status="draft") is None


def test_set_page_status_refuses_on_gtin_mismatch(httpx_mock: HTTPXMock) -> None:
    # E8: a stale page id in state addresses another product's page. Drafting a
    # stranger's page is less destructive than deleting it and just as invisible.
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(
        method="GET",
        url=f"{TYPE_URL}/7?context=edit",
        json={"id": 7, "status": "publish", "meta": {"gtin": "999"}},
    )

    with pytest.raises(GtinMismatchError) as exc:
        client.set_page_status(POST_TYPE, 7, gtin="1", status="draft")

    assert exc.value.existing_gtin == "999"
    assert all(r.method != "POST" for r in _business_requests(httpx_mock))


def test_set_page_status_refuses_on_non_gtin_page(httpx_mock: HTTPXMock) -> None:
    # E11: the id addresses a page that is not ours at all.
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(
        method="GET", url=f"{TYPE_URL}/7?context=edit", json={"id": 7, "status": "publish"}
    )

    with pytest.raises(WordPressAPIError) as exc:
        client.set_page_status(POST_TYPE, 7, gtin="1", status="draft")

    assert exc.value.status_code == 409
    assert all(r.method != "POST" for r in _business_requests(httpx_mock))


# --- delete_page / delete_media (§4.4) ---------------------------------------


def test_delete_page_force_deletes_and_returns_previous(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(
        method="GET", url=f"{TYPE_URL}/7?context=edit", json={"id": 7, "meta": {"gtin": "1"}}
    )
    httpx_mock.add_response(method="DELETE", json={"deleted": True, "previous": {"id": 7}})

    page = client.delete_page(POST_TYPE, 7, gtin="1")

    # Unwrapped from {"deleted": ..., "previous": {...}} — the raw body has no "id".
    assert page == {"id": 7}
    deleted = next(r for r in _business_requests(httpx_mock) if r.method == "DELETE")
    assert deleted.url.params["force"] == "true"  # a string: params are dict[str, str]


def test_delete_page_trashes_when_not_forced(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(
        method="GET", url=f"{TYPE_URL}/7?context=edit", json={"id": 7, "meta": {"gtin": "1"}}
    )
    httpx_mock.add_response(method="DELETE", json={"id": 7, "status": "trash"})

    page = client.delete_page(POST_TYPE, 7, gtin="1", force=False)

    assert page == {"id": 7, "status": "trash"}  # trash answers with the post itself
    deleted = next(r for r in _business_requests(httpx_mock) if r.method == "DELETE")
    assert "force" not in deleted.url.params


def test_delete_page_refuses_on_gtin_mismatch(httpx_mock: HTTPXMock) -> None:
    # E8 for a delete: the page belongs to another product. No DELETE is registered, so
    # pytest-httpx would error if one were issued.
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(
        method="GET", url=f"{TYPE_URL}/7?context=edit", json={"id": 7, "meta": {"gtin": "999"}}
    )

    with pytest.raises(GtinMismatchError) as exc:
        client.delete_page(POST_TYPE, 7, gtin="1")

    assert exc.value.existing_gtin == "999"
    assert all(r.method != "DELETE" for r in _business_requests(httpx_mock))


def test_delete_page_refuses_on_non_gtin_page(httpx_mock: HTTPXMock) -> None:
    # E11 for a delete: the id addresses a page that is not ours at all.
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(
        method="GET", url=f"{TYPE_URL}/7?context=edit", json={"id": 7, "meta": {}}
    )

    with pytest.raises(WordPressAPIError) as exc:
        client.delete_page(POST_TYPE, 7, gtin="1")

    assert exc.value.status_code == 409
    assert all(r.method != "DELETE" for r in _business_requests(httpx_mock))


def test_delete_page_missing_is_noop(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="GET", url=f"{TYPE_URL}/7?context=edit", status_code=404)

    assert client.delete_page(POST_TYPE, 7, gtin="1") is None
    assert all(r.method != "DELETE" for r in _business_requests(httpx_mock))


def test_delete_page_gone_during_delete_is_noop(httpx_mock: HTTPXMock) -> None:
    # The race: purged between the read and the delete.
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(
        method="GET", url=f"{TYPE_URL}/7?context=edit", json={"id": 7, "meta": {"gtin": "1"}}
    )
    httpx_mock.add_response(method="DELETE", status_code=410)

    assert client.delete_page(POST_TYPE, 7, gtin="1") is None


def test_delete_media_forces(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="DELETE", json={"deleted": True})

    assert client.delete_media(5) is True

    deleted = next(r for r in _business_requests(httpx_mock) if r.method == "DELETE")
    assert deleted.url.path == "/wp-json/wp/v2/media/5"
    assert deleted.url.params["force"] == "true"  # WP refuses to trash attachments (501)


def test_delete_media_missing_is_noop(httpx_mock: HTTPXMock) -> None:
    client, _ = make_client(httpx_mock)
    httpx_mock.add_response(method="DELETE", status_code=404)

    assert client.delete_media(5) is False


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
