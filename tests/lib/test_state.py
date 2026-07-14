"""Unit tests for lib/state.py (IMPLEMENTATION_SPEC §4.8, §12 Phase 6/7).

Covers the round-trip, the atomic-write / kill-mid-write no-corruption guarantee
(the Phase 6 DoD atomicity item), content-hash determinism, and StateError on a
corrupt file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lib.config import WordPressConfig
from lib.errors import ConfigError, StateError
from lib.records import LocalisedText, PlanClassification, ProductRecord, State, StateEntry
from lib.state import (
    compute_content_hash,
    diff_against_state,
    load_state,
    save_state,
    state_path,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HASH_LEN = 64


def _product(**overrides: object) -> ProductRecord:
    base: dict[str, object] = {
        "gtin": "08713195007359",
        "brand": "Noviplast",
        "product_name": LocalisedText(values={"nl": "Rugsteun", "fr": "Support arrière"}),
    }
    base.update(overrides)
    return ProductRecord(**base)


def _entry(page_id: int = 1) -> StateEntry:
    return StateEntry(
        wp_page_id=page_id,
        wp_url=f"https://noviplast.test/p/{page_id}",
        wp_featured_media_id=None,
        content_hash="c" * _HASH_LEN,
        gs1_link_set_hash="g" * _HASH_LEN,
        last_run=datetime(2026, 7, 12, 10, 0, tzinfo=UTC),
        title="Rugsteun",
    )


# --- load_state / save_state round-trip --------------------------------------


def test_load_state_absent_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    state = load_state("noviplast")
    assert state.client_id == "noviplast"
    assert state.entries == {}


def test_save_then_load_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    original = State(client_id="noviplast", entries={"08713195007359": {"nl": _entry(7)}})

    save_state(original)
    reloaded = load_state("noviplast")

    assert reloaded == original
    assert state_path("noviplast").is_file()


def test_load_state_corrupt_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    path = state_path("noviplast")
    path.parent.mkdir(parents=True)
    path.write_text("{ not valid json", encoding="utf-8")

    with pytest.raises(StateError):
        load_state("noviplast")


# --- Atomicity / kill-mid-write (§12 Phase 6 DoD) ----------------------------


def test_save_state_replace_failure_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure at the replace step must leave the prior file intact, no stray temp."""
    monkeypatch.chdir(tmp_path)
    save_state(State(client_id="noviplast", entries={"08713195007359": {"nl": _entry(1)}}))
    path = state_path("noviplast")
    before = path.read_bytes()

    def _boom(_src: object, _dst: object) -> None:
        raise OSError("simulated crash during replace")

    monkeypatch.setattr("lib.state.os.replace", _boom)
    with pytest.raises(StateError):
        save_state(State(client_id="noviplast", entries={"99999999": {"nl": _entry(2)}}))

    assert path.read_bytes() == before  # original untouched, not truncated
    assert load_state("noviplast").entries.keys() == {"08713195007359"}
    assert not list(path.parent.glob("*.tmp"))  # temp cleaned up


def test_save_state_survives_sigkill_mid_write(tmp_path: Path) -> None:
    """SIGKILL a process hammering save_state; the file must never be corrupt.

    Because save_state writes to a temp file then ``os.replace``s it, the target is
    always either the old or a fully-written new state — never a partial one.
    """
    child = tmp_path / "hammer.py"
    child.write_text(
        "from datetime import UTC, datetime\n"
        "from lib.records import State, StateEntry\n"
        "from lib.state import save_state\n"
        "def e(i):\n"
        "    return StateEntry(wp_page_id=i, wp_url=f'https://x/{i}', wp_featured_media_id=None,\n"
        "        content_hash='c'*64, gs1_link_set_hash='g'*64, last_run=datetime.now(UTC))\n"
        "save_state(State(client_id='k', entries={'1': {'nl': e(1)}}))\n"
        "big = {str(g): {'nl': e(g)} for g in range(3000)}\n"
        "while True:\n"
        "    save_state(State(client_id='k', entries=big))\n",
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT)}
    proc = subprocess.Popen([sys.executable, str(child)], cwd=tmp_path, env=env)  # noqa: S603
    time.sleep(0.4)
    proc.kill()
    proc.wait()

    path = tmp_path / "output" / "k" / "state.json"
    assert path.is_file()
    parsed = State.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert parsed.client_id == "k"  # loads cleanly: old or new, never corrupt


# --- compute_content_hash ----------------------------------------------------


def test_content_hash_is_deterministic() -> None:
    a = compute_content_hash(_product(), "nl", "https://noviplast.test/p/1")
    b = compute_content_hash(_product(), "nl", "https://noviplast.test/p/1")
    assert a == b
    assert len(a) == _HASH_LEN
    assert all(c in "0123456789abcdef" for c in a)


@pytest.mark.parametrize(
    ("language", "target_url", "product"),
    [
        ("fr", "https://noviplast.test/p/1", _product()),
        ("nl", "https://noviplast.test/p/2", _product()),
        ("nl", "https://noviplast.test/p/1", _product(brand="Other")),
    ],
)
def test_content_hash_sensitive_to_each_input(
    language: str, target_url: str, product: ProductRecord
) -> None:
    baseline = compute_content_hash(_product(), "nl", "https://noviplast.test/p/1")
    assert compute_content_hash(product, language, target_url) != baseline


# --- diff_against_state (§4.8, §8.2, Phase 7) --------------------------------


def _wp(**overrides: object) -> WordPressConfig:
    base: dict[str, object] = {
        "site_url": "https://noviplast.test",
        "username": "bot",
        "app_password_env": "NOVIPLAST_WP_APP_PASS",
        "post_type": "noviplast",
        "languages": ["nl", "fr"],
        "default_language": "nl",
        "slug_pattern": "p-{gtin}",
        "target_url_pattern": "{site_url}/{lang_segment}{post_type}/{slug}/",
    }
    base.update(overrides)
    return WordPressConfig(**base)


def _row_for(rows: list[object], language: str) -> object:
    return next(r for r in rows if getattr(r, "language") == language)  # noqa: B009


def _state_with(
    gtin: str,
    language: str,
    *,
    content_hash: str,
    wp_url: str,
    title: str | None = "Rugsteun",
) -> State:
    entry = StateEntry(
        wp_page_id=1,
        wp_url=wp_url,
        wp_featured_media_id=None,
        content_hash=content_hash,
        gs1_link_set_hash="g" * _HASH_LEN,
        last_run=datetime(2026, 7, 12, 10, 0, tzinfo=UTC),
        title=title,
    )
    return State(client_id="noviplast", entries={gtin: {language: entry}})


def test_diff_new_when_no_state_entry() -> None:
    rows = diff_against_state([_product()], State(client_id="noviplast", entries={}), ["nl"], _wp())

    assert len(rows) == 1
    assert rows[0].classification is PlanClassification.NEW
    assert rows[0].diff is None


def test_diff_slug_and_target_url_built_from_patterns() -> None:
    product = _product(gtin="08713195007359")
    rows = diff_against_state(
        [product], State(client_id="noviplast", entries={}), ["nl", "fr"], _wp()
    )

    nl, fr = _row_for(rows, "nl"), _row_for(rows, "fr")
    assert nl.slug == "p-08713195007359"
    # Default language has no language path segment; a non-default one does.
    assert nl.target_url == "https://noviplast.test/noviplast/p-08713195007359/"
    assert fr.target_url == "https://noviplast.test/fr/noviplast/p-08713195007359/"
    assert nl.title == "Rugsteun"
    assert fr.title == "Support arrière"


def test_diff_unchanged_when_hash_matches() -> None:
    product = _product()
    baseline = diff_against_state(
        [product], State(client_id="noviplast", entries={}), ["nl"], _wp()
    )[0]
    state = _state_with(
        product.gtin, "nl", content_hash=baseline.content_hash, wp_url=baseline.target_url
    )

    rows = diff_against_state([product], state, ["nl"], _wp())

    assert rows[0].classification is PlanClassification.UNCHANGED
    assert rows[0].diff is None


def test_diff_changed_in_body_only_has_no_diff() -> None:
    product = _product()
    baseline = diff_against_state(
        [product], State(client_id="noviplast", entries={}), ["nl"], _wp()
    )[0]
    # Title and URL both unmoved, stale content hash -> the change is in the product
    # body, which state does not retain. CHANGED, but no field-level diff to show.
    state = _state_with(product.gtin, "nl", content_hash="stale", wp_url=baseline.target_url)

    rows = diff_against_state([product], state, ["nl"], _wp())

    assert rows[0].classification is PlanClassification.CHANGED
    assert rows[0].diff is None


def test_diff_changed_surfaces_title_when_renamed() -> None:
    # The Phase 7 exit-gate scenario (PROJECT_HANDOVER §8.2): rename a product, re-run,
    # and the CHANGED prompt must say what changed. The slug is GTIN-derived, so the URL
    # does not move and the title is the only thing to show.
    renamed = _product(product_name=LocalisedText(values={"nl": "Rugsteun Pro"}))
    baseline = diff_against_state(
        [renamed], State(client_id="noviplast", entries={}), ["nl"], _wp()
    )[0]
    state = _state_with(
        renamed.gtin, "nl", content_hash="stale", wp_url=baseline.target_url, title="Rugsteun"
    )

    rows = diff_against_state([renamed], state, ["nl"], _wp())

    assert rows[0].classification is PlanClassification.CHANGED
    assert rows[0].diff == {"title": ("Rugsteun", "Rugsteun Pro")}


def test_diff_changed_surfaces_target_url_when_moved() -> None:
    product = _product()
    baseline = diff_against_state(
        [product], State(client_id="noviplast", entries={}), ["nl"], _wp()
    )[0]
    state = _state_with(product.gtin, "nl", content_hash="stale", wp_url="https://old.test/x/")

    rows = diff_against_state([product], state, ["nl"], _wp())

    assert rows[0].classification is PlanClassification.CHANGED
    assert rows[0].diff == {"target_url": ("https://old.test/x/", baseline.target_url)}


def test_diff_changed_surfaces_title_and_target_url_together() -> None:
    renamed = _product(product_name=LocalisedText(values={"nl": "Rugsteun Pro"}))
    baseline = diff_against_state(
        [renamed], State(client_id="noviplast", entries={}), ["nl"], _wp()
    )[0]
    state = _state_with(
        renamed.gtin, "nl", content_hash="stale", wp_url="https://old.test/x/", title="Rugsteun"
    )

    rows = diff_against_state([renamed], state, ["nl"], _wp())

    # §10.6.2 presents title before target_url.
    assert list(rows[0].diff or {}) == ["title", "target_url"]
    assert rows[0].diff == {
        "title": ("Rugsteun", "Rugsteun Pro"),
        "target_url": ("https://old.test/x/", baseline.target_url),
    }


def test_diff_state_without_recorded_title_omits_title_diff() -> None:
    # State written before titles were persisted: the title is unknown, so it is omitted
    # rather than fabricated. The URL diff still works.
    renamed = _product(product_name=LocalisedText(values={"nl": "Rugsteun Pro"}))
    baseline = diff_against_state(
        [renamed], State(client_id="noviplast", entries={}), ["nl"], _wp()
    )[0]
    state = _state_with(
        renamed.gtin, "nl", content_hash="stale", wp_url="https://old.test/x/", title=None
    )

    rows = diff_against_state([renamed], state, ["nl"], _wp())

    assert rows[0].diff == {"target_url": ("https://old.test/x/", baseline.target_url)}


def test_diff_multilanguage_expands_rows() -> None:
    rows = diff_against_state(
        [_product()], State(client_id="noviplast", entries={}), ["nl", "fr"], _wp()
    )

    assert {r.language for r in rows} == {"nl", "fr"}


def test_diff_missing_product_name_for_language_is_omitted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    product = _product(product_name=LocalisedText(values={"nl": "Rugsteun"}))  # no fr

    with caplog.at_level("WARNING", logger="lib.state"):
        rows = diff_against_state(
            [product], State(client_id="noviplast", entries={}), ["nl", "fr"], _wp()
        )

    assert [r.language for r in rows] == ["nl"]
    assert "missing product_name.fr" in caplog.text


def test_diff_empty_products_yields_no_rows() -> None:
    rows = diff_against_state([], State(client_id="noviplast", entries={}), ["nl"], _wp())
    assert rows == []


def test_diff_missing_patterns_raises() -> None:
    with pytest.raises(ConfigError, match="slug_pattern"):
        diff_against_state(
            [_product()],
            State(client_id="noviplast", entries={}),
            ["nl"],
            _wp(slug_pattern=None),
        )
