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


class TemplateError(OrchestratorError):
    """A template could not be resolved or rendered."""


class StateError(OrchestratorError):
    """The state file could not be loaded, parsed, or written."""
