"""A report goes to the operator's Downloads folder and opens in their own app (2026-10-09)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ui import runner


def test_a_report_is_copied_to_downloads_without_overwriting_and_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[Path] = []
    monkeypatch.setattr(runner, "open_in_default_app", opened.append)
    report = tmp_path / "reports" / "issues.pdf"
    report.parent.mkdir()
    report.write_bytes(b"%PDF one")
    downloads = tmp_path / "Downloads"
    downloads.mkdir()

    first = runner.hand_over(report, downloads=downloads)
    second = runner.hand_over(report, downloads=downloads)

    assert first == downloads / "issues.pdf"
    assert second == downloads / "issues-1.pdf", "an earlier download is never overwritten"
    assert first.read_bytes() == b"%PDF one"
    assert opened == [first, second]
