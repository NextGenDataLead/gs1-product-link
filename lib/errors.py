"""Typed exception hierarchy for the GS1 Digital Link Orchestrator.

Every module raises exceptions from this hierarchy rather than bare
``Exception`` (see ``docs/IMPLEMENTATION_SPEC.md`` §1 and §4.1). Callers can
catch :class:`OrchestratorError` to handle any tool-originated failure, or a
specific subclass for finer control.
"""

from __future__ import annotations


class OrchestratorError(Exception):
    """Base class for every error raised by this tool."""


class ConfigError(OrchestratorError):
    """Configuration is missing, malformed, or internally inconsistent."""


class MissingCredentialError(OrchestratorError):
    """A required secret (API token, app password) is absent from the env."""


class ExportParseError(OrchestratorError):
    """An Excel/CSV export row could not be parsed into a ``ProductRecord``."""


class GS1APIError(OrchestratorError):
    """A GS1 NL Digital Link API call returned a non-success response.

    Attributes:
        status_code: The HTTP status code of the failing response.
        response_body: The raw response body (first-500-char, PII-scrubbed
            variants are produced by the caller for logging; this holds the
            unscrubbed body for programmatic inspection).
        error_results: The parsed ``ErrorResult[]`` payload when the 400 body
            follows the standard v2 shape
            ``[{"identifier": ..., "errors": [{"code": ..., "message": ...}]}]``;
            ``None`` when the body is not in that shape (see §5.1).
        request_id: The server-assigned request id, when the API returns one.
    """

    def __init__(
        self,
        status_code: int,
        response_body: str,
        error_results: list[dict[str, object]] | None = None,
        request_id: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.response_body = response_body
        self.error_results = error_results
        self.request_id = request_id
        detail = f"GS1 API error {status_code}"
        if request_id:
            detail += f" (request_id={request_id})"
        super().__init__(detail)


class WordPressAPIError(OrchestratorError):
    """A WordPress REST API call returned a non-success response.

    Attributes:
        status_code: The HTTP status code of the failing response.
        response_body: The raw response body.
    """

    def __init__(self, status_code: int, response_body: str) -> None:
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(f"WordPress API error {status_code}")


class OverwriteError(OrchestratorError):
    """A write would replace an existing Digital Link and overwrite was not allowed.

    Raised by ``gs1_dl_client.safe_upsert`` when the GTIN already has an entry and the
    caller did not pass ``overwrite=True`` — the GET-before-write guard that prevents
    silently clobbering a live resolver target.

    Attributes:
        gtin: The GTIN whose existing entry would be overwritten.
        existing: The current server state (snapshot) that would be replaced.
    """

    def __init__(self, gtin: str, existing: object) -> None:
        self.gtin = gtin
        self.existing = existing
        super().__init__(
            f"Digital Link already exists for GTIN {gtin}; refusing to overwrite "
            "(pass overwrite=True to replace)"
        )


class GtinMismatchError(OrchestratorError):
    """A WordPress page exists at the target slug but belongs to a different GTIN.

    Raised by ``wp_client.upsert_page`` when the page found by slug (or id) carries a
    ``meta.gtin`` that does not match the row being written (edge E8). The WordPress
    sibling of :class:`OverwriteError`: a GET-before-write guard that refuses to
    overwrite an unrelated page. Distinct from :class:`WordPressAPIError` (E11, a slug
    collision the server reports as 409) so callers can *log and skip the row* rather
    than treat it as a transport failure needing human intervention.

    Attributes:
        gtin: The GTIN of the row being written.
        existing_gtin: The GTIN recorded on the page already at that slug/id.
        wp_page_id: The id of the conflicting WordPress page.
    """

    def __init__(self, gtin: str, existing_gtin: str, wp_page_id: int) -> None:
        self.gtin = gtin
        self.existing_gtin = existing_gtin
        self.wp_page_id = wp_page_id
        super().__init__(
            f"WordPress page {wp_page_id} has meta.gtin {existing_gtin!r}, "
            f"which does not match row GTIN {gtin!r}; skipping to avoid overwriting"
        )


class TemplateError(OrchestratorError):
    """A template could not be resolved or rendered."""


class StateError(OrchestratorError):
    """The state file could not be loaded, parsed, or written."""


class WebsiteStatusError(OrchestratorError):
    """The website-status control file is missing, unreadable, or malformed.

    Raised by ``lib.website_status.load_website_status`` when the operator's
    control file (``input/{client_id}/website_status.xlsx``) cannot be opened or
    is missing a required column. The control file gates which products are
    eligible for page/QR creation (already in GS1 and not yet on the website);
    a missing or malformed one is an operator-config error, so ``run_plan.py``
    treats it like :class:`ConfigError` (exit 2).
    """


class GeneratorError(OrchestratorError):
    """The generated-content cache could not be loaded, parsed, written, or validated.

    Raised by ``lib.generator`` for a corrupt/unwritable ``generated_cache.json`` or a
    producer result that fails validation (e.g. empty bullet lists). Mirrors
    :class:`StateError`: the cache is a between-runs artifact, and a malformed one is a
    fault the operator must see rather than silently ignore.
    """
