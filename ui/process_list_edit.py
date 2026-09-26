"""Write the process list: install an uploaded one, prune it, and put the upload back.

The control file is a list of GTINs and nothing more: being on it is the whole meaning, the tool
reads no cell values, and the operator prepares a batch by **choosing which rows run**. That is
the one step of the loop where a mis-click is expensive in the ordinary way — the wrong rows
publish, or the right rows do not.

So this module keeps four properties:

* **Every other column is preserved verbatim.** Only the GTIN column is configured; the rest are
  the operator's working notes, and a tool that dropped them would be taking away the reason they
  keep the file.
* **Every upload is kept, dated.** ``uploads/selection-{stamp}.xlsx`` is written the moment a file
  arrives, before anything is derived from it. Dating them on the way *in* rather than on the way
  out is the difference between "every original you ever sent" and "the one before the current
  one": archiving on replacement means a file uploaded once and never replaced has no dated copy
  at all.
* **The current upload has a stable name.** ``uploaded.xlsx`` is the newest upload under a name
  that does not move, because two things need to find it without guessing — the Restore control on
  the Data screen, and ``scripts/report_scope_result.py``, which reads it to name the rows the
  operator deselected. Resolving that by "newest file in the folder" would make any stray copy
  dropped into the directory silently become the operator's original.
* **Every save is kept, dated.** ``selections/selection-{stamp}.xlsx`` is written *as the save
  happens*, holding what was chosen — the same rule as uploads, for the same reason. Archiving
  the file being **replaced** instead produced two wrong records: the first save filed the
  untouched upload as though it were a chosen selection, and the selection actually in force had
  no dated copy at all until a later save displaced it. Both are visible by counting — one list
  appeared twice under two meanings, and the current one appeared once.
* **``uploads/`` and ``selections/`` are kept apart.** An upload is the operator's own document;
  a selection is what they chose from it. Mixing them loses which is which exactly when somebody
  is looking for the original.
* **Neither archive decides what gets written.** A design that derived the control file from the
  upload would put a wrong join between the operator and their own list, silently.
* **An empty result is refused.** ``load_process_list`` already treats zero GTINs as an error
  rather than an empty run, for the reason this project keeps designing against: an empty plan
  and a successful-looking no-op are indistinguishable. Saving an empty file here would just move
  that failure one step earlier.

Reading is :func:`lib.process_list.read_process_list` — the same call a run makes, not a second
opinion about the same file. Writing is openpyxl. No NiceGUI, so it is testable without a browser.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import openpyxl

from lib.config import ProcessListConfig
from lib.errors import ProcessListError
from lib.process_list import ProcessListSheet, read_process_list

__all__ = [
    "ProcessListSheet",
    "archive",
    "archive_path",
    "read_sheet",
    "save_sheet",
]


def read_sheet(config: ProcessListConfig) -> ProcessListSheet:
    """Load the control file for display.

    A thin call through to :func:`lib.process_list.read_process_list`, which is what a run reads
    with. This used to be a second reader — openpyxl, header fixed at row 1 — and the two
    disagreed about exactly the files this project was built for: Strict Open XML, with a report
    title above the table. Those loaded in a run and failed on screen.

    Raises:
        ProcessListError: If the file cannot be opened, or no worksheet carries the configured
            GTIN column — phrased by the one reader, so the shell and the CLI say the same thing.
    """
    return read_process_list(config)


def _stamp() -> str:
    """A second-resolution stamp for an archived copy — readable, sortable, filename-safe."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S")


def uploads_dir(control: Path) -> Path:
    """``process/uploads/`` — everything the operator sent, derived from the live selection.

    The control file is ``process/selection/selections.xlsx``, so uploads are its aunt. Derived
    rather than configured because there is exactly one layout, and a second place to declare it
    is a second place for it to be wrong.
    """
    folder = control.parent.parent / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def archive_path(control: Path) -> Path:
    """The most recent uploaded list: ``process/uploads/product-list.xlsx``.

    It lives with the other uploads and not beside the live file, because it is not one: an upload
    is what the operator sent, a selection is what they chose from it. And it carries the word
    **list** rather than **selection**, so that nothing in ``uploads/`` shares a name with
    anything in ``selection/`` — the two folders hold different documents and a shared name is how
    that stops being obvious.

    Undated on purpose — two things have to find it without guessing, the Restore control and
    ``scripts.report_scope_result`` — while ``product-list-{stamp}.xlsx`` beside it keeps every
    one. Resolving it as "the newest file in the folder" would let any stray copy dropped there
    become the operator's original.
    """
    return uploads_dir(control) / f"product-list{control.suffix}"


def uploads_path(control: Path, stamp: str) -> Path:
    """Where an uploaded list is kept forever, dated: ``uploads/product-list-{stamp}.xlsx``.

    Named for the document the operator sent rather than for the control file it becomes, because
    this folder is read by a person looking for "the list I sent in August" and never by the tool.
    """
    return _unique(uploads_dir(control) / f"product-list-{stamp}{control.suffix}")


def selections_path(control: Path, stamp: str) -> Path:
    """Where a saved selection is kept forever, dated: ``selection/selection-{stamp}.xlsx``.

    Written when the save happens, not when the next save displaces it. ``.bak`` held exactly one
    version, so two saves lost the first; archiving-on-replacement fixed that and introduced a
    subtler error — the record was always one save behind, so the list actually in force was the
    one list with no dated copy.
    """
    # Beside the live selection, not in a folder of its own: these *are* the selection's history,
    # and one folder holding "what is chosen now" and "what was chosen before" is the smallest
    # arrangement that says so.
    return _unique(control.parent / f"selection-{stamp}{control.suffix}")


def _unique(candidate: Path) -> Path:
    """``candidate``, or the next free ``-1``, ``-2``… beside it.

    Two writes inside one second are ordinary here — untick a row, glance at the count, untick
    another — and a second-resolution name silently overwrote the first, so "every version is
    kept" kept one.

    Read these back **by modification time, never by name**: ``-1`` sorts *before* the unsuffixed
    name, because ``-`` precedes ``.``. That is the same trap the run logs carry, and it means a
    sorted listing shows a same-second pair the wrong way round.
    """
    if not candidate.exists():
        return candidate
    serial = 0
    while candidate.exists():
        serial += 1
        candidate = candidate.with_name(
            f"{candidate.stem.rsplit('-', 1)[0] if serial > 1 else candidate.stem}"
            f"-{serial}{candidate.suffix}"
        )
    return candidate


def archive(config: ProcessListConfig, data: bytes) -> Path:
    """Install an uploaded scope list: validate it, keep it, then make it the control file.

    The order is the point. The upload is written to a temporary file and read with the run's own
    reader first, so a file that would fail on the Preflight screen is refused while the operator
    is still looking at the upload button, and the list they were working from is untouched. Only
    then are the archive and the control file written — the archive first, so there is no window
    in which a run could read a control file the archive does not match.

    Args:
        config: The client's ``process_list`` configuration. Its ``path`` is the control file.
        data: The uploaded workbook, byte for byte.

    Returns:
        The path the upload was archived at.

    Raises:
        ProcessListError: If the upload will not read as a process list, or carries no GTINs.
            Nothing is written; the file on disk is exactly as it was.
    """
    control = Path(config.path)
    with tempfile.TemporaryDirectory() as folder:
        # A directory rather than NamedTemporaryFile: the reader re-opens the path by name, and
        # a still-open NamedTemporaryFile cannot be re-opened on Windows.
        candidate = Path(folder) / control.name
        candidate.write_bytes(data)
        sheet = read_process_list(
            ProcessListConfig(path=str(candidate), gtin_column=config.gtin_column)
        )
    if not sheet.listed_gtins():
        raise ProcessListError(
            f"refusing to install a scope list with no GTINs under its "
            f"{config.gtin_column!r} column: the next run would plan nothing and report success. "
            f"The list you were using has not been touched."
        )

    control.parent.mkdir(parents=True, exist_ok=True)
    # Dated first, then the stable name, then the control file. The order matters for the same
    # reason it always has here: there must be no window in which a run could read a control file
    # that no archive matches.
    uploads_path(control, _stamp()).write_bytes(data)
    kept = archive_path(control)
    kept.write_bytes(data)
    control.write_bytes(data)
    return kept


def restore_from_upload(config: ProcessListConfig) -> int:
    """Put the operator's own upload back as the control file. Returns its row count.

    The way back from a selection gone wrong. It was re-uploading the file, which is fine when the
    file is to hand and useless when it is in someone's sent items — and it is the same act either
    way, since the archive *is* the upload, byte for byte.

    Nothing is archived on the way out, because there is nothing left to archive: every selection
    the operator saved is already dated under ``selections/``, and a control file they never saved
    is the upload itself.

    Raises:
        ProcessListError: If no upload has been archived for this client, or it will not read.
            Nothing is written in either case.
    """
    control = Path(config.path)
    kept = archive_path(control)
    if not kept.is_file():
        raise ProcessListError(
            f"there is no archived upload at {kept} to restore from — this list was not "
            f"uploaded through this screen. Upload it again to start from your own file."
        )
    sheet = read_process_list(ProcessListConfig(path=str(kept), gtin_column=config.gtin_column))
    control.write_bytes(kept.read_bytes())
    return len(sheet.rows)


def _seed_archive(control: Path) -> None:
    """Make sure the list **as it arrived** is kept, before a save prunes it.

    An upload does this already: ``uploaded.xlsx`` holds the operator's own file from the moment
    it lands, so pruning can always be undone. A control file placed by hand — the CLI path, or a
    client set up before this screen existed — has no such copy, and with saves archiving *what
    was chosen* rather than what they replaced, its unpruned rows would be gone after one save.

    So the pre-save file is filed as an upload, which is what it is: the list as it arrived, just
    not through the picker. Filing it as a *selection* instead would be the error this model was
    changed to fix — recording a choice nobody made.

    Does nothing once an archive exists, so it can never overwrite a real upload with a pruned
    version of itself.
    """
    if not control.is_file():
        return
    kept = archive_path(control)
    if kept.exists():
        return
    data = control.read_bytes()
    uploads_path(control, _stamp()).write_bytes(data)
    kept.write_bytes(data)


def save_sheet(sheet: ProcessListSheet) -> Path:
    """Write the sheet back, and keep a dated copy of **what was saved**. Returns that copy.

    The dated copy is of the new content, not of the file being replaced. Counting the files after
    one upload and two saves is what shows why: replacing-and-archiving filed the untouched upload
    under ``selections`` — a choice nobody made — while the list actually in force had no dated
    copy at all. Archiving what is chosen, when it is chosen, gives one dated file per save and
    none per non-save.

    The header is frozen and filtered, because the file leaves here and goes back to a
    spreadsheet: the operator's next act on it is to sort or filter, and a rewritten file that
    lost that is a rewritten file they have to set up again.

    Raises:
        ProcessListError: If no row carries a GTIN. Saving an empty control file would produce an
            empty plan and a run that reports success having published nothing — the exact
            failure the zero-GTIN check in ``load_process_list`` exists to prevent, one step
            earlier and with the operator's own pruning already lost.
    """
    if not sheet.listed_gtins():
        raise ProcessListError(
            "refusing to save a process list with no GTINs: the next run would plan nothing "
            "and report success"
        )

    _seed_archive(sheet.path)

    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append(sheet.header)
    for row in sheet.rows:
        worksheet.append(row)
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions
    workbook.save(sheet.path)
    workbook.close()

    # The same bytes, dated. Written from the file rather than saved twice, so the copy cannot
    # differ from what a run will read.
    kept = selections_path(sheet.path, _stamp())
    kept.write_bytes(sheet.path.read_bytes())
    return kept
