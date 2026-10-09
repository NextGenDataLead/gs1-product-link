"""Barcodes of other brands: GS1 refuses their Digital Link (21011), so they are stopped first.

The first real links run (2026-10-09) sent 31 products and GS1 refused six — barcodes under other
brands' company prefixes, which the client's account has no contract for.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lib.config import GS1Config
from lib.eligibility import OTHER_BRAND, Eligibility, LinkIssue
from lib.issue_report import OTHER_BRAND_CATEGORY, run_issues, selection_issues
from lib.scope_report import IN_SCOPE, ScopeRow, UnitResult

OURS = "08713195001517"
THEIRS = "04895069002951"


def _gs1(prefixes: tuple[str, ...] = ()) -> GS1Config:
    return GS1Config(
        account_number_test="1",
        client_id_env_test="ID",
        client_secret_env_test="SECRET",
        company_prefixes=prefixes,
    )


def test_a_barcode_is_owned_when_it_is_under_a_company_prefix() -> None:
    gs1 = _gs1(("8713195",))

    assert gs1.owns(OURS)
    assert not gs1.owns(THEIRS)
    assert _gs1().owns(THEIRS), "no prefixes configured means nothing is checked"


def test_a_company_prefix_is_digits() -> None:
    with pytest.raises(ValidationError):
        _gs1(("87A3195",))


def test_the_report_names_another_brand_s_barcode_before_and_after_a_run() -> None:
    verdict = Eligibility(bad_link={THEIRS: LinkIssue(None, OTHER_BRAND)})
    before = selection_issues([(THEIRS, "voegstrijker")], {THEIRS}, verdict)
    error = (
        "POST /digitallinkv2/v2/digitallink GS1APIError('GS1 API error 400 … "
        '[{"code": "21011", "message": "No valid contract found."}]\')'
    )
    row = ScopeRow([THEIRS], THEIRS, IN_SCOPE, {"nl": UnitResult("error", "", error)})
    _, after = run_issues([row], {THEIRS: "voegstrijker"}, writes_pages=False)

    assert [i.category for i in before] == [OTHER_BRAND_CATEGORY]
    assert [i.category for i in after] == [OTHER_BRAND_CATEGORY]
    assert "GS1APIError" not in after[0].reason, "the client reads this; no raw error"
