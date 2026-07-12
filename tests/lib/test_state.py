"""Unit tests for lib/state.py (IMPLEMENTATION_SPEC §4.8, §12 Phase 6).

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

from lib.errors import StateError
from lib.records import LocalisedText, ProductRecord, State, StateEntry
from lib.state import compute_content_hash, load_state, save_state, state_path

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
