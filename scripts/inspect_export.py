"""Inspect a GDSN export and suggest a ``gdsn_map`` (IMPLEMENTATION_SPEC §8.5).

Usage:
    python -m scripts.inspect_export EXCEL_PATH

Prints, for each worksheet, the resolved GDSN attributes (label, attribute id,
whether per-language, sample values), then a ready-to-paste ``export`` block with a
suggested ``gdsn_map`` for the recognised product-page attributes. An onboarding aid:
the operator tunes the suggestion into ``clients.yml``.
"""

from __future__ import annotations

import sys
from typing import NamedTuple

import yaml

from lib.gdsn import GdsnColumn, GdsnSheet, read_workbook
from lib.records import _coerce_cell

_KEY_SEGMENTS = frozenset(
    {
        "Gtin",
        "TargetMarketCountryCode",
        "InformationProviderOfTradeItem",
        "TradeItemUnitDescriptorCode",
    }
)

#: Recognised GDSN attributes → (ProductRecord field, GdsnSource extra kwargs).
_KNOWN_ATTRIBUTES: dict[str, tuple[str, dict[str, bool]]] = {
    "3297": ("product_name", {"localised": True}),
    "3318": ("description_long", {"localised": True}),
    "1083": ("description_short", {"localised": True}),
    "3336": ("brand", {}),
    "3510": ("net_content", {"with_unit": True}),
    "GpcCategoryCode": ("gpc_brick_code", {}),
    "2485": ("image_url", {"primary_file": True}),
}

_SAMPLE_LIMIT = 3
_SAMPLE_ROWS = 25
_SAMPLE_WIDTH = 60


class _Attribute(NamedTuple):
    key: str
    leaf: str
    label: str
    localised: bool
    languages: list[str]
    samples: list[str]


def _summarise_sheet(sheet: GdsnSheet) -> list[_Attribute]:
    """Group a sheet's non-key columns into per-attribute summaries."""
    groups: dict[str, list[GdsnColumn]] = {}
    for column in sheet.columns:
        if column.path and column.path[0] in _KEY_SEGMENTS:
            continue
        key = column.attr_id or column.leaf_name
        if key:
            groups.setdefault(key, []).append(column)

    rows = list(sheet.rows_by_key.values())[:_SAMPLE_ROWS]
    attributes: list[_Attribute] = []
    for key, columns in groups.items():
        localised = any(c.leaf_name == "LanguageCode" for c in columns)
        label = next((c.label for c in columns if c.label), key) or key
        value_cols = [c for c in columns if c.leaf_name == "Value"] or [
            c for c in columns if c.leaf_name not in ("LanguageCode", "MeasurementUnitCode")
        ]
        leaf = value_cols[0].leaf_name if value_cols else ""
        lang_cols = [c for c in columns if c.leaf_name == "LanguageCode"]
        samples: list[str] = []
        languages: set[str] = set()
        for row in rows:
            for col in value_cols:
                value = _coerce_cell(row[col.index]) if col.index < len(row) else None
                if value and value not in samples:
                    samples.append(value[:_SAMPLE_WIDTH])
            for col in lang_cols:
                lang = _coerce_cell(row[col.index]) if col.index < len(row) else None
                if lang:
                    languages.add(lang)
            if len(samples) >= _SAMPLE_LIMIT:
                break
        attributes.append(
            _Attribute(key, leaf, label, localised, sorted(languages), samples[:_SAMPLE_LIMIT])
        )
    return attributes


def _suggest_map(sheets: dict[str, GdsnSheet]) -> dict[str, object]:
    """Build a suggested ``export`` block from recognised attributes."""
    gdsn_map: dict[str, dict[str, object]] = {}
    markets: set[str] = set()
    for name, sheet in sheets.items():
        markets.update(market for (_gtin, market) in sheet.rows_by_key)
        for attr in _summarise_sheet(sheet):
            known = _KNOWN_ATTRIBUTES.get(attr.key) or _KNOWN_ATTRIBUTES.get(attr.leaf)
            if known is None:
                continue
            field, kwargs = known
            if field in gdsn_map:
                continue
            gdsn_map[field] = {"sheet": name, "attribute": attr.key, **kwargs}
    return {
        "format": "gdsn",
        "market_language": {market: "??" for market in sorted(markets)},
        "gdsn_map": gdsn_map,
    }


def _print_report(sheets: dict[str, GdsnSheet]) -> None:
    for name, sheet in sheets.items():
        attributes = _summarise_sheet(sheet)
        if not attributes:
            continue
        print(f"\n### {name}  ({len(sheet.rows_by_key)} rows)")
        for attr in attributes:
            flag = " [localised]" if attr.localised else ""
            langs = f" langs={attr.languages}" if attr.languages else ""
            sample = " | ".join(attr.samples)
            print(f"  {attr.label}  (attr={attr.key}){flag}{langs}")
            if sample:
                print(f"      e.g. {sample}")


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m scripts.inspect_export EXCEL_PATH", file=sys.stderr)
        return 2
    try:
        sheets = read_workbook(args[0])
    except (FileNotFoundError, OSError) as exc:
        print(f"cannot read export: {exc}", file=sys.stderr)
        return 1

    _print_report(sheets)
    print("\n# Suggested clients.yml export block (map market codes to languages, then tune):")
    print(yaml.safe_dump({"export": _suggest_map(sheets)}, sort_keys=False, allow_unicode=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
