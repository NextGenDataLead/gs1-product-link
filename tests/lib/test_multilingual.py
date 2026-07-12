"""Tests for the multilingual adapters (IMPLEMENTATION_SPEC §4.5).

The adapters reuse a :class:`lib.wp_client.WordPressClient`'s transport, so these tests
use a tiny fake exposing just ``_request`` to capture the outbound call without any
HTTP, plus ``make_adapter`` type mapping and the WPML stub.
"""

from __future__ import annotations

from typing import Any

import pytest

from lib.multilingual import (
    NoOpAdapter,
    PolylangAdapter,
    WPMLAdapter,
    make_adapter,
)


class _FakeClient:
    """Records ``_request`` calls the way an adapter would issue them."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def _request(self, method: str, path: str, **kwargs: Any) -> None:
        self.calls.append({"method": method, "path": path, **kwargs})


def test_make_adapter_maps_each_plugin() -> None:
    assert isinstance(make_adapter("polylang"), PolylangAdapter)
    assert isinstance(make_adapter("wpml"), WPMLAdapter)
    assert isinstance(make_adapter("none"), NoOpAdapter)


def test_noop_adapter_makes_no_request() -> None:
    fake = _FakeClient()
    NoOpAdapter().link_translations(fake, {"nl": 1, "fr": 2})  # type: ignore[arg-type]
    assert fake.calls == []


def test_polylang_links_translation_group() -> None:
    fake = _FakeClient()
    translations = {"nl": 10, "fr": 11}

    PolylangAdapter().link_translations(fake, translations)  # type: ignore[arg-type]

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/wp-json/pll/v1/translations"
    assert call["json_body"] == {"translations": translations}


def test_polylang_single_language_is_noop() -> None:
    fake = _FakeClient()
    PolylangAdapter().link_translations(fake, {"nl": 10})  # type: ignore[arg-type]
    assert fake.calls == []


def test_wpml_adapter_raises_not_implemented() -> None:
    fake = _FakeClient()
    with pytest.raises(NotImplementedError):
        WPMLAdapter().link_translations(fake, {"nl": 1, "fr": 2})  # type: ignore[arg-type]
