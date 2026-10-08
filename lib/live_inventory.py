"""Every product this tool has put live, from the ledger.

Live means published and not retracted (``wp_status == "publish"``, ``retracted`` false). The ledger
records what *this machine* wrote, so it is the claim and the site is the check: the complete
data-quality report (:mod:`lib.complete_scope`) joins this with the site listing. Pure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
