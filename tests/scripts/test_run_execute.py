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

from lib.config import (
    ClientConfig,
    ExportConfig,
    GS1Config,
    GS1LinkConfig,
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


def _product(gtin: str = GTIN_A) -> ProductRecord:
    return ProductRecord(
        gtin=gtin,
        brand="Acme",
        product_name=LocalisedText(values={"nl": "Rugsteun", "fr": "Support"}),
    )


def _row(gtin: str = GTIN_A, language: str = "nl") -> PlanRow:
    return PlanRow(
        gtin=gtin,
        language=language,
        classification=PlanClassification.NEW,
        title="Rugsteun",
        slug=f"p-{gtin}",
        content_hash="hash-" + gtin,
        target_url=f"https://wp.test/product/p-{gtin}/",
        product=_product(gtin),
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
