"""Eager, legible checks that a client is ready to run — the pure half of ``scripts/doctor``.

Every failure this module reports was previously a failure the operator met *late*. A missing
secret surfaced as :class:`~lib.errors.MissingCredentialError` at the first API call (E15), so
parse, plan and dry-run could all pass before it fired. A blank ``clients.yml`` field surfaced
as the first pydantic error and only the first. A copy cache that no longer matched the export
surfaced not at all: those units simply vanished from the plan (E21). The point of a preflight
is to move each of those to a moment where nothing is half-done and nothing is permanent.

Every check here is a function from configuration to a :class:`CheckResult`. None of them print,
none of them exit, and none of them write anything the pipeline reads — so a UI, a test, and the
CLI can all run the same checks and disagree only about how to display them.

**Nothing here calls** :func:`lib.state.load_state`. An idle peek at a corrupt state file
*quarantines* it (E19) — a side effect no diagnostic should have, and one that would make merely
looking at the system change what the next run does.

Two checks earn their place by guarding traps that are otherwise silent:

* :func:`check_generator` catches a ``generator`` block removed as unused cleanup. ``run_plan``
  derives ``require_generated_copy = cfg.generator is not None``, so deleting the block does not
  raise — it disables the E21 guard, and units with no copy publish with blank taglines instead
  of being held.
* :func:`check_cache_coverage` catches a generated-copy cache that no longer matches the export.
  The fingerprint covers ``{inputs, language, prompt_version}``, so any feed edit or version bump
  makes those units *pending* again — and a pending unit with no producer to fill it is an E21
  omission, which is to say invisible.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Final

import jsonschema

from lib.categories import coverage_report
from lib.config import DEFAULT_CLIENTS_PATH, ClientConfig, get_client, load_clients
from lib.errors import (
    ConfigError,
    ExportParseError,
    GS1APIError,
    MissingCredentialError,
    OrchestratorError,
    ProcessListError,
    VideoMapError,
    WordPressAPIError,
)
from lib.generator import load_cache, pending_requests, prefill_from_feed
from lib.gs1_dl_client import GS1DigitalLinkClient
from lib.media_video import (
    VideoMapSummary,
    canon_gtin,
    check_video_map,
    fully_mapped_gtins,
    list_video_files,
    load_video_map,
    summarize_video_map,
)
from lib.process_list import load_process_list
from lib.records import ProductRecord
from lib.wp_client import WordPressClient, WordPressIdentity

if TYPE_CHECKING:
    from collections.abc import Iterator

#: Where ``scripts/parse_export.py`` writes the normalised products.
_PRODUCTS_RELATIVE: Final = Path("data") / "products.json"

#: WordPress roles that can publish. A credential that authenticates but holds none of these
#: passes a naive check and then fails mid-run, after some rows are already live.
_PUBLISHING_ROLES: Final = frozenset({"administrator", "editor"})

#: A WordPress application password is issued as six space-separated groups. Fewer almost
#: always means the value lost its quotes in ``.env`` and was truncated at the first space.
_APP_PASSWORD_GROUPS: Final = 6

#: GS1 v2 error code for "no valid contract found" — the hard blocker that no code or config
#: change can fix, because the Digital Link contract is provisioned by GS1 on the account.
_NO_CONTRACT_CODE: Final = "21011"

#: How much of an error body to quote back. Enough to identify it, short enough to read.
_ERROR_BODY_CHARS: Final = 300


class Status(StrEnum):
    """How a check came out.

    ``NA`` is not a pass. A check that does not apply — no ``categories`` block, no video
    mapping — has proved nothing, and reporting it as ``OK`` would let an operator read a
    green line as evidence about a thing that was never looked at.
    """

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    NA = "n/a"


@dataclass(frozen=True)
class CheckResult:
    """One check's verdict.

    ``remedy`` is separate from ``detail`` on purpose: the detail says what is true, the
    remedy says what to do about it, and an operator who is not an engineer needs the second
    one more. It is empty when there is nothing to do.
    """

    name: str
    title: str
    status: Status
    detail: str
    remedy: str = ""
    data: dict[str, object] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        """Whether this check should stop a run."""
        return self.status is Status.FAIL


# --- Offline checks ----------------------------------------------------------


def check_config(path: str | Path = DEFAULT_CLIENTS_PATH) -> CheckResult:
    """Validate ``clients.yml`` against the JSON Schema, reporting **every** field at fault.

    Schema errors are collected with ``jsonschema.iter_errors`` rather than left to
    :func:`lib.config.load_clients`, which raises on the first one and discards the
    ``json_path`` — so an operator with four blank fields fixes them one run at a time,
    learning about the next only after correcting the last.

    Pydantic validation still runs afterwards (it enforces cross-field rules the schema
    cannot express); its first error is reported as-is, since by then the shape is sound.
    """
    path = Path(path)
    if not path.is_file():
        return CheckResult(
            "config",
            "Configuration file",
            Status.FAIL,
            f"{path} does not exist",
            remedy="Copy clients.example.yml to clients.yml and fill in your client's block.",
        )

    schema_errors = list(_schema_errors(path))
    if schema_errors:
        return CheckResult(
            "config",
            "Configuration file",
            Status.FAIL,
            f"{path} has {len(schema_errors)} schema error(s): " + "; ".join(schema_errors),
            remedy="Fix each field named above. clients.example.yml shows a working value.",
            data={"errors": schema_errors},
        )

    try:
        clients = load_clients(path)
    except (ConfigError, ExportParseError) as exc:
        return CheckResult(
            "config",
            "Configuration file",
            Status.FAIL,
            f"{path} failed validation: {exc}",
            remedy="Fix the field named above.",
        )
    return CheckResult(
        "config",
        "Configuration file",
        Status.OK,
        f"{path} is valid; {len(clients)} client(s) defined: {', '.join(sorted(clients))}",
        data={"clients": sorted(clients)},
    )


def _schema_errors(path: Path) -> Iterator[str]:
    """Yield every JSON Schema violation in ``path`` as ``"<json path>: <message>"``."""
    import yaml  # noqa: PLC0415 — local, so importing this module stays cheap for a UI

    schema_path = Path(__file__).resolve().parent.parent / "schema" / "clients.schema.json"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, json.JSONDecodeError) as exc:
        yield f"$: cannot be read as YAML ({exc})"
        return
    validator = jsonschema.Draft202012Validator(schema)
    for error in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        yield f"{error.json_path}: {error.message}"


def check_generator(cfg: ClientConfig) -> CheckResult:
    """Guard the E21 trap: a ``generator`` block deleted because it "looked unused".

    ``run_plan`` derives ``require_generated_copy = cfg.generator is not None``. Removing the
    block therefore does not raise — it silently turns off the check that holds a copy-less
    unit back, and those units publish with blank taglines instead.

    This matters most on a machine that has no API key and never generates anything, where the
    block genuinely *looks* like dead configuration. It is not: it is the switch.

    An absent block is only reported as a failure when a generated-copy cache exists, which is
    proof that copy was generated for this client and so that a generator was configured once.
    With no cache and no block, the client simply has no generator and E21 does not apply.
    """
    if cfg.generator is not None:
        return CheckResult(
            "generator_block",
            "Generator block (E21 guard)",
            Status.OK,
            f"present (prompt_version {cfg.generator.prompt_version}); "
            "units with no generated copy will be held out of the plan",
        )
    if _cache_entry_count(cfg.client_id):
        return CheckResult(
            "generator_block",
            "Generator block (E21 guard)",
            Status.FAIL,
            "clients.yml has no `generator` block, but a generated-copy cache exists for this "
            "client — so one was configured before. Without it run_plan sets "
            "require_generated_copy=False and a unit with no copy publishes a blank tagline "
            "instead of being held.",
            remedy="Restore the `generator:` block in clients.yml. It is required even on a "
            "machine with no API key, which never generates anything: it is the E21 switch, "
            "not a credential.",
        )
    return CheckResult(
        "generator_block",
        "Generator block (E21 guard)",
        Status.NA,
        "no `generator` block and no generated-copy cache — this client does not use generated "
        "copy, so E21 does not apply",
    )


def in_scope(cfg: ClientConfig, products: list[ProductRecord]) -> list[ProductRecord]:
    """The products a run could touch, decided from configuration alone.

    Composes the same two gates ``run_plan`` applies — the process list, then the
    confirmed-video allowlist behind ``media.restrict_to_mapped_gtins`` — out of the same
    ``lib`` primitives, so a check reports on the work the operator actually asked for rather
    than on the whole catalogue. Without this, a client whose export holds 127 products but
    whose process list names 38 gets told about 224 missing cache entries it will never need,
    and learns to ignore the report.

    This is deliberately a **superset** of ``run_plan``'s scope: it omits the "already
    published" drop, because deciding that needs ``state.json``, and an idle read of a corrupt
    state file quarantines it (E19). Erring wide is the safe direction — a preflight may
    mention a unit that turns out to be finished; it must never stay silent about one that is
    not.
    """
    scoped = products
    if cfg.process_list is not None:
        try:
            listed = load_process_list(cfg.process_list)
        except ProcessListError:
            return scoped  # check_process_list reports this; do not fail twice over it
        scoped = [product for product in scoped if product.gtin14 in listed]
    media = cfg.media
    if media is not None and media.restrict_to_mapped_gtins and media.video_map_path:
        try:
            allow = fully_mapped_gtins(
                load_video_map(Path(media.video_map_path)), cfg.wordpress.languages
            )
        except VideoMapError:
            return scoped  # check_video_coverage reports this
        scoped = [product for product in scoped if canon_gtin(product.gtin) in allow]
    return scoped


def check_scope(cfg: ClientConfig, products: list[ProductRecord]) -> CheckResult:
    """State plainly how many products a run would touch, and what removed the rest.

    The number an operator most needs and is least often given. "38 GTINs on the list" and
    "15 GTINs a run can actually publish" are very different facts, and every gate between
    them is silent by design — a filtered-out product produces no error, no row and no count.
    """
    if not products:
        return CheckResult(
            "scope",
            "What a run would touch",
            Status.WARN,
            "no parsed products",
            remedy="Run `python -m scripts.parse_export` first.",
        )
    scoped = in_scope(cfg, products)
    reasons = []
    if cfg.process_list is not None:
        reasons.append(f"process list ({cfg.process_list.path})")
    media = cfg.media
    if media is not None and media.restrict_to_mapped_gtins:
        reasons.append("media.restrict_to_mapped_gtins (confirmed video in every language)")
    detail = f"{len(scoped)} of {len(products)} product(s) in the export are in scope"
    if reasons:
        detail += ", after " + " and ".join(reasons)
    data: dict[str, object] = {"in_scope": len(scoped), "total": len(products)}
    if not scoped:
        return CheckResult(
            "scope",
            "What a run would touch",
            Status.FAIL,
            detail + " — a run would publish nothing and report success",
            remedy="Check that the process list names GTINs that are in the export, and that "
            "the gates above are not excluding all of them.",
            data=data,
        )
    return CheckResult(
        "scope",
        "What a run would touch",
        Status.OK,
        detail + f". Every check below reports on those {len(scoped)}, not on the whole export.",
        data=data,
    )


def check_cache_coverage(cfg: ClientConfig, products: list[ProductRecord]) -> CheckResult:
    """Report how much of the **in-scope** export the generated-copy cache still covers.

    The core check when copy is generated on one machine and published from another. A cache
    entry's fingerprint covers ``{inputs, language, prompt_version}``, so editing one product
    in the feed — or bumping ``prompt_version`` — makes that unit *pending* again. A pending
    unit with no producer to fill it is an E21 omission: it leaves the plan without a row, and
    before ``Plan.skipped`` existed it left without a trace.

    Entirely offline. It answers "is the cache I was handed still the right one for this
    export?", which is the question a stale handover fails.
    """
    if cfg.generator is None:
        return CheckResult(
            "cache_coverage",
            "Generated-copy cache",
            Status.NA,
            "no `generator` block — this client publishes feed copy only",
        )
    products = in_scope(cfg, products)
    if not products:
        return CheckResult(
            "cache_coverage",
            "Generated-copy cache",
            Status.WARN,
            "no in-scope products to check the cache against",
            remedy="Run `python -m scripts.parse_export` first, and check the scope above.",
        )

    cache = load_cache(cfg.client_id)
    languages = cfg.wordpress.languages
    # prefill_from_feed fills the units whose feed copy is usable verbatim, and pending_requests
    # is documented as needing it to have run first. It mutates the cache *in memory* only —
    # nothing here writes it back, so the file on disk is untouched by looking at it.
    prefill_from_feed(
        products, cache, languages, cfg.generator.prompt_version, now=datetime.now(UTC)
    )
    pending = pending_requests(products, cache, languages, cfg.generator.prompt_version)
    total = len(products) * len(languages)
    covered = total - len(pending)
    data: dict[str, object] = {
        "total": total,
        "covered": covered,
        "pending": len(pending),
        "pending_units": [(request.gtin, request.language) for request in pending[:20]],
    }
    if not pending:
        return CheckResult(
            "cache_coverage",
            "Generated-copy cache",
            Status.OK,
            f"{total} unit(s), all cached",
            data=data,
        )
    return CheckResult(
        "cache_coverage",
        "Generated-copy cache",
        Status.FAIL,
        f"{total} unit(s), {covered} cached, {len(pending)} pending — the pending ones have no "
        "copy for this version of the export and will be dropped from the plan (E21)",
        remedy="Request a fresh generated_cache.json for the current export before publishing. "
        "A cache goes stale on any feed edit or prompt_version bump, not only on new products.",
        data=data,
    )


def check_process_list(cfg: ClientConfig) -> CheckResult:
    """Read the process list, which is also how a zero-GTIN file is caught.

    ``load_process_list`` treats an empty list as an error rather than an empty run, for the
    reason this whole module exists: an empty plan and a run that reports success having
    published nothing are indistinguishable from the outside.
    """
    if cfg.process_list is None:
        return CheckResult(
            "process_list",
            "Process list",
            Status.NA,
            "no `process_list` block — every product in the export is planned",
        )
    try:
        listed = load_process_list(cfg.process_list)
    except ProcessListError as exc:
        return CheckResult(
            "process_list",
            "Process list",
            Status.FAIL,
            str(exc),
            remedy=f"Check that {cfg.process_list.path} exists and has a "
            f"{cfg.process_list.gtin_column!r} column with at least one barcode.",
        )
    return CheckResult(
        "process_list",
        "Process list",
        Status.OK,
        f"{len(listed)} GTIN(s) listed for processing in {cfg.process_list.path}",
        data={"count": len(listed)},
    )


def check_category_coverage(cfg: ClientConfig, products: list[ProductRecord]) -> CheckResult:
    """Run the ``build_brick_map --check`` gate offline: every GPC brick resolves to a term."""
    if cfg.categories is None:
        return CheckResult(
            "categories",
            "Category coverage",
            Status.NA,
            "no `categories` block — pages are published without a site category",
        )
    if not products:
        return CheckResult(
            "categories",
            "Category coverage",
            Status.WARN,
            "no parsed products to check brick coverage against",
            remedy="Run `python -m scripts.parse_export` first.",
        )
    report = coverage_report(products, cfg.categories)
    if report.is_complete:
        return CheckResult(
            "categories",
            "Category coverage",
            Status.OK,
            f"all {report.total_bricks} GPC brick(s) resolve to a term",
        )
    return CheckResult(
        "categories",
        "Category coverage",
        Status.WARN,
        f"{len(report.unmapped)} of {report.total_bricks} GPC brick(s) map to nothing: "
        + ", ".join(sorted(report.unmapped)),
        remedy="Add them to categories.brick_category_map, or set a per-GTIN override. "
        "`python -m scripts.build_brick_map --check` prints the same list. Unmapped bricks "
        "publish with the category left unset — the tool never guesses one.",
        data={"unmapped": sorted(report.unmapped)},
    )


def check_video_coverage(cfg: ClientConfig) -> CheckResult:
    """Run the ``build_video_map --check`` gate offline: every video file maps to a GTIN.

    Four outcomes, because they have four different fixes: no mapping configured (NA), a mapping
    that will not load (FAIL), the folders empty (WARN — copy the library across), and gaps in a
    mapping whose files are present (WARN — the client confirms them).
    """
    media = cfg.media
    if media is None or not media.video_map_path:
        return CheckResult(
            "video_map",
            "Video mapping",
            Status.NA,
            "no `media.video_map_path` — no videos are attached to pages",
        )
    try:
        vmap = load_video_map(Path(media.video_map_path))
    except VideoMapError as exc:
        return CheckResult(
            "video_map",
            "Video mapping",
            Status.FAIL,
            str(exc),
            remedy="A missing file is a path problem — check media.video_map_path in clients.yml. "
            "A syntax error is an edit: the position above is where to look.",
        )
    files = {
        language: [p.name for p in list_video_files(Path(folder))]
        for language, folder in media.video_folders.items()
    }
    summary = summarize_video_map(vmap, files, cfg.wordpress.languages)
    data: dict[str, object] = {
        "confirmed_gtins": summary.confirmed_gtins,
        "files": summary.files,
        "entries": summary.entries,
        "unconfirmed": summary.unconfirmed,
        "ambiguous": summary.ambiguous,
        "missing_from_map": summary.missing_from_map,
        "file_missing": summary.file_missing,
        "issues": [
            issue.model_dump(mode="json") for issue in check_video_map(vmap, files)[:_ISSUE_SAMPLE]
        ],
    }
    if not summary.gaps:
        return CheckResult(
            "video_map",
            "Video mapping",
            Status.OK,
            f"{summary.files} video file(s), all mapped and confirmed; "
            f"{summary.confirmed_gtins} GTIN(s) have a confirmed video in every language",
            data=data,
        )
    # A gap is a WARN even under restrict_to_mapped_gtins, and especially then: the restriction
    # is what makes the gap *safe*. An unconfirmed GTIN is excluded from the run rather than
    # published with the wrong video, so this narrows the batch — it does not break it. Calling
    # a handled condition a failure is how a report earns the right to be ignored.
    return CheckResult(
        "video_map",
        "Video mapping",
        Status.WARN,
        _video_gap_detail(summary, restricted=media.restrict_to_mapped_gtins),
        remedy=_VIDEO_FILES_REMEDY if summary.no_files_found else _VIDEO_GAP_REMEDY,
        data=data,
    )


#: How many example gaps to carry in the check's payload for a screen to render.
_ISSUE_SAMPLE = 20

_VIDEO_FILES_REMEDY = (
    "Copy the video folders named under media.video_folders onto this machine — the mapping is "
    "the index to them, not a substitute. Nothing here is wrong with the mapping itself."
)

_VIDEO_GAP_REMEDY = (
    "`python -m scripts.build_video_map --check` lists each gap and writes "
    "video_map_issues.json. Confirming a mapping is the client's call, not the tool's."
)


def _video_gap_detail(summary: VideoMapSummary, *, restricted: bool) -> str:
    """Say what is actually missing, counting each kind against its own denominator.

    This line used to read ``284 of 0 video file(s) are not yet confirmed``: every gap of every
    kind, over the number of files on disk. With the mapping handed over and the multi-gigabyte
    video library not yet copied — a day-one operator machine — each of the 166 rows also
    reported the file it names as absent, so the numerator counted a different thing from the
    denominator and comfortably exceeded it. A count that cannot be true teaches its reader to
    stop reading the line, which is expensive on the one screen they are meant to work down.
    """
    if summary.no_files_found:
        return (
            f"no video files found, but the mapping has {summary.entries} row(s) — the video "
            "folders are empty or not on this machine yet, so nothing can be attached to a page"
        )

    parts = [f"{summary.files} video file(s) found"]
    if summary.unconfirmed:
        parts.append(f"{summary.unconfirmed} mapping row(s) with no GTIN yet")
    if summary.missing_from_map:
        parts.append(f"{summary.missing_from_map} file(s) not in the mapping")
    if summary.file_missing:
        parts.append(f"{summary.file_missing} row(s) naming a file that is not there")
    if summary.ambiguous:
        parts.append(f"{summary.ambiguous} GTIN(s) mapped to more than one file")
    parts.append(f"{summary.confirmed_gtins} GTIN(s) confirmed in every language")

    detail = "; ".join(parts)
    if restricted:
        detail += " — and only those can be published, because media.restrict_to_mapped_gtins is on"
    return detail


def check_ffmpeg(cfg: ClientConfig) -> CheckResult:
    """Check for ``ffmpeg`` on PATH — the one external binary, and only when it is used."""
    media = cfg.media
    if media is None or not media.video_transcode:
        return CheckResult(
            "ffmpeg",
            "ffmpeg",
            Status.NA,
            "media.video_transcode is off — videos are uploaded as-is",
        )
    binary = media.ffmpeg_bin or "ffmpeg"
    found = shutil.which(binary)
    if found:
        return CheckResult("ffmpeg", "ffmpeg", Status.OK, f"{binary} found at {found}")
    return CheckResult(
        "ffmpeg",
        "ffmpeg",
        Status.FAIL,
        f"{binary} is not on PATH, but media.video_transcode is on",
        remedy="Install ffmpeg (macOS: `brew install ffmpeg`), or set media.video_transcode "
        "to false to upload the source files unchanged.",
    )


# --- Network checks ----------------------------------------------------------


def check_wordpress(cfg: ClientConfig) -> CheckResult:
    """Prove the WordPress credential authenticates *and* can still publish.

    ``GET /wp/v2/users/me?context=edit``, which is the check ``docs/troubleshooting.md``
    documents as a shell one-liner. The ``context=edit`` matters: a bare ``users/me`` returns
    200 for any credential that authenticates and omits ``roles`` entirely, so it cannot tell a
    working password from a working password on a demoted account.

    A 401 is reported with the six-groups hint, because the commonest cause by a wide margin is
    an application password that lost its quotes in ``.env`` and was truncated at the first
    space — a password the operator is certain is correct.
    """
    try:
        with WordPressClient(cfg.wordpress) as wp:
            identity = wp.whoami()
            detected = wp.detect_multilingual_plugin()
    except MissingCredentialError as exc:
        return CheckResult(
            "wordpress",
            "WordPress credential",
            Status.FAIL,
            str(exc),
            remedy=f"Set {cfg.wordpress.app_password_env} in .env, single-quoted — WordPress "
            "issues the password as six space-separated groups and an unquoted value is "
            "truncated at the first space.",
        )
    except WordPressAPIError as exc:
        return CheckResult(
            "wordpress",
            "WordPress credential",
            Status.FAIL,
            f"{cfg.wordpress.site_url} rejected the credential: {exc}",
            remedy=_wordpress_remedy(cfg, exc),
        )

    return _wordpress_verdict(cfg, identity, detected)


def _wordpress_remedy(cfg: ClientConfig, exc: WordPressAPIError) -> str:
    """The most likely fix for a WordPress error, chosen by status."""
    if exc.status_code == HTTPStatus.UNAUTHORIZED:
        return (
            f"Check {cfg.wordpress.app_password_env} in .env is single-quoted and has all "
            f"{_APP_PASSWORD_GROUPS} groups, and that {cfg.wordpress.username!r} still has an "
            "application password. An unquoted value truncates at the first space, which "
            "produces a 401 with a password the operator knows is correct."
        )
    if exc.status_code == HTTPStatus.FORBIDDEN:
        return (
            f"{cfg.wordpress.username!r} authenticated but lacks edit capability. Restore its "
            "editor role in WP Admin → Users."
        )
    return (
        f"Check that {cfg.wordpress.site_url} is reachable and its REST API is enabled "
        "(/wp-json should return JSON)."
    )


def _wordpress_verdict(
    cfg: ClientConfig, identity: WordPressIdentity, detected: str
) -> CheckResult:
    """Turn a successful ``whoami`` into a verdict, including the plugin-mismatch warning."""
    roles = identity.roles
    slug = identity.slug
    configured = cfg.wordpress.multilingual_plugin
    data: dict[str, object] = {
        "user": slug,
        "roles": roles,
        "plugin_configured": configured,
        "plugin_detected": detected,
    }

    if not _PUBLISHING_ROLES.intersection(roles):
        return CheckResult(
            "wordpress",
            "WordPress credential",
            Status.FAIL,
            f"authenticated as {slug!r} with roles {roles or ['(none)']} — none of which can "
            "publish",
            remedy=f"Give {cfg.wordpress.username!r} the editor role in WP Admin → Users. A "
            "credential that authenticates but cannot publish fails mid-run, after some rows "
            "are already live.",
            data=data,
        )

    # A mismatch is a warning, not a failure: _resolve_plugin lets the configured value win, so
    # the run still uses the right adapter. It is worth saying because the *other* direction is
    # silent — a site that answers "none" to both probes gets the NoOpAdapter, whose
    # link_translations does nothing and raises nothing, and every page publishes unlinked.
    if configured not in ("none", detected):
        return CheckResult(
            "wordpress",
            "WordPress credential",
            Status.WARN,
            f"authenticated as {slug!r} ({', '.join(roles)}), but the multilingual plugin is "
            f"configured as {configured!r} and the site probes as {detected!r}. The configured "
            "value wins, so the run proceeds — but one of the two is wrong.",
            remedy=f"Confirm {configured!r} is actually active on {cfg.wordpress.site_url}. If "
            "it is not, translations will not be linked and no error will say so.",
            data=data,
        )
    return CheckResult(
        "wordpress",
        "WordPress credential",
        Status.OK,
        f"authenticated as {slug!r} ({', '.join(roles)}) on {cfg.wordpress.site_url}; "
        f"multilingual plugin: {detected}",
        data=data,
    )


def check_site_serves(cfg: ClientConfig) -> CheckResult:
    """HEAD the site's public URL — the cheapest check here, and the one with no credential.

    Separated from :func:`check_wordpress` so "the site is down" and "the password is wrong"
    are two different answers rather than one confusing one.
    """
    try:
        with WordPressClient(cfg.wordpress) as wp:
            # verify_url returns True *or raises* — it never returns False. Both are handled
            # because the signature permits both and the comment in run_execute says so.
            served = wp.verify_url(cfg.wordpress.site_url)
    except (WordPressAPIError, MissingCredentialError) as exc:
        return CheckResult(
            "site_serves",
            "Site reachable",
            Status.FAIL,
            f"{cfg.wordpress.site_url} did not serve: {exc}",
            remedy="Check the site_url in clients.yml, and that the site is up.",
        )
    if not served:
        return CheckResult(
            "site_serves",
            "Site reachable",
            Status.FAIL,
            f"{cfg.wordpress.site_url} did not serve a 2xx/3xx",
            remedy="Check the site_url in clients.yml, and that the site is up.",
        )
    return CheckResult(
        "site_serves", "Site reachable", Status.OK, f"{cfg.wordpress.site_url} serves"
    )


def check_gs1(cfg: ClientConfig, products: list[ProductRecord]) -> CheckResult:
    """Mint a GS1 token and issue one read-only GET, so credentials fail here and not mid-run.

    Constructing the client mints nothing; the GET does, which is what makes this a credential
    check at all. The request is a read — it creates no record, and there is nothing to undo.

    **A ``None`` result cannot prove the account has a Digital Link contract.** The API answers
    a GTIN it has never seen with the same ``400 "No valid contract found for Gtin with id: …"``
    that the 21011 blocker produces, and the client maps that to ``None``. So a clean ``None``
    is reported as reachable-with-credentials-accepted and explicitly *not* as contract-present.
    Saying otherwise would be the most expensive kind of false pass: every write fails, and the
    operator has a green preflight telling them the account is fine.
    """
    environment = cfg.gs1.environment
    try:
        resolved = cfg.gs1.resolve()
    except ConfigError as exc:
        return CheckResult(
            "gs1",
            f"GS1 resolver ({environment})",
            Status.FAIL,
            str(exc),
            remedy="Fill in the account number and credential env-var names for this "
            "environment in the client's `gs1` block.",
        )

    gtin = products[0].gtin if products else None
    if gtin is None:
        return CheckResult(
            "gs1",
            f"GS1 resolver ({environment})",
            Status.WARN,
            "no parsed products, so there is no real GTIN to probe with",
            remedy="Run `python -m scripts.parse_export` first. This check will not invent a "
            "GTIN: a GS1 record can never be deleted, and a typo is permanent.",
        )

    try:
        with GS1DigitalLinkClient(resolved) as gs1:
            record = gs1.get(gtin)
    except MissingCredentialError as exc:
        return CheckResult(
            "gs1",
            f"GS1 resolver ({environment})",
            Status.FAIL,
            str(exc),
            remedy="Set the GS1 client id and secret for this environment in .env. The names "
            "come from the client's `gs1` block; the values come from MyGS1.",
        )
    except GS1APIError as exc:
        no_contract = _NO_CONTRACT_CODE in _gs1_error_codes(exc)
        return CheckResult(
            "gs1",
            f"GS1 resolver ({environment})",
            Status.FAIL,
            f"{exc}: {exc.response_body[:_ERROR_BODY_CHARS]}",
            remedy=(
                f"Error {_NO_CONTRACT_CODE}: this account has no Digital Link contract. That "
                "is provisioned by GS1 and cannot be fixed in code or config — see "
                "docs/gs1-nl-onboarding.md. Every write will fail until it is in place."
                if no_contract
                else "Check the account number and credentials for this environment against "
                "MyGS1, and docs/gs1-nl-onboarding.md for what the status means."
            ),
            data={"status_code": exc.status_code, "no_contract": no_contract},
        )

    if record is None:
        return CheckResult(
            "gs1",
            f"GS1 resolver ({environment})",
            Status.OK,
            f"credentials accepted and the {environment} resolver answered; GTIN {gtin} has no "
            "record yet, which is expected before its first publish. Note this response cannot "
            "distinguish 'not registered' from 'no Digital Link contract' — both are the same "
            "400.",
            data={"gtin": gtin, "registered": False},
        )
    return CheckResult(
        "gs1",
        f"GS1 resolver ({environment})",
        Status.OK,
        f"credentials accepted; GTIN {gtin} is already registered on the {environment} "
        "resolver, which also proves the account's Digital Link contract is live",
        data={"gtin": gtin, "registered": True},
    )


# --- Orchestration -----------------------------------------------------------


def load_products(client_id: str) -> list[ProductRecord]:
    """Read the parsed products, or an empty list when ``parse_export`` has not run.

    Missing is not an error here: a preflight run before the first parse is a reasonable
    thing to do, and the checks that need products say so themselves.
    """
    path = Path("output") / client_id / _PRODUCTS_RELATIVE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    try:
        return [ProductRecord.model_validate(item) for item in data]
    except (ValueError, TypeError):
        return []


def run_checks(
    client_id: str | None = None,
    *,
    config_path: str | Path = DEFAULT_CLIENTS_PATH,
    offline: bool = False,
) -> list[CheckResult]:
    """Run every applicable check, in the order an operator should read them.

    Config first, because nothing after it means anything if it fails — and when it does, that
    single result is the whole report rather than a page of cascading noise.

    Offline checks come before network ones so a run with no connectivity still returns
    everything it legitimately can, and ``offline=True`` stops before any credential is read.
    """
    config = check_config(config_path)
    if config.failed:
        return [config]

    try:
        cfg = get_client(client_id, config_path)
    except ConfigError as exc:
        return [
            config,
            CheckResult(
                "client",
                "Client selection",
                Status.FAIL,
                str(exc),
                remedy="Pass the client id explicitly, or remove the extra client from "
                "clients.yml — the id is only optional when exactly one is defined.",
            ),
        ]

    products = load_products(cfg.client_id)
    results = [
        config,
        check_scope(cfg, products),
        check_generator(cfg),
        check_cache_coverage(cfg, products),
        check_process_list(cfg),
        check_category_coverage(cfg, products),
        check_video_coverage(cfg),
        check_ffmpeg(cfg),
    ]
    if offline:
        return results

    results.append(check_site_serves(cfg))
    results.append(check_wordpress(cfg))
    results.append(check_gs1(cfg, products))
    return results


def worst_status(results: list[CheckResult]) -> Status:
    """The most serious status in ``results`` — what the run as a whole should be called."""
    for status in (Status.FAIL, Status.WARN, Status.OK):
        if any(result.status is status for result in results):
            return status
    return Status.NA


def _cache_entry_count(client_id: str) -> int:
    """How many entries the generated-copy cache holds, or 0 when there is no cache."""
    try:
        return len(load_cache(client_id).entries)
    except OrchestratorError:
        return 0


def _gs1_error_codes(exc: GS1APIError) -> set[str]:
    """The ``code`` values from a v2 ``ErrorResult[]`` body, or an empty set."""
    codes: set[str] = set()
    for result in exc.error_results or []:
        errors = result.get("errors")
        if isinstance(errors, list):
            codes.update(
                str(item["code"]) for item in errors if isinstance(item, dict) and "code" in item
            )
    return codes
