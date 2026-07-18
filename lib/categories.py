"""GPC brick → client category resolution (Phase 7.5).

The GDSN feed carries a product's GPC brick (``ProductRecord.gpc_brick_code``) but not the
client's marketing category. This module turns one into the other using the reviewed,
signed-off ``categories`` config (:class:`lib.config.CategoryConfig`): a per-GTIN override
wins, else the ``brick_category_map`` lookup, else nothing — an unmapped brick is *reported*
and leaves the category unset. The tool never guesses (a brick can span categories, so a
guess is a wrong page filing, not a near-miss).

Format-agnostic on purpose: it consumes :class:`~lib.records.ProductRecord`, not the GDSN
export, so it works for any export path. See ``docs/clients/noviplast-page-adapter.md`` §5.7.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from lib.config import CategoryConfig
from lib.records import ProductRecord, SourceIssue

_log = logging.getLogger(__name__)

#: How the ``category`` field is named where the operator finds it — the GPC brick, carried
#: on the ``BrickGPCCommercialData`` sheet as ``GpcCategoryCode``.
_CATEGORY_SOURCE: str = "GpcCategoryCode (GPC brick)"


@dataclass(frozen=True)
class CategoryResolution:
    """The outcome of resolving one product's category.

    Exactly one side is populated: a resolved ``term`` (``issue`` is ``None``), or ``term``
    ``None`` with an ``issue`` describing why it could not be resolved.
    """

    term: str | None
    issue: SourceIssue | None


def _normalise_overrides(overrides: dict[str, str]) -> dict[str, str]:
    """Key overrides by GTIN-14 so a 13-digit authored barcode matches a 14-digit product."""
    return {gtin.zfill(14): term for gtin, term in overrides.items()}


def _issue(product: ProductRecord, kind: str, detail: str) -> SourceIssue:
    """Build a ``category`` :class:`SourceIssue` for an unresolved product."""
    return SourceIssue(
        gtin=product.gtin,
        field="category",
        source=_CATEGORY_SOURCE,
        issue=kind,
        value=product.gpc_brick_code or "",
        detail=detail,
    )


def resolve_category(
    product: ProductRecord,
    *,
    brick_category_map: dict[str, str],
    overrides: dict[str, str],
    allowed_terms: frozenset[str],
) -> CategoryResolution:
    """Resolve a product's category term. Precedence: per-GTIN override > brick map > none.

    Args:
        product: The product to categorise.
        brick_category_map: GPC brick code → category term.
        overrides: GTIN → category term, winning over the brick map (matched on GTIN-14).
        allowed_terms: The closed set of permitted terms; a term outside it is treated as
            unresolved (defensive — the config validator already guarantees membership).

    Returns:
        A :class:`CategoryResolution`. An unmapped brick, an out-of-set term, or a product
        with no GPC brick yields ``term=None`` and a :class:`SourceIssue`. Never guesses.
    """
    override = _normalise_overrides(overrides).get(product.gtin14)
    if override is not None:
        if override in allowed_terms:
            return CategoryResolution(term=override, issue=None)
        return CategoryResolution(
            term=None,
            issue=_issue(
                product,
                "category_unmapped",
                f"per-GTIN override {override!r} is not an allowed category term",
            ),
        )

    brick = product.gpc_brick_code
    if brick is None:
        return CategoryResolution(
            term=None,
            issue=_issue(
                product, "category_brick_missing", "no GPC brick to derive a category from"
            ),
        )

    term = brick_category_map.get(brick)
    if term in allowed_terms:
        return CategoryResolution(term=term, issue=None)
    return CategoryResolution(
        term=None,
        issue=_issue(
            product,
            "category_unmapped",
            f"GPC brick {brick} maps to no category term — "
            "add it to brick_category_map or add a per-GTIN override",
        ),
    )


def distinct_bricks(products: list[ProductRecord]) -> dict[str, list[str]]:
    """Group the GTIN-14s in ``products`` by their GPC brick.

    Products without a brick are omitted (they have no brick to map); they surface instead
    as ``category_brick_missing`` findings from :func:`resolve_category`.
    """
    by_brick: dict[str, list[str]] = {}
    for product in products:
        if product.gpc_brick_code is not None:
            by_brick.setdefault(product.gpc_brick_code, []).append(product.gtin14)
    return by_brick


@dataclass(frozen=True)
class CoverageReport:
    """Which of the export's GPC bricks resolve to a term, and which do not (DoD #2).

    A brick counts as covered when it is in ``brick_category_map`` (``mapped``) or when every
    product carrying it has a per-GTIN override (``override_only``). ``unmapped`` maps each
    still-uncovered brick to the GTIN-14s under it that resolve to nothing.
    """

    total_bricks: int
    mapped: list[str]
    override_only: list[str]
    unmapped: dict[str, list[str]]

    @property
    def is_complete(self) -> bool:
        """Whether every brick in the export resolves to a term."""
        return not self.unmapped


def coverage_report(products: list[ProductRecord], categories: CategoryConfig) -> CoverageReport:
    """Verify every GPC brick in ``products`` resolves to a term under ``categories``.

    Iterates products (not just bricks) so a brick covered entirely by per-GTIN overrides
    counts as covered. This is the machine-checkable form of "every brick maps to a category".
    """
    allowed = frozenset(categories.terms)
    by_brick: dict[str, list[ProductRecord]] = {}
    for product in products:
        if product.gpc_brick_code is not None:
            by_brick.setdefault(product.gpc_brick_code, []).append(product)

    mapped: list[str] = []
    override_only: list[str] = []
    unmapped: dict[str, list[str]] = {}
    for brick, brick_products in by_brick.items():
        if brick in categories.brick_category_map:
            mapped.append(brick)
            continue
        unresolved = [
            product.gtin14
            for product in brick_products
            if resolve_category(
                product,
                brick_category_map=categories.brick_category_map,
                overrides=categories.overrides,
                allowed_terms=allowed,
            ).term
            is None
        ]
        if unresolved:
            unmapped[brick] = unresolved
        else:
            override_only.append(brick)

    return CoverageReport(
        total_bricks=len(by_brick),
        mapped=sorted(mapped),
        override_only=sorted(override_only),
        unmapped=unmapped,
    )
