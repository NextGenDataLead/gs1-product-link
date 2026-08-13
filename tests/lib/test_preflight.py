"""Tests for lib/preflight.py — the checks behind ``scripts/doctor``.

Each check is a pure function from configuration to a verdict, so most of these build a
``ClientConfig`` and assert the status and the words. The two network checks are driven
against recording fakes patched over ``WordPressClient`` / ``GS1DigitalLinkClient``, because
what is being tested is the *interpretation* of each failure — which one gets the six-groups
hint, which one names the 21011 contract blocker — not the HTTP wiring those clients already
have covered exhaustively.

One test here is about a side effect rather than a verdict: a preflight must never quarantine
a corrupt ``state.json``. That is what makes it safe to run at any time.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from lib.config import (
    ClientConfig,
    ExportConfig,
    GeneratorConfig,
    GS1Config,
    MediaConfig,
    ProcessListConfig,
    WordPressConfig,
)
from lib.errors import GS1APIError, MissingCredentialError, WordPressAPIError
from lib.generator import ORIGIN_GENERATED, CacheEntry, GeneratedCache, save_cache
from lib.preflight import (
    Status,
    check_cache_coverage,
    check_config,
    check_ffmpeg,
    check_generator,
    check_gs1,
    check_process_list,
    check_scope,
    check_video_coverage,
    check_wordpress,
    in_scope,
    run_checks,
    worst_status,
)
from lib.records import LocalisedText, ProductRecord
from lib.wp_client import WordPressIdentity

GTIN_A = "08713195007359"
GTIN_B = "08713195007360"


# --- Builders ----------------------------------------------------------------


def _make_config(**overrides: Any) -> ClientConfig:
    params: dict[str, Any] = {
        "client_id": "acme",
        "display_name": "Acme BV",
        "gs1": GS1Config(
            account_number_test="8720796420906",
            client_id_env_test="GS1_CID",
            client_secret_env_test="GS1_SEC",
            environment="test",
        ),
        "export": ExportConfig(path="input/acme.xlsx"),
        "wordpress": WordPressConfig(
            site_url="https://wp.test",
            username="bot",
            app_password_env="WP_PASS",
            post_type="product",
            default_language="nl",
            languages=["nl"],
            slug_pattern="p-{gtin}",
            target_url_pattern="{site_url}/{lang_segment}{post_type}/{slug}/",
        ),
    }
    params.update(overrides)
    return ClientConfig(**params)


def _product(gtin: str = GTIN_A) -> ProductRecord:
    return ProductRecord(
        gtin=gtin, brand="Acme", product_name=LocalisedText(values={"nl": "Rugsteun"})
    )


def _write_process_list(tmp_path: Path, gtins: list[str]) -> ProcessListConfig:
    import openpyxl  # noqa: PLC0415 — only this helper needs it

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Barcode"])
    for gtin in gtins:
        sheet.append([gtin])
    path = tmp_path / "process-list.xlsx"
    workbook.save(path)
    return ProcessListConfig(path=str(path), gtin_column="Barcode")


def _write_video_map(tmp_path: Path, confirmed: list[str], languages: list[str]) -> str:
    entries = {
        language: [{"file": f"{g}_{language}.mp4", "gtin": g, "confirmed": True} for g in confirmed]
        for language in languages
    }
    path = tmp_path / "mapping.yml"
    path.write_text(yaml.safe_dump(entries), encoding="utf-8")
    return str(path)


# --- check_config -------------------------------------------------------------


def test_config_missing_file_says_what_to_copy(tmp_path: Path) -> None:
    result = check_config(tmp_path / "nope.yml")
    assert result.status is Status.FAIL
    assert "clients.example.yml" in result.remedy


def test_config_reports_every_schema_error_not_just_the_first(tmp_path: Path) -> None:
    """An operator with four blank fields must not fix them one run at a time.

    ``load_clients`` raises on the first violation and discards its ``json_path``, so the
    schema is walked directly here to collect all of them with the field each belongs to.
    """
    path = tmp_path / "clients.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "clients": {
                    "acme": {
                        "display_name": "Acme",
                        "gs1": {"environment": "wrong-value"},
                        "wordpress": {"site_url": "https://wp.test"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = check_config(path)

    assert result.status is Status.FAIL
    errors = result.data["errors"]
    assert isinstance(errors, list)
    assert len(errors) > 1  # not stopped at the first
    assert any("gs1" in error for error in errors)
    assert any("wordpress" in error for error in errors)


def test_config_unreadable_yaml_is_a_finding_not_a_crash(tmp_path: Path) -> None:
    path = tmp_path / "clients.yml"
    path.write_text("clients: [unclosed\n", encoding="utf-8")

    result = check_config(path)

    assert result.status is Status.FAIL
    assert "YAML" in result.detail


# --- check_generator (the E21 trap) ------------------------------------------


def test_generator_block_present_is_ok() -> None:
    result = check_generator(_make_config(generator=GeneratorConfig(enabled=True)))
    assert result.status is Status.OK
    assert "held out of the plan" in result.detail


def test_generator_block_missing_with_a_cache_present_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trap: the block looks like dead config on a machine with no API key. It is the switch.

    ``run_plan`` derives ``require_generated_copy = cfg.generator is not None``, so deleting
    the block does not raise — it turns off the E21 hold, and copy-less units publish blank
    taglines. A cache on disk proves a generator *was* configured for this client.
    """
    monkeypatch.chdir(tmp_path)
    # A cache with no entries is not evidence that a generator was ever configured; one with
    # entries is — that copy came from somewhere.
    save_cache(GeneratedCache(client_id="acme", entries={GTIN_A: {"nl": _cache_entry()}}))

    result = check_generator(_make_config())

    assert result.status is Status.FAIL
    assert "blank tagline" in result.detail
    assert "not a credential" in result.remedy


def test_generator_block_missing_with_no_cache_is_not_applicable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = check_generator(_make_config())
    assert result.status is Status.NA


def _cache_entry() -> CacheEntry:
    return CacheEntry(
        usps=["Een voordeel"],
        product_name="Rugsteun",
        origin=ORIGIN_GENERATED,
        input_fingerprint="fp",
        provenance="test",
        source_input="1083",
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


# --- Scope --------------------------------------------------------------------


def test_scope_applies_the_process_list_but_not_the_video_allowlist(tmp_path: Path) -> None:
    """The process list narrows scope; a missing video does not — it holds, visibly (E24).

    A GTIN the operator listed and cannot yet have is a different fact from one they never
    asked about, and only the first is actionable. Narrowing here made it invisible on every
    surface at once — this figure, the plan, and the quality report.
    """
    cfg = _make_config(
        process_list=_write_process_list(tmp_path, [GTIN_A, GTIN_B]),
        media=MediaConfig(
            restrict_to_mapped_gtins=True,
            video_map_path=_write_video_map(tmp_path, [GTIN_A], ["nl"]),
        ),
    )
    products = [_product(GTIN_A), _product(GTIN_B), _product("08713195007361")]

    scoped = in_scope(cfg, products)

    # B has no confirmed video but stays in scope; only the unlisted third product is dropped.
    assert [p.gtin for p in scoped] == [GTIN_A, GTIN_B]


def test_the_scope_check_names_the_gtins_and_not_only_how_many(tmp_path: Path) -> None:
    """A count lets a consumer *report* scope; the list lets it *filter* by it.

    The operator shell's Content screen needs the second: it lists generated copy, and the cache
    accumulates every unit ever generated on that machine, so showing this run's batch means
    intersecting the two. Without the list it would have to re-derive scope, and a second
    implementation of "what will this run touch" is what ``in_scope`` exists to prevent.

    ``ProductRecord.gtin`` verbatim, because that is the field the generator keys its cache by —
    a normalised variant would silently match nothing for a 13-digit feed.
    """
    cfg = _make_config(process_list=_write_process_list(tmp_path, [GTIN_A]))

    result = check_scope(cfg, [_product(GTIN_A), _product(GTIN_B)])

    assert result.data["in_scope_gtins"] == [GTIN_A]
    assert result.data["in_scope"] == 1


def test_the_gtin_list_is_never_truncated(tmp_path: Path) -> None:
    """``pending_units`` is capped at 20 because it is read; this list is *filtered with*.

    A truncated filter hides in-scope work, which is the failure this data exists to fix rather
    than a tidier version of it.
    """
    gtins = [f"0871319500{n:04d}" for n in range(50)]
    cfg = _make_config(process_list=_write_process_list(tmp_path, gtins))

    result = check_scope(cfg, [_product(gtin) for gtin in gtins])

    assert result.data["in_scope_gtins"] == gtins


def test_scope_of_nothing_is_a_failure_not_a_quiet_pass(tmp_path: Path) -> None:
    """An empty scope means a run that publishes nothing and reports success."""
    cfg = _make_config(process_list=_write_process_list(tmp_path, [GTIN_B]))

    result = check_scope(cfg, [_product(GTIN_A)])

    assert result.status is Status.FAIL
    assert "publish nothing and report success" in result.detail


# --- Cache coverage -----------------------------------------------------------


def test_cache_coverage_counts_only_the_products_in_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reporting 224 missing entries the run will never need teaches the operator to look away."""
    monkeypatch.chdir(tmp_path)
    cfg = _make_config(
        generator=GeneratorConfig(enabled=True),
        process_list=_write_process_list(tmp_path, [GTIN_A]),
    )
    save_cache(GeneratedCache(client_id="acme", entries={}))

    result = check_cache_coverage(cfg, [_product(GTIN_A), _product(GTIN_B)])

    assert result.status is Status.FAIL
    assert result.data["total"] == 1  # one in-scope product × one language
    assert result.data["pending"] == 1


def test_cache_coverage_without_a_generator_is_not_applicable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert check_cache_coverage(_make_config(), [_product()]).status is Status.NA


# --- Process list -------------------------------------------------------------


def test_process_list_with_no_gtins_fails(tmp_path: Path) -> None:
    """Zero GTINs is deliberately an error, not an empty run."""
    cfg = _make_config(process_list=_write_process_list(tmp_path, []))
    assert check_process_list(cfg).status is Status.FAIL


def test_process_list_reports_the_count(tmp_path: Path) -> None:
    cfg = _make_config(process_list=_write_process_list(tmp_path, [GTIN_A, GTIN_B]))
    result = check_process_list(cfg)
    assert result.status is Status.OK
    assert result.data["count"] == 2


# --- Video mapping ------------------------------------------------------------


def test_unconfirmed_videos_warn_rather_than_fail_under_the_restriction(tmp_path: Path) -> None:
    """The restriction is what makes a gap safe: the GTIN is excluded, not mis-published.

    Calling a handled condition a failure is how a report earns the right to be ignored.
    """
    folder = tmp_path / "videos" / "nl"
    folder.mkdir(parents=True)
    (folder / "unmapped.mp4").write_bytes(b"x")
    cfg = _make_config(
        media=MediaConfig(
            restrict_to_mapped_gtins=True,
            video_map_path=_write_video_map(tmp_path, [GTIN_A], ["nl"]),
            video_folders={"nl": str(folder)},
        )
    )

    result = check_video_coverage(cfg)

    assert result.status is Status.WARN
    assert "only those can be published" in result.detail


def test_absent_video_files_are_reported_as_absent_files(tmp_path: Path) -> None:
    """The mapping arrived and the multi-gigabyte library did not — a day-one operator machine.

    It used to read ``284 of 0 video file(s) are not yet confirmed against a GTIN``: every gap of
    every kind over the number of files on disk. The fix for "the folders are empty" is to copy
    them across, which is nothing like the fix for "the client has not confirmed these rows", so
    the two say different things now.
    """
    folder = tmp_path / "videos" / "nl"
    folder.mkdir(parents=True)  # exists, but empty — as it is before the library is copied
    cfg = _make_config(
        media=MediaConfig(
            restrict_to_mapped_gtins=True,
            video_map_path=_write_video_map(tmp_path, [GTIN_A], ["nl"]),
            video_folders={"nl": str(folder)},
        )
    )

    result = check_video_coverage(cfg)

    assert result.status is Status.WARN
    assert "no video files found" in result.detail
    assert "not yet confirmed" not in result.detail, "the mapping is confirmed; the files are gone"
    assert "media.video_folders" in result.remedy
    assert result.data["files"] == 0


def test_the_video_check_never_counts_more_than_it_measured(tmp_path: Path) -> None:
    """A regression guard on the shape of the sentence, not on one wording of it.

    ``N of M`` with N > M is not a cosmetic defect: a count that cannot be true teaches its
    reader to skip the line, on the one screen they are meant to work down. Whatever this line
    says in future, it must not claim more of something than it found.
    """
    folder = tmp_path / "videos" / "nl"
    folder.mkdir(parents=True)
    cfg = _make_config(
        media=MediaConfig(
            restrict_to_mapped_gtins=True,
            video_map_path=_write_video_map(tmp_path, [GTIN_A, GTIN_B], ["nl"]),
            video_folders={"nl": str(folder)},
        )
    )

    (folder / "one.mp4").write_bytes(b"x")
    result = check_video_coverage(cfg)

    # True of the wording as it stands: the files it reports are the files it found.
    assert result.data["files"] == 1
    assert "1 video file(s) found" in result.detail

    # And true of any future wording: no ratio may claim more than it measured.
    for numerator, denominator in re.findall(r"(\d+) of (\d+)", result.detail):
        assert int(numerator) <= int(denominator), f"impossible count in: {result.detail}"


def test_a_hand_edited_mapping_fails_with_a_position_not_a_traceback(tmp_path: Path) -> None:
    """A missing file already reported cleanly; the file a human edits is the one that crashed."""
    path = tmp_path / "mapping.yml"
    path.write_text('nl:\n  - {file: "a.mp4", gtin: ""}\n\tstray tab\n', encoding="utf-8")
    cfg = _make_config(media=MediaConfig(video_map_path=str(path)))

    result = check_video_coverage(cfg)

    assert result.status is Status.FAIL
    assert "line 3" in result.detail
    assert result.remedy, "a failure an operator can act on needs to say how"


def test_a_hand_edited_mapping_does_not_crash_the_checks_that_run_first(tmp_path: Path) -> None:
    """``check_scope`` reads the same file, earlier, and used to take the whole doctor down.

    That is why a stray tab produced a traceback instead of one red line: the crash happened
    before the check that would have reported it ever ran.
    """
    path = tmp_path / "mapping.yml"
    path.write_text('nl:\n  - {file: "a.mp4", gtin: ""}\n\tstray tab\n', encoding="utf-8")
    cfg = _make_config(
        media=MediaConfig(restrict_to_mapped_gtins=True, video_map_path=str(path)),
        process_list=None,
    )

    scope = check_scope(cfg, [_product(GTIN_A)])

    assert scope.status in {Status.OK, Status.WARN, Status.FAIL}  # a verdict, not an exception


# --- ffmpeg -------------------------------------------------------------------


def test_ffmpeg_is_not_applicable_when_transcoding_is_off() -> None:
    cfg = _make_config(media=MediaConfig(video_transcode=False))
    assert check_ffmpeg(cfg).status is Status.NA


def test_ffmpeg_missing_binary_fails_when_transcoding_is_on() -> None:
    cfg = _make_config(
        media=MediaConfig(video_transcode=True, ffmpeg_bin="ffmpeg-that-is-not-here")
    )
    result = check_ffmpeg(cfg)
    assert result.status is Status.FAIL
    assert "video_transcode" in result.remedy


# --- WordPress ----------------------------------------------------------------


def _patch_wp(monkeypatch: pytest.MonkeyPatch, **behaviour: Any) -> None:
    """Patch WordPressClient with a fake whose whoami/detect behaviour the test chooses."""

    class FakeWP:
        def __init__(self, config: WordPressConfig, **_: Any) -> None:
            self.config = config

        def __enter__(self) -> FakeWP:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def whoami(self) -> WordPressIdentity:
            error = behaviour.get("whoami_error")
            if error is not None:
                raise error
            return WordPressIdentity(
                id=1, slug="bot", roles=list(behaviour.get("roles", ["editor"]))
            )

        def detect_multilingual_plugin(self) -> str:
            return str(behaviour.get("detected", "none"))

        def verify_url(self, url: str) -> bool:
            return bool(behaviour.get("serves", True))

    monkeypatch.setattr("lib.preflight.WordPressClient", FakeWP)


def test_wordpress_401_names_the_six_group_password_trap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The commonest cause by a wide margin is a password that lost its quotes in .env.

    The symptom is a 401 with a password the operator knows is correct, so the remedy has to
    say what actually happened rather than "check your credentials".
    """
    _patch_wp(monkeypatch, whoami_error=WordPressAPIError(401, "Unauthorized"))

    result = check_wordpress(_make_config())

    assert result.status is Status.FAIL
    assert "six" in result.remedy or "6" in result.remedy
    assert "truncate" in result.remedy


def test_wordpress_credential_that_cannot_publish_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A demoted bot passes a naive users/me check and then fails mid-run, after rows are live."""
    _patch_wp(monkeypatch, roles=["subscriber"])

    result = check_wordpress(_make_config())

    assert result.status is Status.FAIL
    assert "none of which can publish" in result.detail


def test_wordpress_missing_secret_is_caught_before_any_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_wp(monkeypatch, whoami_error=MissingCredentialError("WP_PASS is not set"))
    result = check_wordpress(_make_config())
    assert result.status is Status.FAIL
    assert "WP_PASS" in result.remedy


def test_wordpress_plugin_mismatch_warns_and_says_which_way_is_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The configured value wins, so the run proceeds — but one of the two is wrong."""
    _patch_wp(monkeypatch, detected="none")
    cfg = _make_config(
        wordpress=WordPressConfig(
            site_url="https://wp.test",
            username="bot",
            app_password_env="WP_PASS",
            post_type="product",
            default_language="nl",
            languages=["nl"],
            multilingual_plugin="wpml",
        )
    )

    result = check_wordpress(cfg)

    assert result.status is Status.WARN
    assert "translations will not be linked" in result.remedy


# --- GS1 ----------------------------------------------------------------------


def _patch_gs1(monkeypatch: pytest.MonkeyPatch, **behaviour: Any) -> None:
    class FakeGS1:
        def __init__(self, config: object, **_: Any) -> None:
            pass

        def __enter__(self) -> FakeGS1:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def get(self, gtin: str) -> object | None:
            error = behaviour.get("error")
            if error is not None:
                raise error
            return behaviour.get("record")

    monkeypatch.setattr("lib.preflight.GS1DigitalLinkClient", FakeGS1)


def test_gs1_no_record_is_ok_but_refuses_to_claim_the_contract_is_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The most expensive possible false pass, so it is stated rather than glossed.

    A GTIN the resolver has never seen answers with the same ``400 "No valid contract found
    for Gtin with id: …"`` that the 21011 blocker produces, and the client maps that to
    ``None``. Reporting "contract present" here would hand the operator a green preflight on
    an account where every write fails.
    """
    _patch_gs1(monkeypatch, record=None)

    result = check_gs1(_make_config(), [_product()])

    assert result.status is Status.OK
    assert "cannot distinguish" in result.detail
    assert result.data["registered"] is False


def test_gs1_existing_record_does_prove_the_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_gs1(monkeypatch, record=object())
    result = check_gs1(_make_config(), [_product()])
    assert result.status is Status.OK
    assert "contract is live" in result.detail


def test_gs1_21011_names_the_blocker_and_says_it_is_not_fixable_here(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_gs1(
        monkeypatch,
        error=GS1APIError(
            400,
            "No valid contract found.",
            [{"identifier": GTIN_A, "errors": [{"code": "21011", "message": "No valid contract"}]}],
        ),
    )

    result = check_gs1(_make_config(), [_product()])

    assert result.status is Status.FAIL
    assert result.data["no_contract"] is True
    assert "cannot be fixed in code or config" in result.remedy


def test_gs1_will_not_invent_a_gtin_to_probe_with(monkeypatch: pytest.MonkeyPatch) -> None:
    """A GS1 record can never be deleted, so a typo'd probe GTIN would be permanent."""
    _patch_gs1(monkeypatch, record=None)

    result = check_gs1(_make_config(), [])

    assert result.status is Status.WARN
    assert "will not invent" in result.remedy


# --- Orchestration ------------------------------------------------------------


def test_a_preflight_never_quarantines_a_corrupt_state_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The invariant that makes this safe to run at any time.

    ``load_state`` recovers from a corrupt file by *moving it aside* (E19) and starting fresh.
    A diagnostic that triggered that would change what the next run does simply by looking.
    """
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "clients.yml"
    config.write_text(_MINIMAL_CONFIG, encoding="utf-8")
    state = tmp_path / "output" / "acme" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text("{ truncated", encoding="utf-8")

    run_checks("acme", config_path=config, offline=True)

    assert state.read_text(encoding="utf-8") == "{ truncated"
    assert not list(state.parent.glob("state.json.corrupt.*"))


def test_offline_stops_before_any_credential_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "clients.yml"
    config.write_text(_MINIMAL_CONFIG, encoding="utf-8")

    results = run_checks("acme", config_path=config, offline=True)

    assert {"wordpress", "gs1", "site_serves"}.isdisjoint({r.name for r in results})


def test_a_broken_config_is_the_whole_report_not_a_page_of_cascade(tmp_path: Path) -> None:
    config = tmp_path / "clients.yml"
    config.write_text("clients: {}\n", encoding="utf-8")

    results = run_checks("acme", config_path=config, offline=True)

    assert [r.name for r in results] in (["config"], ["config", "client"])
    assert results[-1].status is Status.FAIL


def test_worst_status_picks_the_most_serious() -> None:
    from lib.preflight import CheckResult  # noqa: PLC0415 — only this test needs the class

    results = [
        CheckResult("a", "A", Status.OK, ""),
        CheckResult("b", "B", Status.WARN, ""),
        CheckResult("c", "C", Status.FAIL, ""),
    ]
    assert worst_status(results) is Status.FAIL
    assert worst_status(results[:2]) is Status.WARN


_MINIMAL_CONFIG = json.dumps(
    {
        "clients": {
            "acme": {
                "display_name": "Acme BV",
                "gs1": {
                    "account_number_test": "8720796420906",
                    "client_id_env_test": "GS1_CID",
                    "client_secret_env_test": "GS1_SEC",
                    "environment": "test",
                },
                "export": {"path": "input/acme.xlsx"},
                "wordpress": {
                    "site_url": "https://wp.test",
                    "username": "bot",
                    "app_password_env": "WP_PASS",
                    "post_type": "product",
                    "default_language": "nl",
                    "languages": ["nl"],
                },
            }
        }
    }
)
