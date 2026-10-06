"""Which products of a batch are eligible to publish, why the rest are not, and which lack a video.

The Data screen's funnel — *in product list → eligible → selected* — needs three answers about each
product the export carries: may it publish at all, and if not why, and if so is it missing a video
somewhere. Each already has exactly one producer, and this module only joins them:

* **not eligible, and why** is :func:`lib.holds.held_products` — the plan's own rules in the plan's
  own order (E23 missing data, E24 the video gate, E22 no image), with the E23 gaps generation
  cannot close. A screen that re-derived "eligible" would sooner or later offer a tick box on a
  product the plan then drops in silence;
* **the video words** are :func:`lib.video_status.waiting_on`, so the screen and report §1 say the
  same thing about the same product ("no confirmed video in fr", "two videos in nl").

A barcode on the list that the export does not carry is not a question for this module: it has no
record to ask about. The screen lists those itself, first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from lib.errors import VideoMapError
from lib.holds import held_products
from lib.preflight import load_video_status
from lib.records import SkipReason
from lib.video_status import CLASHING, ProductVideo, waiting_on

if TYPE_CHECKING:
    from lib.config import ClientConfig
    from lib.records import ProductRecord

#: What a product held for want of a source image is called on screen.
NO_IMAGE: Final = "no product image in the export"

#: What the screen says when the mapping will not load, so no product can be judged on video.
VIDEO_UNKNOWN: Final = "the video mapping could not be read"


@dataclass(frozen=True)
class Eligibility:
    """The batch's products, split the way the funnel needs them. Keyed by GTIN-14.

    Attributes:
        not_eligible: ``{gtin14: why}`` for every product a run will hold — the plan's reason in a
            few words. Everything else in the export may publish.
        missing_video: ``{gtin14: what it waits on}`` for the *eligible* products that publish with
            no video in at least one language (``media.publish_without_video``).
        problem: Set when the holds could not be decided at all; then nothing is called eligible.
    """

    not_eligible: dict[str, str] = field(default_factory=dict)
    missing_video: dict[str, str] = field(default_factory=dict)
    problem: str | None = None

    def is_eligible(self, gtin14: str) -> bool:
        """Whether a product the export carries may publish."""
        return self.problem is None and gtin14 not in self.not_eligible


def eligibility(cfg: ClientConfig, products: list[ProductRecord]) -> Eligibility:
    """Split ``products`` — those the batch's list names and the export carries — for the funnel."""
    try:
        holds = held_products(cfg, products)
    except VideoMapError as exc:
        # Fail closed: a run with an unreadable mapping and the rule on would hold everything.
        return Eligibility(problem=f"{VIDEO_UNKNOWN}: {exc}")
    status = load_video_status(cfg, products)
    words = (
        {p.gtin: _held_for(p, status.publish_without_video) for p in status.products}
        if status
        else {}
    )
    by_gtin = {product.gtin: product.gtin14 for product in products}
    # The video gate only holds anything when the rule is on; otherwise its words are not a reason.
    enforced = cfg.media is not None and cfg.media.restrict_to_mapped_gtins

    not_eligible: dict[str, str] = {}
    for gtin, hold in holds.items():
        gtin14 = by_gtin[gtin]
        video = words.get(gtin14, "") if enforced else ""
        if hold.reason is SkipReason.MISSING_MANDATORY_FIELD:
            reasons = ["missing data: " + ", ".join(gap.label for gap in hold.gaps), video]
        elif hold.reason is SkipReason.NO_CONFIRMED_VIDEO:
            reasons = [video or "no confirmed video"]
        else:
            reasons = [NO_IMAGE, video]
        # Every reason, not only the first rule that fires: the plan attributes a hold to one rule,
        # but the operator has to fix all of them before the product is eligible — and report §1b
        # lists a two-video product whatever else holds it, so the screen must too.
        not_eligible[gtin14] = "; ".join(reason for reason in reasons if reason)

    bare = status.without_video if status is not None else ()
    missing_video = {p.gtin: waiting_on(p) for p in bare if p.gtin not in not_eligible}
    return Eligibility(not_eligible, missing_video)


def _held_for(product: ProductVideo, publish_without_video: bool) -> str:
    """Why the video gate holds ``product``, naming only what actually holds it.

    Under ``media.publish_without_video`` a missing language holds nothing, so "no confirmed video
    in fr; two videos in nl" would send the reader after a video that would not unblock it.
    """
    if not publish_without_video:
        return waiting_on(product)
    clashing = product.languages_in(CLASHING)
    return "; ".join(
        f"two videos in {language} — the client must keep one" for language in clashing
    )
