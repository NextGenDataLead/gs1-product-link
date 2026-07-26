"""Tests for scripts/report_quality.py — the CLI that loads the issue files and writes the report.

Drives ``main`` in a temp working directory: writes sample ``output/{client}/data/*_issues.json``
(and products.json), then asserts the consolidated markdown report is written and the exit code.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.records import LocalisedText, ProductRecord
from scripts import report_quality


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _seed(tmp_path: Path) -> Path:
    data = tmp_path / "output" / "noviplast" / "data"
    _write(
        data / "generated_issues.json",
        [
            {
                "gtin": "08713195003276",
                "field": "description_short.nl",
                "source": "attr 1083",
                "issue": "missing_generation_input",
                "value": "",
                "detail": "d",
            },
            {
                "gtin": "08713195007915",
                "field": "generated_description.nl",
                "source": "inferred",
                "issue": "generation_inference",
                "value": "magnet claim",
                "detail": "d",
            },
        ],
    )
    _write(data / "source_issues.json", [])
    _write(data / "video_map_issues.json", [])
    _write(data / "category_issues.json", [])
    prod = ProductRecord(
        gtin="08713195007915",
        brand="Noviplast",
        product_name=LocalisedText(values={"nl": "LED-lamp"}),
    )
    _write(data / "products.json", [prod.model_dump(mode="json")])
    return data


def test_writes_report_and_exit_0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed(tmp_path)

    code = report_quality.main(["noviplast"])

    assert code == 0
    report = tmp_path / "output" / "noviplast" / "data-quality-report.md"
    assert report.is_file()
    text = report.read_text(encoding="utf-8")
    assert "Data quality report" in text
    assert "08713195003276" in text  # held GTIN
    assert "magnet claim" in text  # inference surfaced


def test_missing_data_dir_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # no output/ at all

    assert report_quality.main(["noviplast"]) == 2


def test_absent_issue_files_treated_as_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    # data dir exists but only products.json present; issue files absent
    data = tmp_path / "output" / "noviplast" / "data"
    data.mkdir(parents=True)
    (data / "products.json").write_text("[]", encoding="utf-8")

    assert report_quality.main(["noviplast"]) == 0
    assert (tmp_path / "output" / "noviplast" / "data-quality-report.md").is_file()
