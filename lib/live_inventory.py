"""Everything this tool has put live, from the ledger — the opening of the "everything live" report.

Operator, 2026-10-07: a data-quality report for every product that went live through this tool, to
give it follow-up. The quality report is otherwise scoped to the batch in progress; this supplies
the other scope — and, because the ledger records what *this machine* wrote rather than what the
site holds, it says per page whether the site was asked and what it answered.

**Live means published and not retracted** in the ledger (``wp_status == "publish"``, ``retracted``
false), and then *confirmed by the site listing* when one was possible. A page the site does not
list is named as such and counted; a site that could not be asked leaves every page "not checked" —
never "live", because the ledger alone is the claim, not the check.

Pure: the ledger and the site's answer come in, markdown lines go out. ``scripts/report_quality``
does the reading.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from lib.report_markdown import cell, table

if TYPE_CHECKING:
    from collections.abc import Collection

    from lib.records import State


@dataclass(frozen=True)
class LivePage:
    """One published page, as the ledger records it."""

    language: str
    url: str
    published: str
    video_file: str | None
    gs1_link: bool


@dataclass(frozen=True)
class LiveProduct:
    """A product with at least one live page."""

    gtin: str
    title: str
    pages: tuple[LivePage, ...]


def live_products(state: State) -> list[LiveProduct]:
    """Every product with a published, unretracted page, ordered by barcode."""
    found = []
    for gtin, by_language in sorted(state.entries.items()):
        pages = tuple(
            LivePage(
                language=language,
                url=entry.wp_url,
                published=entry.last_run.date().isoformat(),
                video_file=entry.video_file,
                gs1_link=bool(entry.gs1_link_set_hash),
            )
            for language, entry in sorted(by_language.items())
            if entry.wp_status == "publish" and not entry.retracted
        )
        if pages:
            title = next((e.title for e in by_language.values() if e.title), "") or ""
            found.append(LiveProduct(gtin=gtin, title=title, pages=pages))
    return found


def inventory_lines(
    live: list[LiveProduct],
    languages: list[str],
    *,
    on_site: Collection[tuple[str, str]] | None,
    site_note: str,
) -> list[str]:
    """The "Live on the site" section: one row per product, one column per language.

    Args:
        live: From :func:`live_products`.
        languages: The client's configured languages — the column set.
        on_site: ``(gtin14, language)`` for every page the site lists, or ``None`` when the site
            could not be asked.
        site_note: Why it could not be asked, shown when ``on_site`` is ``None``.
    """
    pages = sum(len(product.pages) for product in live)
    rows = []
    missing = 0
    for product in live:
        by_language = {page.language: page for page in product.pages}
        cells = []
        for language in languages:
            page = by_language.get(language)
            if page is None:
                cells.append("—")
            elif on_site is None:
                cells.append("not checked")
            elif (product.gtin, language) not in on_site:
                cells.append("not on the site")
                missing += 1
            else:
                cells.append("live · " + ("video" if page.video_file else "no video"))
        published = max(page.published for page in product.pages)
        gs1 = "yes" if any(page.gs1_link for page in product.pages) else "no"
        rows.append([f"`{product.gtin}`", cell(product.title), published, *cells, gs1])

    if on_site is None:
        checked = f"The site could not be asked ({site_note}), so no page is confirmed live."
    elif missing:
        checked = (
            f"Checked against the site: {missing} page(s) the ledger calls live are not on the "
            "site — find out why before following up on their data."
        )
    else:
        checked = "Checked against the site: every page below is there."
    return [
        "## Live on the site",
        "",
        f"Every product this tool has published from this machine and not taken down: "
        f"{len(live)} product(s), {pages} page(s). {checked} *Published* is the date of the "
        "latest run that wrote the page; a value changed in MyGS1 after that date is not on the "
        "page until the product is published again.",
        "",
        *table(["GTIN", "Product", "Published", *languages, "GS1 link"], rows),
        "",
    ]
