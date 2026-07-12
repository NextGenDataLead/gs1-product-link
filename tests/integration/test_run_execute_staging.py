"""Live end-to-end run_execute DoD check (IMPLEMENTATION_SPEC §12 Phase 6, §6.5).

This is the executable form of the Phase 6 exit gate — "``run_execute.py`` completes for
one GTIN end-to-end against staging" — plus the live §6.5 idempotency contract. It drives
``scripts.run_execute.main`` against a **real WordPress staging** site and the **GS1
production** environment (the GS1 sandbox account has no Digital Link contract, so
production is the only environment that resolves — a project decision). It is marked
``staging`` and skipped unless both targets are configured, so CI stays green on mocks.

Run once WordPress staging is provisioned and GS1 production credentials are available::

    WP_STAGING_URL=https://staging.noviplast.nl \\
    WP_STAGING_USER=automation-bot \\
    NOVIPLAST_WP_APP_PASS='xxxx xxxx xxxx xxxx' \\
    GS1_PROD_ACCOUNT=87207XXXXXXXX \\
    GS1_PROD_CLIENT_ID='...' GS1_PROD_CLIENT_SECRET='...' \\
    STAGING_GTIN=08712345678905 \\
    pytest -m staging

WARNING: this writes a **live** GS1 production resolver entry for ``STAGING_GTIN`` and
publishes a WordPress page. Use a disposable/pilot GTIN dedicated to smoke testing — the
run upserts with ``overwrite=True``. Optional overrides: ``WP_STAGING_POST_TYPE``
(default ``noviplast``), ``WP_STAGING_APP_PASS_ENV`` (default ``NOVIPLAST_WP_APP_PASS``).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lib.config import (
    ClientConfig,
    ExportConfig,
    GS1Config,
    GS1LinkConfig,
    QRConfig,
    WordPressConfig,
)
from lib.records import LocalisedText, Plan, PlanClassification, PlanRow, ProductRecord
from lib.state import load_state
from scripts import run_execute

_WP_URL = os.environ.get("WP_STAGING_URL")
_WP_USER = os.environ.get("WP_STAGING_USER")
_WP_APP_PASS_ENV = os.environ.get("WP_STAGING_APP_PASS_ENV", "NOVIPLAST_WP_APP_PASS")
_WP_POST_TYPE = os.environ.get("WP_STAGING_POST_TYPE", "noviplast")

_GS1_ACCOUNT = os.environ.get("GS1_PROD_ACCOUNT")
_GS1_CLIENT_ID_ENV = "GS1_PROD_CLIENT_ID"
_GS1_CLIENT_SECRET_ENV = "GS1_PROD_CLIENT_SECRET"

_GTIN = os.environ.get("STAGING_GTIN", "08712345678905")

_STAGING_READY = bool(
    _WP_URL
    and _WP_USER
    and os.environ.get(_WP_APP_PASS_ENV)
    and _GS1_ACCOUNT
    and os.environ.get(_GS1_CLIENT_ID_ENV)
    and os.environ.get(_GS1_CLIENT_SECRET_ENV)
)

pytestmark = [
    pytest.mark.staging,
    pytest.mark.skipif(
        not _STAGING_READY,
        reason="staging WP + GS1 production not configured (set WP_STAGING_URL, "
        "WP_STAGING_USER, the WP app-password env, GS1_PROD_ACCOUNT, GS1_PROD_CLIENT_ID, "
        "and GS1_PROD_CLIENT_SECRET)",
    ),
]

_CLIENT_ID = "staging"


def _config() -> ClientConfig:
    return ClientConfig(
        client_id=_CLIENT_ID,
        display_name="Staging Smoke",
        gs1=GS1Config(
            account_number_test="0",
            account_number_production=_GS1_ACCOUNT,
            client_id_env_test="UNUSED",
            client_secret_env_test="UNUSED",
            client_id_env_production=_GS1_CLIENT_ID_ENV,
            client_secret_env_production=_GS1_CLIENT_SECRET_ENV,
            environment="production",
        ),
        export=ExportConfig(path="input/staging.xlsx"),
        wordpress=WordPressConfig(
            site_url=_WP_URL or "",
            username=_WP_USER or "",
            app_password_env=_WP_APP_PASS_ENV,
            post_type=_WP_POST_TYPE,
            multilingual_plugin="polylang",
            default_language="nl",
            languages=["nl"],
        ),
        qr=QRConfig(formats=["svg"], size_mm=20, error_correction="M", dpi=300),
        gs1_links=[GS1LinkConfig(link_type="pip", default=True, title_pattern="{product_name}")],
    )


def _plan_file(tmp_path: Path) -> Path:
    product = ProductRecord(
        gtin=_GTIN,
        brand="SmokeTest",
        product_name=LocalisedText(values={"nl": "Smoke test product"}),
    )
    row = PlanRow(
        gtin=_GTIN,
        language="nl",
        classification=PlanClassification.NEW,
        title="Smoke test product",
        slug=f"p-{_GTIN}",
        content_hash="staging-smoke",
        target_url=f"{_WP_URL}/{_WP_POST_TYPE}/p-{_GTIN}/",
        product=product,
    )
    plan = Plan(
        client_id=_CLIENT_ID,
        generated_at=datetime(2026, 7, 12, tzinfo=UTC),
        total=1,
        counts={PlanClassification.NEW: 1},
        rows=[row],
    )
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan.model_dump(mode="json")), encoding="utf-8")
    return path


def _entry_without_timestamp(client_id: str) -> dict[str, object]:
    entry = load_state(client_id).entries[_GTIN]["nl"].model_dump(mode="json")
    entry.pop("last_run")
    return entry


def test_run_execute_end_to_end_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_execute, "get_client", lambda _cid: _config())
    plan = _plan_file(tmp_path)

    # DoD: completes end-to-end for one GTIN against staging WP + GS1 production.
    assert run_execute.main([_CLIENT_ID, "--plan", str(plan)]) == 0
    first = _entry_without_timestamp(_CLIENT_ID)
    assert first["wp_page_id"]

    logs = sorted((tmp_path / "output" / _CLIENT_ID / "runs").glob("*.jsonl"))
    outcomes = [json.loads(line) for line in logs[-1].read_text().splitlines()]
    assert [o["status"] for o in outcomes] == ["ok"]

    # §6.5: a second identical run leaves the meaningful state unchanged.
    assert run_execute.main([_CLIENT_ID, "--plan", str(plan)]) == 0
    assert _entry_without_timestamp(_CLIENT_ID) == first
