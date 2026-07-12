"""Multilingual plugin adapters for the WordPress client (§4.5).

WordPress sites localise content through one of several plugins. The orchestrator
publishes one page per (GTIN, language) and then links those pages as translations of
one another; *how* that link is expressed is plugin-specific. This module isolates that
difference behind :class:`MultilingualAdapter` so ``lib.wp_client.WordPressClient`` stays
plugin-agnostic.

v0.1.0 supports Polylang (Noviplast's plugin) and a no-op for single-language sites.
WPML is a v0.2 stub that raises :class:`NotImplementedError` (risk R3, §4.5).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from lib.wp_client import WordPressClient

#: Polylang REST endpoint that groups per-language posts into one translation set.
_PLL_LINK_PATH: Final = "/wp-json/pll/v1/translations"


class MultilingualAdapter(ABC):
    """Strategy for linking per-language pages as translations (§4.5)."""

    @abstractmethod
    def link_translations(self, wp: WordPressClient, translations: dict[str, int]) -> None:
        """Link the given ``{language: page_id}`` pages as translations of each other.

        Args:
            wp: The WordPress client whose authenticated transport to reuse.
            translations: Mapping of language code to the page id in that language.
        """


class NoOpAdapter(MultilingualAdapter):
    """Adapter for sites with no multilingual plugin — linking is a no-op (§4.5)."""

    def link_translations(self, wp: WordPressClient, translations: dict[str, int]) -> None:
        """Do nothing: single-language sites have no translation groups."""
        return


class PolylangAdapter(MultilingualAdapter):
    """Adapter for Polylang, using its ``/wp-json/pll/v1/`` REST endpoints (§4.5)."""

    def link_translations(self, wp: WordPressClient, translations: dict[str, int]) -> None:
        """Register ``translations`` as one Polylang translation group.

        Args:
            wp: The WordPress client whose authenticated transport to reuse.
            translations: Mapping of language code to the page id in that language.
        """
        if len(translations) < 2:  # noqa: PLR2004 — nothing to link with under two languages
            return
        wp._request(  # noqa: SLF001 — adapters intentionally reuse the client's transport
            "POST",
            _PLL_LINK_PATH,
            json_body={"translations": translations},
            label=f"pll link {sorted(translations)}",
        )


class WPMLAdapter(MultilingualAdapter):
    """Adapter for WPML — a v0.2 stub (§4.5, risk R3)."""

    def link_translations(self, wp: WordPressClient, translations: dict[str, int]) -> None:
        """Not implemented: WPML translation linking lands in v0.2.

        Raises:
            NotImplementedError: Always — WPML support is deferred to v0.2.
        """
        raise NotImplementedError("WPML translation linking is not implemented (v0.2)")


def make_adapter(plugin: Literal["polylang", "wpml", "none"]) -> MultilingualAdapter:
    """Return the :class:`MultilingualAdapter` for a detected plugin (§4.5).

    Args:
        plugin: The multilingual plugin identifier.

    Returns:
        The matching adapter instance.
    """
    if plugin == "polylang":
        return PolylangAdapter()
    if plugin == "wpml":
        return WPMLAdapter()
    return NoOpAdapter()
