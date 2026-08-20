"""What the screens read: config facts, file freshness, plan and run artifacts.

Read-only, and every function tolerates the file being absent — a shell that raises before the
operator has run anything is a shell that cannot help them run it. "Not there yet" is a state to
display, not an error.

Nothing here loads ``state.json``, for the reason :mod:`lib.preflight` gives: an idle read of a
corrupt one quarantines it (E19), and looking at the system must not change what the next run does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.config import ClientConfig, get_client, resolve_client_id
from lib.errors import ConfigError
from lib.gates import Mode
from lib.records import Plan, PlanSummary, ProductRecord, RunOutcome
from ui import REPO_ROOT


@dataclass(frozen=True)
class FileFact:
    """A path, whether it is there, and how stale it is.

    Age is shown wherever a file is named because gate 0's export cross-check is exactly this
    question — "is this the file you mean?" — and a modification date answers it faster than a
    path does.
    """

    path: Path
    exists: bool
    modified: datetime | None
    size: int

    @property
    def age(self) -> str:
        """ "12 days ago", or "missing"."""
        if not self.exists or self.modified is None:
            return "missing"
        days = (datetime.now(UTC) - self.modified).days
        if days == 0:
            return "today"
        if days == 1:
            return "yesterday"
        return f"{days} days ago"


def file_fact(path: str | Path) -> FileFact:
    """Describe a path without reading it."""
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = REPO_ROOT / resolved
    try:
        stat = resolved.stat()
    except OSError:
        return FileFact(resolved, exists=False, modified=None, size=0)
    return FileFact(
        resolved,
        exists=True,
        modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
        size=stat.st_size,
    )


def client_id() -> str | None:
    """The single configured client, or ``None`` when the config cannot say which."""
    try:
        return resolve_client_id(None)
    except ConfigError:
        return None


def client_config(cid: str | None) -> ClientConfig | None:
    """The client's config, or ``None`` when it will not load — the Setup screen says why."""
    try:
        return get_client(cid)
    except (ConfigError, OSError):
        return None


def is_production(cfg: ClientConfig) -> bool:
    """Whether this client's GS1 environment is the permanent one."""
    return cfg.gs1.environment == "production"


def output_dir(cid: str) -> Path:
    return REPO_ROOT / "output" / cid


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_plan(cid: str) -> Plan | None:
    """The last plan, or ``None``."""
    data = _load_json(output_dir(cid) / "plan.json")
    if data is None:
        return None
    try:
        return Plan.model_validate(data)
    except ValueError:
        return None


def load_plan_summary(cid: str) -> PlanSummary | None:
    """The last plan's summary — counts, exclusions, the E19 flag, and the stderr line verbatim."""
    data = _load_json(output_dir(cid) / "plan.summary.json")
    if data is None:
        return None
    try:
        return PlanSummary.model_validate(data)
    except ValueError:
        return None


def product_count(cid: str) -> int | None:
    """How many products the parsed catalogue holds, or ``None`` when it has not been parsed."""
    data = _load_json(output_dir(cid) / "data" / "products.json")
    return len(data) if isinstance(data, list) else None


def doctor_check(payload: Any, name: str) -> dict[str, Any] | None:
    """One named check out of ``scripts.doctor --json``, or ``None``.

    The payload is whatever the subprocess printed, so it may not be a list at all — a crashed
    command still says something, and every caller here would rather show that than raise.
    """
    if not isinstance(payload, list):
        return None
    return next((entry for entry in payload if entry.get("name") == name), None)


@dataclass(frozen=True)
class Scope:
    """What a run would touch, as the doctor's ``scope`` check reports it.

    Read from the doctor rather than recomputed here, and that is the point: ``lib.preflight``
    already composes the two gates that decide scope — the process list, then the confirmed-video
    allowlist behind ``media.restrict_to_mapped_gtins`` — and a second implementation of "what
    will this run touch" is the same class of mistake as a second implementation of the gates.

    ``in_scope`` is deliberately a **superset** of what ``run_plan`` will classify: it omits the
    already-published drop, because deciding that needs ``state.json`` and an idle read of a
    corrupt one quarantines it (E19). So this is the ceiling on what a run could touch, never a
    promise of how many rows it will write — that number arrives at the plan gate.
    """

    in_scope: int
    total: int
    #: The doctor's sentence, verbatim, naming what removed the rest.
    detail: str
    #: The check failed: nothing is in scope, so a run would publish nothing and report success.
    empty: bool
    #: The in-scope GTINs, as ``ProductRecord.gtin`` — the same field the generated-copy cache is
    #: keyed by, so a screen can filter cache entries down to this run without renormalising.
    #: Empty when the doctor predates this field; callers must treat that as "scope unknown"
    #: rather than as "nothing is in scope".
    gtins: frozenset[str]


def scope_from(payload: Any) -> Scope | None:
    """Read the doctor's ``scope`` check, or ``None`` when it did not report one.

    ``None`` is a state to display, not a reason to fall back on the catalogue count. Showing the
    catalogue total under a label that says "in scope" would be the defect this replaces, wearing
    the right words.
    """
    entry = doctor_check(payload, "scope")
    if entry is None:
        return None
    data = entry.get("data") or {}
    in_scope, total = data.get("in_scope"), data.get("total")
    if not isinstance(in_scope, int) or not isinstance(total, int):
        return None
    gtins = data.get("in_scope_gtins")
    return Scope(
        in_scope=in_scope,
        total=total,
        detail=str(entry.get("detail") or ""),
        empty=entry.get("status") == "fail",
        gtins=frozenset(g for g in gtins if isinstance(g, str))
        if isinstance(gtins, list)
        else frozenset(),
    )


@dataclass(frozen=True)
class ResultsSplit:
    """A results file divided into this run's units and everything else.

    The file is written per run rather than accumulated, so ``others`` no longer means "copy from
    older batches this machine kept". It now means the file was produced against a **different
    scope** than the one about to run — which is a stronger signal than the old accumulation was,
    and the screen says so rather than folding it away as normal.
    """

    #: Copy for GTINs this run would touch.
    in_scope: dict[str, Any]
    #: Copy for everything else — written for a scope that is not this one.
    others: dict[str, Any]
    #: In-scope GTINs with no copy at all, sorted.
    missing: tuple[str, ...]
    #: Whether the split actually happened. ``False`` means scope was unknown, so ``in_scope``
    #: holds the whole file unfiltered and a caller must say so rather than present it as
    #: the batch.
    scoped: bool


def group_results(results: list[Any]) -> dict[str, dict[str, Any]]:
    """Group a results file's flat item list into ``{gtin: {language: item}}``.

    The file is a list because that is what a producer writes one entry at a time; a screen reads
    it per product. Items that are not objects, or carry no gtin, are dropped rather than raising:
    this runs against a file a human may have hand-edited.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        gtin, language = item.get("gtin"), item.get("language")
        if isinstance(gtin, str) and isinstance(language, str):
            grouped.setdefault(gtin, {})[language] = item
    return grouped


def split_results(entries: dict[str, dict[str, Any]], scope: Scope | None) -> ResultsSplit:
    """Divide this run's copy into the GTINs it covers and the rest.

    Membership is a plain set test against :attr:`Scope.gtins`, which the doctor reports as
    ``ProductRecord.gtin`` — the same field the results are keyed by. Nothing is renormalised here,
    deliberately: a second opinion about how a GTIN is spelled is a second opinion about what a
    run covers.

    An unknown scope returns everything as ``in_scope`` with ``scoped=False`` rather than an
    empty split. Filtering to nothing would hide the copy entirely and read as "there is none",
    which is wrong in the direction that stops an operator looking.
    """
    if scope is None or not scope.gtins:
        return ResultsSplit(in_scope=dict(entries), others={}, missing=(), scoped=False)
    return ResultsSplit(
        in_scope={gtin: value for gtin, value in entries.items() if gtin in scope.gtins},
        others={gtin: value for gtin, value in entries.items() if gtin not in scope.gtins},
        missing=tuple(sorted(scope.gtins - set(entries))),
        scoped=True,
    )


def load_products(cid: str) -> list[ProductRecord]:
    """The parsed catalogue, or an empty list when it is absent or unreadable.

    Empty rather than ``None``: the one screen that reads this uses it for fuzzy *suggestions*,
    so an unparsed catalogue should cost the suggestions and nothing else.
    """
    data = _load_json(output_dir(cid) / "data" / "products.json")
    if not isinstance(data, list):
        return []
    try:
        return [ProductRecord.model_validate(item) for item in data]
    except ValueError:
        return []


@dataclass(frozen=True)
class RunLog:
    """One run's JSONL, as far as it got.

    ``partial`` is the point of reading it this way. The log is appended row by row as the run
    goes, so a file that stops mid-way is a run that stopped mid-way — and that is exactly the
    case an operator most needs to see, because live pages and permanent records may already
    exist for the rows that did land.
    """

    path: Path
    outcomes: list[RunOutcome]
    modified: datetime | None
    unreadable_lines: int

    @property
    def ok(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "ok")

    @property
    def errors(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "error")

    @property
    def dry_run(self) -> bool:
        return bool(self.outcomes) and all(o.status == "dry-run" for o in self.outcomes)


def load_run(path: Path) -> RunLog:
    """Read one run log, keeping the rows that parse and counting the ones that do not.

    A truncated final line is normal for a run killed mid-write, and discarding the whole file
    over it would throw away the record precisely when it matters most.
    """
    outcomes: list[RunOutcome] = []
    unreadable = 0
    try:
        text = path.read_text(encoding="utf-8")
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return RunLog(path, [], None, 0)
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            outcomes.append(RunOutcome.model_validate_json(line))
        except ValueError:
            unreadable += 1
    return RunLog(path, outcomes, modified, unreadable)


def recent_runs(cid: str, limit: int = 20) -> list[RunLog]:
    """The most recent run logs, newest first.

    Sorted by modification time rather than by name: a same-second second run is named
    ``{ts}-1.jsonl``, which sorts *before* ``{ts}.jsonl`` because ``-`` precedes ``.``.
    """
    runs_dir = output_dir(cid) / "runs"
    if not runs_dir.is_dir():
        return []
    paths = sorted(runs_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [load_run(path) for path in paths[:limit]]


def _newest_run(cid: str) -> Path | None:
    """The most recent run log's path, without reading any of them."""
    try:
        paths = sorted((output_dir(cid) / "runs").glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return None
    return paths[-1] if paths else None


def rail_facts(cid: str | None, cfg: ClientConfig | None) -> dict[str, str]:
    """One short fact per rail entry: "have I done this yet?", answerable from any screen.

    **Facts, never ticks.** A green tick on Data because *an* export exists cannot tell you it is
    the *right* export, and a tick that lies is worse than no tick — the same reasoning that put
    scope rather than the catalogue count on gate 0. A date and a row count can be checked against
    what the operator believes; a checkmark can only be trusted or not.

    **Everything here must stay stat-cheap.** This runs on every render of every screen, so one
    subprocess would put a quarter-second on all seven — which is why the counts an operator
    really wants (units with copy, checks passing) are *not* here: they need the doctor, and they
    are already on the screens that own them. ``plan.summary.json`` is read because it is a small
    file written for exactly this, not because reading files is free.

    Preflight has no entry. It leaves no artifact, and there is no cheap way to say when it last
    ran — an empty fact is better than a misleading one.
    """
    if cid is None or cfg is None:
        return {}
    facts = {
        "Data": file_fact(cfg.export.path).age,
        "Content": file_fact(output_dir(cid) / "data" / "generation_results.json").age,
    }
    if (summary := load_plan_summary(cid)) is not None:
        facts["Publish"] = f"{summary.total} row{'' if summary.total == 1 else 's'}"
    newest = _newest_run(cid)
    facts["Runs"] = file_fact(newest).age if newest else "none yet"
    return facts


def mode_from(value: str | None) -> Mode:
    """Parse a mode name, defaulting to the least destructive one.

    Defaulting to ``pages`` rather than ``both`` is deliberate: an unreadable or absent choice
    must never resolve toward the mode that writes permanent records.
    """
    try:
        return Mode(value or "")
    except ValueError:
        return Mode.PAGES
