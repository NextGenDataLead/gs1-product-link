"""GS1's 21011 refusal is reported in plain words — and only after it was actually tried.

The first real links run (2026-10-09) had six barcodes refused with ``No valid contract found``.
They are on the client's GS1 account all the same (operator, 2026-10-09: "try to perform the
actions and then potentially run into an error instead of assuming that it cannot be done"), so
nothing predicts the refusal before a run; the report only says it clearly afterwards.
"""

from __future__ import annotations

from lib.issue_report import NO_CONTRACT, RUN_ERROR, run_issues
from lib.scope_report import IN_SCOPE, ScopeRow, UnitResult

GTIN = "04895069002951"


def _row(error: str) -> ScopeRow:
    return ScopeRow([GTIN], GTIN, IN_SCOPE, {"nl": UnitResult("error", "", error)})


def test_a_no_contract_refusal_is_named_without_the_raw_error() -> None:
    error = (
        "POST /digitallinkv2/v2/digitallink GS1APIError('GS1 API error 400 … "
        '[{"code": "21011", "message": "No valid contract found."}]\')'
    )

    _, issues = run_issues([_row(error)], {GTIN: "voegstrijker"}, writes_pages=False)

    assert [i.category for i in issues] == [NO_CONTRACT]
    assert "GS1APIError" not in issues[0].reason, "the client reads this; no raw error"
    assert "21011" in issues[0].reason


def test_any_other_error_stays_a_publishing_failure() -> None:
    _, issues = run_issues([_row("WP 500 on POST /pages")], {}, writes_pages=False)

    assert [i.category for i in issues] == [RUN_ERROR]
