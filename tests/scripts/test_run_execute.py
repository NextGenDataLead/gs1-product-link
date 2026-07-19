"""Tests for scripts/run_execute.py (IMPLEMENTATION_SPEC §8.3, §5.4, §6.5, §12 Phase 6).

run_execute orchestrates the real ``TemplateEngine`` and ``render_qr`` but delegates
every HTTP mutation to the WordPress and GS1 clients, whose wire behaviour is already
covered exhaustively by ``tests/lib/test_wp_client.py`` and
``tests/lib/test_gs1_dl_client.py``. So here the two clients are replaced with recording
fakes and the tests assert the *orchestration*: order of operations, state updates,
JSONL logging, exit codes, dry-run side-effect suppression, the confirmed subset, and
§6.5 idempotency. Real HTTP wiring is exercised by the ``staging``-marked integration test.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from lib.config import (
    ClientConfig,
    ExportConfig,
    GS1Config,
    GS1LinkConfig,
    MediaConfig,
    QRConfig,
    TemplateConfig,
    WordPressConfig,
)
from lib.records import LocalisedText, Plan, PlanClassification, PlanRow, ProductRecord, State
from lib.state import load_state
from scripts import run_execute

GTIN_A = "08713195007359"
GTIN_B = "08713195007360"


# --- Fixtures / builders -----------------------------------------------------


def _make_config(**overrides: Any) -> ClientConfig:
    params: dict[str, Any] = {
        "client_id": "acme",
        "display_name": "Acme BV",
        "gs1": GS1Config(
            account_number_test="8720796420906",
            client_id_env_test="GS1_CID",
            client_secret_env_test="GS1_SEC",
            environment="test",
            digital_link_url_pattern="https://id.gs1.org/01/{gtin14}",
        ),
        "export": ExportConfig(path="input/acme.xlsx"),
        "wordpress": WordPressConfig(
            site_url="https://wp.test",
            username="bot",
            app_password_env="WP_PASS",
            post_type="product",
            default_language="nl",
            languages=["nl", "fr"],
        ),
        "template": TemplateConfig(override_dir=None),  # falls back to templates/_default
        "qr": QRConfig(formats=["svg"], size_mm=20, error_correction="M", dpi=300),
        "gs1_links": [
            GS1LinkConfig(
                link_type="pip", default=True, public=True, title_pattern="{product_name}"
            )
        ],
    }
    params.update(overrides)
    return ClientConfig(**params)


def _product(gtin: str = GTIN_A, *, image_url: str | None = None) -> ProductRecord:
    return ProductRecord(
        gtin=gtin,
        brand="Acme",
        product_name=LocalisedText(values={"nl": "Rugsteun", "fr": "Support"}),
        image_url=image_url,
    )


def _row(gtin: str = GTIN_A, language: str = "nl", *, image_url: str | None = None) -> PlanRow:
    return PlanRow(
        gtin=gtin,
        language=language,
        classification=PlanClassification.NEW,
        title="Rugsteun",
        slug=f"p-{gtin}",
        content_hash="hash-" + gtin,
        target_url=f"https://wp.test/product/p-{gtin}/",
        product=_product(gtin, image_url=image_url),
    )


def _plan(*rows: PlanRow) -> Plan:
    return Plan(
        client_id="acme",
        generated_at=datetime(2026, 7, 12, tzinfo=UTC),
        total=len(rows),
        counts={PlanClassification.NEW: len(rows)},
        rows=list(rows),
    )


def _write_json(path: Path, model: Plan) -> Path:
    path.write_text(json.dumps(model.model_dump(mode="json")), encoding="utf-8")
    return path


def _page_id(slug: str, language: str = "nl") -> int:
    """Deterministic WordPress id per (slug, language), so re-runs are idempotent (§6.5).

    Language is part of the key because the slug deliberately has *no* language component
    — nl and fr both live at ``p-{gtin}`` (that is the point of the ``?lang=`` write, see
    the page-adapter doc §3.1). Keying on the slug alone would hand both languages the
    same id, and every assertion about linking them as translations would pass vacuously.
    """
    return 1000 + int.from_bytes(hashlib.sha256(f"{slug}/{language}".encode()).digest()[:2], "big")


def _page_url(language: str, slug: str, *, post_type: str = "product") -> str:
    """The URL FakeWP returns for a page — default language at the root, others prefixed.

    Mirrors the real site's ``/noviplast/{slug}/`` vs ``/fr/noviplast/{slug}/`` split, and
    ``state.py:_lang_segment``. Without this the fake hands both languages the same URL and
    "each link points at its own language's page" is untestable.
    """
    prefix = "" if language == "nl" else f"/{language}"
    return f"https://wp.test{prefix}/{post_type}/{slug}/"


class _Recorder:
    def __init__(self) -> None:
        self.wp: list[dict[str, Any]] = []
        self.gs1: list[dict[str, Any]] = []
        self.verified: list[str] = []
        self.translations: list[dict[str, int]] = []
        self.downloaded: list[str] = []
        self.uploaded: list[dict[str, Any]] = []


def _install(
    monkeypatch: pytest.MonkeyPatch,
    cfg: ClientConfig,
    *,
    verify: bool = True,
    wp_error: Exception | None = None,
    wp_error_languages: tuple[str, ...] = ("nl", "fr"),
) -> _Recorder:
    """Patch the two clients with recording fakes.

    ``wp_error_languages`` narrows ``wp_error`` to specific languages, so a test can fail
    one language of a GTIN and leave its sibling healthy.
    """
    rec = _Recorder()

    class FakeWP:
        def __init__(self, config: WordPressConfig) -> None:
            self._default_language = config.default_language

        def __enter__(self) -> FakeWP:
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def upsert_page(self, **kw: Any) -> dict[str, Any]:
            rec.wp.append(kw)
            if wp_error is not None and kw["language"] in wp_error_languages:
                raise wp_error
            language = kw["language"]
            pid = kw["existing_id"] or _page_id(kw["slug"], language)
            return {"id": pid, "link": _page_url(language, kw["slug"], post_type=kw["post_type"])}

        def verify_url(self, url: str) -> bool:
            rec.verified.append(url)
            return verify

        def link_translations(self, translations: dict[str, int]) -> None:
            rec.translations.append(translations)

        def download_image(self, url: str) -> bytes | None:
            rec.downloaded.append(url)
            return None if url == "MISSING" else b"imgbytes:" + url.encode()

        def upload_media(self, file_path: Any, title: str | None = None) -> int:
            key = str(file_path)
            mid = 5000 + int.from_bytes(hashlib.sha256(key.encode()).digest()[:2], "big")
            rec.uploaded.append({"path": key, "title": title, "id": mid})
            return mid

        def media_source_url(self, media_id: int) -> str | None:
            return f"https://wp.test/wp-content/uploads/{media_id}.jpg"

    class FakeGS1:
        def __init__(self, config: object) -> None:
            pass

        def __enter__(self) -> FakeGS1:
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def safe_upsert(self, **kw: Any) -> None:
            rec.gs1.append(kw)

    monkeypatch.setattr(run_execute, "get_client", lambda _cid: cfg)
    monkeypatch.setattr(run_execute, "WordPressClient", FakeWP)
    monkeypatch.setattr(run_execute, "GS1DigitalLinkClient", FakeGS1)
    return rec


# --- Per-GTIN operations across languages ------------------------------------


def test_both_languages_land_in_one_gs1_link_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One GS1 write per GTIN, carrying every language — not one write per language.

    GS1's CreateOrUpdate **replaces** the links array (confirmed live against the real
    API). The pipeline used to issue one ``safe_upsert`` per (GTIN, language), each with a
    single-element array, so the fr row overwrote the record with only its own link — the
    nl link was destroyed, the Dutch QR resolved nowhere, and the row reported ``ok``.
    """
    monkeypatch.chdir(tmp_path)
    cfg = _make_config()
    rec = _install(monkeypatch, cfg)
    plan = _write_json(tmp_path / "plan.json", _plan(_row(GTIN_A, "nl"), _row(GTIN_A, "fr")))

    code = run_execute.main(["acme", "--plan", str(plan)])

    assert code == 0
    assert len(rec.gs1) == 1  # one write for the GTIN, not one per language
    links = {link["language"]: link for link in rec.gs1[0]["links"]}
    assert set(links) == {"nl", "fr"}
    # Each link points at its own language's page.
    assert links["nl"]["target_url"] == _page_url("nl", f"p-{GTIN_A}")
    assert links["fr"]["target_url"] == _page_url("fr", f"p-{GTIN_A}")
    assert links["fr"]["link_title"] == "Support"  # the fr product_name, not the nl one
    # "standaardlink voor nl, niet voor fr" — exactly one default link, and it is nl.
    assert links["nl"]["default_link_type"] is True
    assert links["fr"]["default_link_type"] is False


def test_translations_are_linked_once_per_gtin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pages are linked as a translation group — the third call of §3.1.

    ``link_translations`` existed on the client and was never called from the pipeline, so
    a run left nl and fr as unrelated pages with their own trids.
    """
    monkeypatch.chdir(tmp_path)
    cfg = _make_config()
    rec = _install(monkeypatch, cfg)
    plan = _write_json(tmp_path / "plan.json", _plan(_row(GTIN_A, "nl"), _row(GTIN_A, "fr")))

    assert run_execute.main(["acme", "--plan", str(plan)]) == 0

    assert rec.translations == [
        {
            "nl": _page_id(f"p-{GTIN_A}", "nl"),
            "fr": _page_id(f"p-{GTIN_A}", "fr"),
        }
    ]


def test_sibling_language_failure_blocks_the_whole_gtin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If one language's page fails, the GTIN gets no GS1 write and no state at all.

    A link set built from the surviving language would **replace** the array and destroy
    the failed language's link. And writing state for the survivor would make the next run
    classify it UNCHANGED, so the GS1 write would never be retried.
    """
    monkeypatch.chdir(tmp_path)
    cfg = _make_config()
    rec = _install(monkeypatch, cfg, wp_error=RuntimeError("boom"), wp_error_languages=("fr",))
    plan = _write_json(tmp_path / "plan.json", _plan(_row(GTIN_A, "nl"), _row(GTIN_A, "fr")))

    code = run_execute.main(["acme", "--plan", str(plan)])

    assert code == 1
    assert rec.gs1 == []  # a partial link set would have destroyed the fr link
    assert rec.translations == []
    assert load_state("acme").entries == {}  # nl must stay retryable
    logs = list((tmp_path / "output" / "acme" / "runs").glob("*.jsonl"))
    outcomes = [json.loads(line) for line in logs[0].read_text().splitlines()]
    assert [o["status"] for o in outcomes] == ["error", "error"]


def test_partial_confirm_reconstructs_the_other_language_from_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Confirming only fr must not drop nl from the link set.

    The orchestrator confirms rows individually, so an operator can apply fr and skip nl.
    Because the array replaces, sending links:[fr] would destroy the nl link — so the nl
    link is rebuilt from the state entry written by the run that created its page.
    """
    monkeypatch.chdir(tmp_path)
    cfg = _make_config()
    rec = _install(monkeypatch, cfg)

    # A prior run created the nl page.
    both = _plan(_row(GTIN_A, "nl"), _row(GTIN_A, "fr"))
    nl_only = _write_json(tmp_path / "nl.json", _plan(_row(GTIN_A, "nl")))
    assert run_execute.main(["acme", "--plan", str(nl_only)]) == 0
    rec.gs1.clear()
    rec.translations.clear()

    # Now only the fr row is confirmed.
    confirmed = {
        "plan": both.model_dump(mode="json"),
        "confirmed_gtins_by_lang": [[GTIN_A, "fr"]],
    }
    path = tmp_path / "confirmed.json"
    path.write_text(json.dumps(confirmed), encoding="utf-8")

    assert run_execute.main(["acme", "--confirmed", str(path)]) == 0

    assert len(rec.gs1) == 1
    links = {link["language"]: link for link in rec.gs1[0]["links"]}
    assert set(links) == {"nl", "fr"}  # nl survives, rebuilt from state
    assert links["nl"]["target_url"] == _page_url("nl", f"p-{GTIN_A}")
    assert links["nl"]["default_link_type"] is True
    # The translation group keeps the stored nl page id alongside the fresh fr one.
    assert rec.translations == [
        {"nl": _page_id(f"p-{GTIN_A}", "nl"), "fr": _page_id(f"p-{GTIN_A}", "fr")}
    ]


# --- Happy path --------------------------------------------------------------


def test_happy_path_one_gtin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _make_config()
    rec = _install(monkeypatch, cfg)
    plan = _write_json(tmp_path / "plan.json", _plan(_row()))

    code = run_execute.main(["acme", "--plan", str(plan)])

    assert code == 0
    # WP upsert (new: existing_id None) -> verify -> GS1 set, in order.
    assert rec.wp[0]["existing_id"] is None
    assert rec.wp[0]["meta"] == {"gtin": GTIN_A}
    assert rec.verified == [f"https://wp.test/product/p-{GTIN_A}/"]
    assert rec.gs1[0]["gtin"] == GTIN_A
    assert rec.gs1[0]["overwrite"] is True
    # GS1 link points at the actual page URL and carries the resolved title. Keyed by
    # language, not index: the link set spans every language of the GTIN, in sorted order.
    links = {link["language"]: link for link in rec.gs1[0]["links"]}
    assert set(links) == {"nl"}  # this plan confirms only nl
    assert links["nl"]["target_url"] == f"https://wp.test/product/p-{GTIN_A}/"
    assert links["nl"]["link_title"] == "Rugsteun"  # title_pattern "{product_name}" for nl
    # State persisted for the row.
    state = load_state("acme")
    entry = state.entries[GTIN_A]["nl"]
    assert entry.wp_page_id == _page_id(f"p-{GTIN_A}")
    assert entry.content_hash == "hash-" + GTIN_A
    assert entry.title == "Rugsteun"  # the next run diffs the title against this (§10.6.2)
    # QR rendered to disk.
    assert (tmp_path / "output" / "acme" / "qr" / f"{GTIN_A}.svg").is_file()
    # One ok outcome logged.
    logs = list((tmp_path / "output" / "acme" / "runs").glob("*.jsonl"))
    outcomes = [json.loads(line) for line in logs[0].read_text().splitlines()]
    assert [o["status"] for o in outcomes] == ["ok"]


# --- §6.5 idempotency --------------------------------------------------------


def test_rerun_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _make_config()
    _install(monkeypatch, cfg)
    plan = _write_json(tmp_path / "plan.json", _plan(_row()))

    assert run_execute.main(["acme", "--plan", str(plan)]) == 0
    first = _entry_without_timestamp(load_state("acme"))

    assert run_execute.main(["acme", "--plan", str(plan)]) == 0
    second = _entry_without_timestamp(load_state("acme"))

    # §6.5: same confirmed plan twice -> same final state (ids/hashes), no duplicates.
    assert first == second


def _entry_without_timestamp(state: State) -> dict[str, dict[str, dict[str, object]]]:
    out: dict[str, dict[str, dict[str, object]]] = {}
    for gtin, langs in state.entries.items():
        out[gtin] = {}
        for lang, entry in langs.items():
            dumped = entry.model_dump(mode="json")
            dumped.pop("last_run")  # advances every run by design
            out[gtin][lang] = dumped
    return out


# --- Error path --------------------------------------------------------------


def test_verify_failure_marks_error_and_skips_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _make_config()
    rec = _install(monkeypatch, cfg, verify=False)
    plan = _write_json(tmp_path / "plan.json", _plan(_row()))

    code = run_execute.main(["acme", "--plan", str(plan)])

    assert code == 1
    assert rec.gs1 == []  # never reached GS1 after the failed verify
    assert load_state("acme").entries == {}  # row not persisted
    logs = list((tmp_path / "output" / "acme" / "runs").glob("*.jsonl"))
    outcomes = [json.loads(line) for line in logs[0].read_text().splitlines()]
    assert outcomes[0]["status"] == "error"
    assert "did not return 200" in outcomes[0]["error"]


# --- Dry run -----------------------------------------------------------------


def test_dry_run_performs_no_mutations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _make_config()
    rec = _install(monkeypatch, cfg)
    plan = _write_json(tmp_path / "plan.json", _plan(_row()))

    code = run_execute.main(["acme", "--plan", str(plan), "--dry-run"])

    assert code == 0
    assert rec.wp == [] and rec.gs1 == []  # no mutating client calls
    assert not (tmp_path / "output" / "acme" / "state.json").exists()  # no state write
    qr_dir = tmp_path / "output" / "acme" / "qr"
    assert not qr_dir.exists() or not list(qr_dir.glob("*"))  # no QR files
    logs = list((tmp_path / "output" / "acme" / "runs").glob("*.jsonl"))
    outcomes = [json.loads(line) for line in logs[0].read_text().splitlines()]
    assert outcomes[0]["status"] == "dry-run"


# --- Confirmed subset --------------------------------------------------------


def test_confirmed_subset_executes_only_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _make_config()
    rec = _install(monkeypatch, cfg)
    plan = _plan(_row(GTIN_A, "nl"), _row(GTIN_B, "nl"))
    confirmed = {
        "plan": plan.model_dump(mode="json"),
        "confirmed_gtins_by_lang": [[GTIN_A, "nl"]],
    }
    path = tmp_path / "confirmed.json"
    path.write_text(json.dumps(confirmed), encoding="utf-8")

    code = run_execute.main(["acme", "--confirmed", str(path)])

    assert code == 0
    assert [c["meta"]["gtin"] for c in rec.wp] == [GTIN_A]  # only the confirmed row
    assert set(load_state("acme").entries) == {GTIN_A}


# --- Held rows (§8.3) --------------------------------------------------------


def _held(row: PlanRow) -> PlanRow:
    return row.model_copy(update={"classification": PlanClassification.HELD})


def test_drop_held_skips_held_gtins_by_default() -> None:
    rows = [_held(_row(GTIN_A)), _row(GTIN_B)]

    kept = run_execute._drop_held(rows, revive=False)

    assert [row.gtin for row in kept] == [GTIN_B]


def test_drop_held_keeps_everything_with_revive() -> None:
    rows = [_held(_row(GTIN_A)), _row(GTIN_B)]

    kept = run_execute._drop_held(rows, revive=True)

    assert [row.gtin for row in kept] == [GTIN_A, GTIN_B]


def test_drop_held_drops_the_whole_gtin_not_just_the_held_row() -> None:
    # The resolver write carries every language at once, so publishing the fr row of a
    # held GTIN would write a link set missing nl — the per-language destruction the
    # per-GTIN phase exists to prevent.
    rows = [_held(_row(GTIN_A, "nl")), _row(GTIN_A, "fr"), _row(GTIN_B, "nl")]

    kept = run_execute._drop_held(rows, revive=False)

    assert [(row.gtin, row.language) for row in kept] == [(GTIN_B, "nl")]


def test_held_gtin_is_not_republished_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The failure this guards: confirming a plan is a judgement about content, not a
    # licence to undo somebody's unpublish. No WP or GS1 write may reach a held GTIN.
    monkeypatch.chdir(tmp_path)
    rec = _install(monkeypatch, _make_config())
    path = _write_json(tmp_path / "plan.json", _plan(_held(_row(GTIN_A)), _row(GTIN_B)))

    code = run_execute.main(["acme", "--plan", str(path)])

    assert code == 0
    assert [c["meta"]["gtin"] for c in rec.wp] == [GTIN_B]
    assert [c["gtin"] for c in rec.gs1] == [GTIN_B]
    assert set(load_state("acme").entries) == {GTIN_B}


def test_revive_republishes_a_held_gtin_and_clears_the_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # --revive writes a fresh StateEntry, whose wp_status/gs1_enabled defaults are the
    # published condition — so a successful revive clears the hold with no extra code.
    monkeypatch.chdir(tmp_path)
    rec = _install(monkeypatch, _make_config())
    path = _write_json(tmp_path / "plan.json", _plan(_held(_row(GTIN_A))))

    code = run_execute.main(["acme", "--plan", str(path), "--revive"])

    assert code == 0
    assert [c["meta"]["gtin"] for c in rec.wp] == [GTIN_A]
    entry = load_state("acme").entries[GTIN_A]["nl"]
    assert entry.wp_status == "publish"
    assert entry.gs1_enabled is True


# --- Config / setup errors ---------------------------------------------------


def test_unknown_client_returns_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    plan = _write_json(tmp_path / "plan.json", _plan(_row()))

    # Real get_client against the repo clients.yml raises for an unknown id.
    code = run_execute.main(["no-such-client", "--plan", str(plan)])

    assert code == 2


def test_requires_plan_or_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit):  # argparse mutually-exclusive group is required
        run_execute.main(["acme"])


# --- Media (Phase 9.5) -------------------------------------------------------


def _media_config(**media_kw: Any) -> ClientConfig:
    return _make_config(media=MediaConfig(**media_kw))


def _fake_convert(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the real Pillow convert with a stub that writes a stand-in JPEG at the dest."""

    def convert(data: bytes, dest: Path, *, max_dim: int = 1600, quality: int = 85) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"jpeg:" + data[:8])
        return dest

    monkeypatch.setattr(run_execute, "convert_image_for_web", convert)


def _write_video_map(tmp_path: Path, entries: dict[str, list[dict[str, str]]]) -> Path:
    path = tmp_path / "mapping.yml"
    path.write_text(yaml.safe_dump(entries), encoding="utf-8")
    return path


def test_hero_image_downloaded_converted_uploaded_and_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _media_config()
    rec = _install(monkeypatch, cfg)
    _fake_convert(monkeypatch)
    plan = _write_json(
        tmp_path / "plan.json", _plan(_row(GTIN_A, "nl", image_url="https://cdn/x.jpg"))
    )

    assert run_execute.main(["acme", "--plan", str(plan)]) == 0

    assert rec.downloaded == ["https://cdn/x.jpg"]
    assert len(rec.uploaded) == 1
    hero_id = rec.uploaded[0]["id"]
    kw = rec.wp[0]
    assert kw["featured_media"] == hero_id
    assert kw["acf"]["product_header_image"] == hero_id
    assert kw["acf"]["product_regular_image"] == hero_id


def test_image_write_shape_url_uses_source_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _media_config(image_write_shape="url")
    rec = _install(monkeypatch, cfg)
    _fake_convert(monkeypatch)
    plan = _write_json(
        tmp_path / "plan.json", _plan(_row(GTIN_A, "nl", image_url="https://cdn/x.jpg"))
    )

    assert run_execute.main(["acme", "--plan", str(plan)]) == 0

    hero_id = rec.uploaded[0]["id"]
    kw = rec.wp[0]
    # featured_media is always the attachment id; only the ACF image fields switch to a URL.
    assert kw["featured_media"] == hero_id
    assert kw["acf"]["product_header_image"] == f"https://wp.test/wp-content/uploads/{hero_id}.jpg"


def test_missing_image_still_publishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _media_config()
    rec = _install(monkeypatch, cfg)
    plan = _write_json(tmp_path / "plan.json", _plan(_row(GTIN_A, "nl", image_url="MISSING")))

    assert run_execute.main(["acme", "--plan", str(plan)]) == 0

    assert rec.uploaded == []  # nothing uploaded
    kw = rec.wp[0]
    assert kw["featured_media"] is None
    assert "product_header_image" not in kw["acf"]
    assert rec.verified  # the page was still created and verified (E7)


def test_video_set_on_correct_language_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    mapping = _write_video_map(
        tmp_path,
        {
            "nl": [{"file": "vid_nl.mp4", "gtin": GTIN_A}],
            "fr": [{"file": "vid_fr.mp4", "gtin": GTIN_A}],
        },
    )
    cfg = _media_config(
        video_folders={"nl": str(tmp_path / "vnl"), "fr": str(tmp_path / "vfr")},
        video_map_path=str(mapping),
        video_transcode=False,  # prepare_video returns the source path unchanged
    )
    rec = _install(monkeypatch, cfg)
    plan = _write_json(tmp_path / "plan.json", _plan(_row(GTIN_A, "nl"), _row(GTIN_A, "fr")))

    assert run_execute.main(["acme", "--plan", str(plan)]) == 0

    acf_by_lang = {kw["language"]: kw["acf"] for kw in rec.wp}
    nl_video = acf_by_lang["nl"]["product_header_video_file"]
    fr_video = acf_by_lang["fr"]["product_header_video_file"]
    assert nl_video != fr_video  # each language got its own video attachment
    # the uploaded paths were the language-correct files
    paths = {u["path"] for u in rec.uploaded}
    assert any(p.endswith("vnl/vid_nl.mp4") for p in paths)
    assert any(p.endswith("vfr/vid_fr.mp4") for p in paths)


def test_no_matching_video_leaves_field_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    mapping = _write_video_map(tmp_path, {"nl": [], "fr": []})
    cfg = _media_config(
        video_folders={"nl": str(tmp_path / "vnl")},
        video_map_path=str(mapping),
    )
    rec = _install(monkeypatch, cfg)
    plan = _write_json(tmp_path / "plan.json", _plan(_row(GTIN_A, "nl")))

    assert run_execute.main(["acme", "--plan", str(plan)]) == 0

    assert "product_header_video_file" not in rec.wp[0]["acf"]


def test_state_records_featured_media_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _media_config()
    rec = _install(monkeypatch, cfg)
    _fake_convert(monkeypatch)
    plan = _write_json(
        tmp_path / "plan.json", _plan(_row(GTIN_A, "nl", image_url="https://cdn/x.jpg"))
    )

    assert run_execute.main(["acme", "--plan", str(plan)]) == 0

    hero_id = rec.uploaded[0]["id"]
    entry = load_state("acme").entries[GTIN_A]["nl"]
    assert entry.wp_featured_media_id == hero_id


def test_media_rerun_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _media_config()
    _fake_convert(monkeypatch)
    plan = _write_json(
        tmp_path / "plan.json", _plan(_row(GTIN_A, "nl", image_url="https://cdn/x.jpg"))
    )

    rec1 = _install(monkeypatch, cfg)
    assert run_execute.main(["acme", "--plan", str(plan)]) == 0
    rec2 = _install(monkeypatch, cfg)
    assert run_execute.main(["acme", "--plan", str(plan)]) == 0

    # deterministic converter + content-hash dedupe → the same attachment id both runs.
    assert rec1.uploaded[0]["id"] == rec2.uploaded[0]["id"]
    assert rec2.wp[0]["featured_media"] == rec1.uploaded[0]["id"]


def test_dry_run_uploads_no_media(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _media_config()
    rec = _install(monkeypatch, cfg)
    plan = _write_json(
        tmp_path / "plan.json", _plan(_row(GTIN_A, "nl", image_url="https://cdn/x.jpg"))
    )

    assert run_execute.main(["acme", "--plan", str(plan), "--dry-run"]) == 0

    assert rec.downloaded == []
    assert rec.uploaded == []
