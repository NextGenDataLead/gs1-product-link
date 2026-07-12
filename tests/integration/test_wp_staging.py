"""Live staging-WordPress smoke tests for the DoD (IMPLEMENTATION_SPEC §12 Phase 4).

These are the three staging-gated Definition-of-Done checks that cannot run against a
mock: §6.1 (upsert idempotency), §6.2 (media idempotency), Polylang detection, and the
exit gate "published page returns 200 at its URL". They are marked ``staging`` and
skipped unless the staging environment is configured, so CI stays green on mocks. Run
them once staging is provisioned with::

    WP_STAGING_URL=https://staging.noviplast.nl \\
    WP_STAGING_USER=automation-bot \\
    NOVIPLAST_WP_APP_PASS='xxxx xxxx xxxx xxxx' \\
    pytest -m staging

Optional overrides: ``WP_STAGING_POST_TYPE`` (default ``noviplast``),
``WP_STAGING_APP_PASS_ENV`` (default ``NOVIPLAST_WP_APP_PASS``),
``WP_STAGING_GTIN`` (default ``08712345678905``).
"""

from __future__ import annotations

import base64
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from lib.config import WordPressConfig
from lib.wp_client import WordPressClient

_URL = os.environ.get("WP_STAGING_URL")
_USER = os.environ.get("WP_STAGING_USER")
_APP_PASS_ENV = os.environ.get("WP_STAGING_APP_PASS_ENV", "NOVIPLAST_WP_APP_PASS")
_POST_TYPE = os.environ.get("WP_STAGING_POST_TYPE", "noviplast")
_GTIN = os.environ.get("WP_STAGING_GTIN", "08712345678905")
_SLUG = f"p-{_GTIN}"

# A 1x1 transparent PNG — small, valid image bytes for the media round-trip.
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

_STAGING_READY = bool(_URL and _USER and os.environ.get(_APP_PASS_ENV))

pytestmark = [
    pytest.mark.staging,
    pytest.mark.skipif(
        not _STAGING_READY,
        reason="staging WP not configured (set WP_STAGING_URL, WP_STAGING_USER, and "
        f"{_APP_PASS_ENV})",
    ),
]


@pytest.fixture(scope="module")
def client() -> Iterator[WordPressClient]:
    config = WordPressConfig(
        site_url=_URL or "",
        username=_USER or "",
        app_password_env=_APP_PASS_ENV,
        post_type=_POST_TYPE,
        multilingual_plugin="polylang",
        default_language="nl",
        languages=["nl", "fr"],
    )
    wp = WordPressClient(config)
    try:
        yield wp
    finally:
        wp.close()


def _upsert_smoke_page(client: WordPressClient) -> dict[str, object]:
    return dict(
        client.upsert_page(
            _POST_TYPE,
            _SLUG,
            "Smoke test product",
            "<p>Smoke test content.</p>",
            "nl",
            meta={"gtin": _GTIN, "brand": "SmokeTest"},
        )
    )


def test_detects_polylang(client: WordPressClient) -> None:
    # DoD: multilingual detection returns the correct value on Polylang staging.
    assert client.detect_multilingual_plugin() == "polylang"


def test_upsert_is_idempotent(client: WordPressClient) -> None:
    # DoD §6.1: two identical upserts converge on the same page id.
    first = _upsert_smoke_page(client)
    second = _upsert_smoke_page(client)
    assert first["id"] == second["id"]


def test_upload_media_is_idempotent(client: WordPressClient, tmp_path: Path) -> None:
    # DoD §6.2: uploading the same file twice yields one media asset.
    img = tmp_path / "smoke.png"
    img.write_bytes(_PNG_1X1)
    first = client.upload_media(img, title="Smoke test image")
    second = client.upload_media(img, title="Smoke test image")
    assert first == second


def test_published_page_resolves(client: WordPressClient) -> None:
    # DoD exit gate: the published page lives at its URL and returns 200/3xx.
    page = _upsert_smoke_page(client)
    link = page.get("link")
    assert isinstance(link, str) and link
    assert client.verify_url(link) is True
