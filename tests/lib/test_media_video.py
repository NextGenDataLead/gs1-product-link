"""Unit tests for the video name→GTIN mapping and transcode step (Phase 9.5 media)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib.errors import VideoMapError
from lib.media_video import (
    VideoMap,
    canon_gtin,
    check_video_map,
    fully_mapped_gtins,
    list_video_files,
    load_video_map,
    normalize_video_name,
    prepare_video,
    rank_candidates,
    summarize_video_map,
)
from lib.records import LocalisedText, ProductRecord


def _product(
    gtin: str, *, nl: str = "", fr: str = "", extras: dict[str, str] | None = None
) -> ProductRecord:
    values = {k: v for k, v in {"nl": nl, "fr": fr}.items() if v}
    return ProductRecord(
        gtin=gtin,
        brand="Noviplast",
        product_name=LocalisedText(values=values or {"nl": gtin}),
        extras=extras or {},
    )


# --- normalize_video_name ----------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("BeanieBrite_NL_SmallV2.mpg", "beanie brite"),
        ("WaspTrap_NL_Small.mpg", "wasp trap"),
        ("DrainSticks_NL.mpeg", "drain sticks"),
        ("PaperKnife_NL_small.mpeg", "paper knife"),
        ("Crazy Cat_FR.mpg", "crazy cat"),
        ("BambooPillow FR.mpg", "bamboo pillow"),
        ("LegPillow_FR_NEW.mpeg", "leg pillow"),
        ("4-in-1 Lamp.mpg", "4 in 1 lamp"),
        ("Garden Clipper.mpg", "garden clipper"),
    ],
)
def test_normalize_strips_lang_size_version_and_splits_camelcase(
    filename: str, expected: str
) -> None:
    assert normalize_video_name(filename) == expected


# --- list_video_files --------------------------------------------------------


def test_list_video_files_skips_system_dirs_and_dotfiles(tmp_path: Path) -> None:
    (tmp_path / "A_NL.mpeg").write_bytes(b"x")
    (tmp_path / "B.mpg").write_bytes(b"x")
    (tmp_path / ".DS_Store").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")
    svi = tmp_path / "System Volume Information"
    svi.mkdir()
    (svi / "junk.mpg").write_bytes(b"x")

    files = list_video_files(tmp_path)
    names = sorted(p.name for p in files)
    assert names == ["A_NL.mpeg", "B.mpg"]


# --- rank_candidates (hints only) --------------------------------------------


def test_rank_candidates_scores_partial_hit_from_extras() -> None:
    products = [
        _product("08713195007434", nl="handige lamp", extras={"logistics_name": "Bulb man"}),
        _product("08713195000001", nl="iets heel anders"),
    ]
    ranked = rank_candidates("bulbman", products, top_n=3)
    assert ranked[0].gtin == "08713195007434"
    assert ranked[0].field == "extras.logistics_name"
    assert ranked[0].score > ranked[1].score


def test_rank_candidates_pure_miss_scores_low() -> None:
    products = [_product("08713195000001", nl="reinigingssticks voor je afvoer")]
    ranked = rank_candidates("wasp trap", products, top_n=3)
    assert ranked[0].score < 0.5


# --- VideoMap.resolve --------------------------------------------------------


def _map(data: dict[str, list[dict[str, str]]]) -> VideoMap:
    return VideoMap.model_validate({"by_language": data})


def test_canon_gtin_pads_to_14_and_strips_nondigits() -> None:
    assert canon_gtin("8713195007717") == "08713195007717"
    assert canon_gtin("08713195007717") == "08713195007717"
    assert canon_gtin(" 8713195007717 ") == "08713195007717"


def test_resolve_matches_across_gtin_digit_forms() -> None:
    # mapping keyed 13-digit; pipeline queries 14-digit — must still match (the live bug).
    vmap = _map({"nl": [{"file": "DrainSticks_NL.mpeg", "gtin": "8713195001234"}]})
    assert vmap.resolve("08713195001234", "nl") == "DrainSticks_NL.mpeg"
    assert vmap.resolve("8713195001234", "nl") == "DrainSticks_NL.mpeg"


def test_fully_mapped_gtins_requires_all_languages() -> None:
    vmap = _map(
        {
            "nl": [
                {"file": "a_nl.mp4", "gtin": "8713195000001"},
                {"file": "b_nl.mp4", "gtin": "8713195000002"},  # nl-only
                {"file": "c_nl.mp4", "gtin": "skip"},
            ],
            "fr": [
                {"file": "a_fr.mp4", "gtin": "8713195000001"},
                {"file": "d_fr.mp4", "gtin": ""},  # blank
            ],
        }
    )
    both = fully_mapped_gtins(vmap, ["nl", "fr"])
    assert both == frozenset({"08713195000001"})  # canonical 14-digit; only the both-mapped one


def test_resolve_returns_confirmed_filename() -> None:
    vmap = _map({"nl": [{"file": "DrainSticks_NL.mpeg", "gtin": "08713195001234"}]})
    assert vmap.resolve("08713195001234", "nl") == "DrainSticks_NL.mpeg"


def test_resolve_blank_gtin_returns_none() -> None:
    vmap = _map({"nl": [{"file": "WaspTrap_NL.mpg", "gtin": ""}]})
    assert vmap.resolve("08713195001234", "nl") is None


def test_resolve_unknown_pair_returns_none() -> None:
    vmap = _map({"nl": [{"file": "A.mpg", "gtin": "08713195001234"}]})
    assert vmap.resolve("08713195009999", "fr") is None


def test_resolve_ambiguous_gtin_returns_none() -> None:
    vmap = _map(
        {
            "nl": [
                {"file": "A.mpg", "gtin": "08713195001234"},
                {"file": "B.mpg", "gtin": "08713195001234"},
            ]
        }
    )
    assert vmap.resolve("08713195001234", "nl") is None


def test_load_video_map_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "mapping.yml"
    path.write_text(
        "nl:\n"
        '  - {file: "DrainSticks_NL.mpeg", gtin: "08713195001234"}\n'
        "fr:\n"
        '  - {file: "DrainSticks_FR.mpeg", gtin: "08713195001234"}\n',
        encoding="utf-8",
    )
    vmap = load_video_map(path)
    assert vmap.resolve("08713195001234", "nl") == "DrainSticks_NL.mpeg"
    assert vmap.resolve("08713195001234", "fr") == "DrainSticks_FR.mpeg"


def test_a_missing_mapping_says_so_rather_than_raising_oserror(tmp_path: Path) -> None:
    """Every caller used to catch ``OSError`` for this. Now the loader owns the message."""
    with pytest.raises(VideoMapError) as caught:
        load_video_map(tmp_path / "nope.yml")
    assert "cannot read" in str(caught.value)
    assert "nope.yml" in str(caught.value)


def test_a_hand_edited_mapping_reports_where_the_syntax_error_is(tmp_path: Path) -> None:
    """The one failure that cannot happen unless a human edited the file used to be a traceback.

    ``yaml.YAMLError`` inherits from ``Exception`` alone, so it escaped every ``except (OSError,
    ValueError)`` in the codebase — and this file is one the design requires a human to edit and
    a client to sign off. A tab pasted by a text editor was enough to produce 25 lines of Python.
    """
    path = tmp_path / "mapping.yml"
    path.write_text(
        'nl:\n  - {file: "DrainSticks_NL.mpeg", gtin: ""}\n\tstray tab\n', encoding="utf-8"
    )

    with pytest.raises(VideoMapError) as caught:
        load_video_map(path)

    message = str(caught.value)
    assert "line 3" in message, f"the operator has to be told where to look: {message}"
    assert "column 1" in message
    assert "\n" not in message, "one line, so it survives a table cell or a log record"


def test_a_mapping_of_the_wrong_shape_says_what_was_expected(tmp_path: Path) -> None:
    path = tmp_path / "mapping.yml"
    path.write_text("nl: 42\n", encoding="utf-8")

    with pytest.raises(VideoMapError) as caught:
        load_video_map(path)

    message = str(caught.value)
    assert "not a video mapping" in message
    assert "by_language.nl" in message
    assert "\n" not in message


# --- summarize_video_map -----------------------------------------------------


def test_the_summary_counts_each_kind_of_gap_separately() -> None:
    vmap = _map(
        {
            "nl": [
                {"file": "unset.mpg", "gtin": ""},
                {"file": "gone.mpg", "gtin": "8713195000001"},
                {"file": "here.mpg", "gtin": "8713195000002"},
            ]
        }
    )

    summary = summarize_video_map(vmap, {"nl": ["here.mpg", "extra.mpg"]}, ["nl"])

    assert summary.files == 2
    assert summary.entries == 3
    assert summary.unconfirmed == 1  # unset.mpg
    assert summary.file_missing == 2  # unset.mpg and gone.mpg name files not on disk
    assert summary.missing_from_map == 1  # extra.mpg
    assert summary.ambiguous == 0
    assert summary.confirmed_gtins == 2
    assert summary.gaps == 4


def test_the_summary_distinguishes_an_empty_folder_from_an_unconfirmed_mapping() -> None:
    """The day-one operator machine: the mapping was handed over, the video library was not.

    Counting that as unconfirmed mappings sends someone to edit a file that is already correct —
    and produced the "284 of 0" line, whose numerator was a different quantity from its
    denominator.
    """
    vmap = _map({"nl": [{"file": "a.mpg", "gtin": "8713195000001"}]})

    absent = summarize_video_map(vmap, {"nl": []}, ["nl"])
    present = summarize_video_map(vmap, {"nl": ["a.mpg"]}, ["nl"])

    assert absent.no_files_found is True
    assert present.no_files_found is False
    assert present.gaps == 0


def test_an_empty_mapping_and_empty_folders_is_not_the_missing_files_case() -> None:
    """Nothing configured yet is not the same as a library that has not been copied."""
    assert summarize_video_map(_map({}), {}, ["nl"]).no_files_found is False


# --- check_video_map ---------------------------------------------------------


def test_check_reports_unconfirmed_entry() -> None:
    vmap = _map({"nl": [{"file": "A.mpg", "gtin": ""}]})
    issues = check_video_map(vmap, {"nl": ["A.mpg"]})
    kinds = {i.issue for i in issues}
    assert "video_unconfirmed" in kinds


def test_check_reports_file_on_disk_missing_from_map() -> None:
    vmap = _map({"nl": [{"file": "A.mpg", "gtin": "08713195001234"}]})
    issues = check_video_map(vmap, {"nl": ["A.mpg", "B.mpg"]})
    assert any(i.issue == "video_missing_from_map" and "B.mpg" in i.value for i in issues)


def test_check_reports_map_entry_without_file() -> None:
    vmap = _map({"nl": [{"file": "GONE.mpg", "gtin": "08713195001234"}]})
    issues = check_video_map(vmap, {"nl": []})
    assert any(i.issue == "video_file_missing" for i in issues)


def test_check_reports_ambiguous_gtin() -> None:
    vmap = _map(
        {
            "nl": [
                {"file": "A.mpg", "gtin": "08713195001234"},
                {"file": "B.mpg", "gtin": "08713195001234"},
            ]
        }
    )
    issues = check_video_map(vmap, {"nl": ["A.mpg", "B.mpg"]})
    assert any(i.issue == "video_ambiguous" for i in issues)


def test_check_skip_sentinel_is_not_a_gap() -> None:
    vmap = _map({"nl": [{"file": "A.mpg", "gtin": "skip"}]})
    issues = check_video_map(vmap, {"nl": ["A.mpg"]})
    assert issues == []


def test_check_all_confirmed_and_covered_is_clean() -> None:
    vmap = _map({"nl": [{"file": "A.mpg", "gtin": "08713195001234"}]})
    assert check_video_map(vmap, {"nl": ["A.mpg"]}) == []


# --- prepare_video -----------------------------------------------------------


def test_prepare_video_noop_returns_src(tmp_path: Path) -> None:
    src = tmp_path / "clip.mpeg"
    src.write_bytes(b"x")
    assert prepare_video(src, tmp_path / "out", transcode=False) == src


def test_prepare_video_transcode_invokes_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "DrainSticks_NL.mpeg"
    src.write_bytes(b"x")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"mp4")  # ffmpeg writes the output file (last arg)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = prepare_video(src, tmp_path / "out", transcode=True)
    assert out == tmp_path / "out" / "DrainSticks_NL.mp4"
    assert out is not None and out.exists()
    assert calls and "-c:v" in calls[0] and "libx264" in calls[0]
    assert "-map_metadata" in calls[0]


def test_prepare_video_transcode_short_circuits_when_dest_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "clip.mpeg"
    src.write_bytes(b"x")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "clip.mp4").write_bytes(b"already")

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("ffmpeg must not run when dest exists")

    monkeypatch.setattr(subprocess, "run", boom)
    assert prepare_video(src, out_dir, transcode=True) == out_dir / "clip.mp4"


def test_prepare_video_ffmpeg_failure_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "clip.mpeg"
    src.write_bytes(b"x")

    def fail(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(subprocess, "run", fail)
    assert prepare_video(src, tmp_path / "out", transcode=True) is None
