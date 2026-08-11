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
    return Scope(
        in_scope=in_scope,
        total=total,
        detail=str(entry.get("detail") or ""),
        empty=entry.get("status") == "fail",
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


def mode_from(value: str | None) -> Mode:
    """Parse a mode name, defaulting to the least destructive one.

    Defaulting to ``pages`` rather than ``both`` is deliberate: an unreadable or absent choice
    must never resolve toward the mode that writes permanent records.
    """
    try:
        return Mode(value or "")
    except ValueError:
        return Mode.PAGES
