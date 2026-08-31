"""Write the process list: install an uploaded one, prune it, and put the upload back.

The control file is a list of GTINs and nothing more: being on it is the whole meaning, the tool
reads no cell values, and the operator prepares a batch by **choosing which rows run**. That is
the one step of the loop where a mis-click is expensive in the ordinary way — the wrong rows
publish, or the right rows do not.

So this module keeps four properties:

* **Every other column is preserved verbatim.** Only the GTIN column is configured; the rest are
  the operator's working notes, and a tool that dropped them would be taking away the reason they
  keep the file.
* **The previous version is kept.** A save writes ``{name}.bak.xlsx`` first. There is no undo in
  a web form, and losing a pruned list means redoing the pruning.
* **The upload is kept too, separately.** ``{name}.source.xlsx`` holds the file the operator sent,
  byte for byte, from the moment it arrives. ``.bak`` only ever holds *the previous save*, so
  after two saves the original is gone; the archive is what makes :func:`restore` mean "the list I
  uploaded" rather than "whatever it looked like last time". It is read for Restore and for
  display. **It never decides what gets written** — a design that derived the control file from it
  would put a wrong join between the operator and their own list, silently.
* **An empty result is refused.** ``load_process_list`` already treats zero GTINs as an error
  rather than an empty run, for the reason this project keeps designing against: an empty plan
  and a successful-looking no-op are indistinguishable. Saving an empty file here would just move
  that failure one step earlier.

Reading is :func:`lib.process_list.read_process_list` — the same call a run makes, not a second
opinion about the same file. Writing is openpyxl. No NiceGUI, so it is testable without a browser.
"""

from __future__ import annotations

import tempfile
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
    "restore",
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


def archive_path(control: Path) -> Path:
    """Where the uploaded list is kept, beside the control file it was installed as.

    Deliberately **not** ``{name}.bak.xlsx``. The backup holds the state before the most recent
    save; if the two collided, Restore would hand back the last pruned version rather than the
    list the operator uploaded — which is the one thing Restore exists to do.
    """
    return control.with_suffix(f".source{control.suffix}")


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
    kept = archive_path(control)
    kept.write_bytes(data)
    control.write_bytes(data)
    return kept


def restore(config: ProcessListConfig) -> Path:
    """Put the uploaded list back, keeping the current control file beside it. Returns the backup.

    What makes deselection reversible. Without it a row removed on screen survives only in
    ``.bak``, which the next save overwrites.

    Raises:
        ProcessListError: If no upload has been archived — there is nothing to restore, and
            silently doing nothing under a success message is the failure mode this project
            keeps designing against.
    """
    control = Path(config.path)
    kept = archive_path(control)
    if not kept.exists():
        raise ProcessListError(
            f"there is no uploaded scope list to restore: {kept} does not exist. It is written "
            f"when a list is uploaded, so a list that arrived any other way has no original here."
        )

    backup = control.with_suffix(f".bak{control.suffix}")
    if control.exists():
        backup.write_bytes(control.read_bytes())
    control.write_bytes(kept.read_bytes())
    return backup


def save_sheet(sheet: ProcessListSheet) -> Path:
    """Write the sheet back, keeping the previous version beside it. Returns the backup path.

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

    backup = sheet.path.with_suffix(f".bak{sheet.path.suffix}")
    if sheet.path.exists():
        backup.write_bytes(sheet.path.read_bytes())

    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append(sheet.header)
    for row in sheet.rows:
        worksheet.append(row)
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions
    workbook.save(sheet.path)
    workbook.close()
    return backup
