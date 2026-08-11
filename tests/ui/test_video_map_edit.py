"""Editing ``mapping.yml`` from a screen without losing what is written in it.

This file is a record of client sign-off, and two things in it carry information nothing else
does: the confirmed GTINs, and the trailing ``# hint:`` comments recording which fuzzy match each
one came from and which were rejected. Both would be gone after one round-trip through
``yaml.safe_dump``, which is why :mod:`ui.video_map_edit` rewrites the text a row at a time.

So these tests are mostly about what *did not* change. The pattern — and several of the cases —
are lifted from :mod:`tests.ui.test_config_edit`, which asks the same question of ``clients.yml``.
No NiceGUI is involved: the module is deliberately free of it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.errors import VideoMapError
from lib.media_video import load_video_map
from ui import video_map_edit

_MAPPING = """\
# Working video mapping. gtin "" = UNSET (fill it), "skip" = no product for this video.
# CONFIRMED = client-confirmed.
nl:
  - {file: "4-in-1 Lamp.mpg", gtin: ""}   # hint: 08713195000893 'lamp' (0.53)
  - {file: "Bulbman.mpg", gtin: "8713195007434"}   # verify 'stickylamp' (0.93)
  - {file: "Trailer.mpg", gtin: "skip"}
fr:
  - {file: "Lampe.mpg", gtin: ""}   # hint: 08713195000893 'lampe' (0.61)
"""


def _rows(text: str) -> dict[tuple[str, str], str]:
    return {(r.language, r.file): r.gtin for r in video_map_edit.parse(text)}


# --- Reading ------------------------------------------------------------------


def test_every_row_is_found_with_its_language_and_its_comment() -> None:
    rows = video_map_edit.parse(_MAPPING)

    assert [(r.language, r.file) for r in rows] == [
        ("nl", "4-in-1 Lamp.mpg"),
        ("nl", "Bulbman.mpg"),
        ("nl", "Trailer.mpg"),
        ("fr", "Lampe.mpg"),
    ]
    assert rows[0].note.startswith("# hint:")
    assert rows[0].state == "unset"
    assert rows[1].state == "confirmed"
    assert rows[2].state == "skip"


def test_a_reformatted_row_is_refused_rather_than_guessed_at() -> None:
    """Every row this tool drafts is one line. A hand-reformatted one is not editable in place.

    Refusing says so and leaves the terminal working. Guessing where a multi-line row ends would
    corrupt a file that represents client sign-off.
    """
    text = "nl:\n  - file: A.mpg\n    gtin: ''\n"

    with pytest.raises(VideoMapError) as caught:
        video_map_edit.parse(text)

    assert "text editor" in str(caught.value)


# --- Writing one row ----------------------------------------------------------


def test_confirming_a_gtin_changes_that_row_and_nothing_else() -> None:
    edited = video_map_edit.set_gtin(_MAPPING, "nl", "4-in-1 Lamp.mpg", "08713195000893")

    before, after = _MAPPING.splitlines(), edited.splitlines()
    assert len(before) == len(after)
    assert [n for n, (a, b) in enumerate(zip(before, after, strict=True)) if a != b] == [3]
    assert _rows(edited)[("nl", "Bulbman.mpg")] == "8713195007434"  # sign-off untouched


def test_the_hint_comment_survives_the_gtin_it_explains() -> None:
    """The comment is the evidence for the decision being recorded. Losing it loses the why."""
    edited = video_map_edit.set_gtin(_MAPPING, "nl", "4-in-1 Lamp.mpg", "08713195000893")

    row = next(r for r in video_map_edit.parse(edited) if r.file == "4-in-1 Lamp.mpg")
    assert row.note == "# hint: 08713195000893 'lamp' (0.53)"


def test_a_longer_value_pushes_its_comment_along_rather_than_overwriting_it() -> None:
    edited = video_map_edit.set_gtin(_MAPPING, "nl", "4-in-1 Lamp.mpg", "08713195000893")

    line = edited.splitlines()[3]
    assert line.index("#") > line.index("08713195000893")
    assert "  #" in line, "at least two spaces between the value and its comment"


def test_a_gtin_is_always_quoted(tmp_path: Path) -> None:
    """Unquoted, YAML reads it as an integer — which fails validation, and drops a leading zero."""
    edited = video_map_edit.set_gtin(_MAPPING, "nl", "4-in-1 Lamp.mpg", "08713195000893")

    assert 'gtin: "08713195000893"' in edited
    path = tmp_path / "mapping.yml"
    path.write_text(edited, encoding="utf-8")
    assert load_video_map(path).resolve("08713195000893", "nl") == "4-in-1 Lamp.mpg"


def test_skip_and_clear_are_both_writable() -> None:
    skipped = video_map_edit.set_gtin(_MAPPING, "fr", "Lampe.mpg", video_map_edit.SKIP)
    cleared = video_map_edit.set_gtin(_MAPPING, "nl", "Bulbman.mpg", "")

    assert _rows(skipped)[("fr", "Lampe.mpg")] == "skip"
    assert _rows(cleared)[("nl", "Bulbman.mpg")] == ""


def test_the_same_filename_in_two_languages_is_two_rows() -> None:
    """``DrainSticks.mpeg`` can exist in both folders; editing one must not touch the other."""
    text = 'nl:\n  - {file: "A.mpg", gtin: ""}\nfr:\n  - {file: "A.mpg", gtin: ""}\n'

    edited = video_map_edit.set_gtin(text, "fr", "A.mpg", "08713195000001")

    assert _rows(edited) == {("nl", "A.mpg"): "", ("fr", "A.mpg"): "08713195000001"}


def test_an_unknown_row_is_an_error_not_a_silent_no_op() -> None:
    with pytest.raises(VideoMapError):
        video_map_edit.set_gtin(_MAPPING, "nl", "NotThere.mpg", "08713195000001")


def test_a_batch_of_edits_is_applied_in_one_pass() -> None:
    edited = video_map_edit.apply_edits(
        _MAPPING,
        {("nl", "4-in-1 Lamp.mpg"): "08713195000893", ("fr", "Lampe.mpg"): "08713195000893"},
    )

    rows = _rows(edited)
    assert rows[("nl", "4-in-1 Lamp.mpg")] == "08713195000893"
    assert rows[("fr", "Lampe.mpg")] == "08713195000893"


# --- Adding rows for new files ------------------------------------------------


def test_files_on_disk_with_no_row_are_found_per_language() -> None:
    missing = video_map_edit.files_missing_from_map(
        _MAPPING, {"nl": ["4-in-1 Lamp.mpg", "New.mpg"], "fr": ["Lampe.mpg"]}
    )

    assert missing == {"nl": ["New.mpg"]}


def test_appending_adds_unset_rows_at_the_end_of_their_language() -> None:
    edited = video_map_edit.append_rows(_MAPPING, "nl", ["New.mpg"])

    rows = video_map_edit.parse(edited)
    added = next(r for r in rows if r.file == "New.mpg")
    assert added.language == "nl"
    assert added.state == "unset"
    assert [r.file for r in rows if r.language == "fr"] == ["Lampe.mpg"]


def test_appending_does_not_steal_the_next_language_header() -> None:
    """The new row must land before ``fr:``, not after it, or it changes language silently."""
    edited = video_map_edit.append_rows(_MAPPING, "nl", ["New.mpg"])

    lines = edited.splitlines()
    assert lines.index('  - {file: "New.mpg", gtin: ""}') < lines.index("fr:")


def test_appending_a_file_already_in_the_map_is_a_no_op() -> None:
    assert video_map_edit.append_rows(_MAPPING, "nl", ["Bulbman.mpg"]) == _MAPPING


def test_a_language_with_no_block_yet_gets_one() -> None:
    edited = video_map_edit.append_rows(_MAPPING, "de", ["Neu.mpg"])

    assert ("de", "Neu.mpg") in _rows(edited)


# --- Writing the file ---------------------------------------------------------


def test_what_the_screen_wrote_is_what_the_pipeline_then_loads(tmp_path: Path) -> None:
    """The round-trip that matters: the run must read back exactly what was confirmed here."""
    path = tmp_path / "mapping.yml"
    path.write_text(_MAPPING, encoding="utf-8")

    video_map_edit.write_validated(
        path, video_map_edit.set_gtin(_MAPPING, "fr", "Lampe.mpg", "8713195007434")
    )

    vmap = load_video_map(path)
    assert vmap.resolve("8713195007434", "fr") == "Lampe.mpg"
    assert vmap.resolve("8713195007434", "nl") == "Bulbman.mpg"


def test_the_previous_version_is_kept(tmp_path: Path) -> None:
    path = tmp_path / "mapping.yml"
    path.write_text(_MAPPING, encoding="utf-8")

    backup = video_map_edit.write_validated(
        path, video_map_edit.set_gtin(_MAPPING, "fr", "Lampe.mpg", "8713195007434")
    )

    assert backup.read_text(encoding="utf-8") == _MAPPING


def test_a_candidate_that_will_not_load_is_refused_and_the_file_is_left_alone(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mapping.yml"
    path.write_text(_MAPPING, encoding="utf-8")

    with pytest.raises(VideoMapError):
        video_map_edit.write_validated(path, "nl: 42\n")

    assert path.read_text(encoding="utf-8") == _MAPPING
    assert not list(tmp_path.glob("*.candidate"))


def test_a_candidate_that_lost_a_row_is_refused(tmp_path: Path) -> None:
    """Nothing on the screen deletes a row, so a row that has gone is a fault in the tool.

    A confirmed row is somebody's sign-off on which video shows which product. Writing a file
    that quietly has fewer of them than the one it replaces is the failure this refuses.
    """
    path = tmp_path / "mapping.yml"
    path.write_text(_MAPPING, encoding="utf-8")
    without_bulbman = (
        "\n".join(line for line in _MAPPING.splitlines() if "Bulbman" not in line) + "\n"
    )

    with pytest.raises(VideoMapError) as caught:
        video_map_edit.write_validated(path, without_bulbman)

    assert "nl/Bulbman.mpg" in str(caught.value)
    assert path.read_text(encoding="utf-8") == _MAPPING
