"""The shape of a client's ``input/`` folder, and the note that explains it on the spot.

    input/{client}/
      process/
        uploads/    GS1 export/export.xlsx              the export a run reads
                    GS1 export/export-{stamp}.xlsx      every export ever uploaded
                    product-list.xlsx                   the list as the operator sent it
                    product-list-{stamp}.xlsx           every list ever uploaded
        selection/  selections.xlsx                     the selection a run reads
                    selection-{stamp}.xlsx              every selection ever saved
      reference/  test-uploads/  superseded/  videos/   nothing a run reads

``process/`` holds the two halves of one act: **what arrived**, and **what was chosen from it**.
Each document sits with its own history, so a person looking for "the list I sent in August" opens
``uploads/`` and a person asking "what did we publish last week?" opens ``selection/``.

Nothing in ``uploads/`` shares a name with anything in ``selection/``. The uploaded list is a
``product-list``; a chosen one is a ``selection``. They were both called "selection" for a while
and the folders stopped distinguishing themselves the moment they were.

**Live files keep fixed names** because ``clients.yml`` points at them and there is no
command-line override. A layout where runs took "the newest file in the folder" would mean any
stray copy — a Finder duplicate, a re-download landing as ``selections (1).xlsx`` — silently
becoming what the next run publishes, with nothing raised and the plan looking ordinary. A wrong
fixed path fails the other way: the count on the Data screen is wrong the moment you look.

**Everything is archived on the way *in*, never on the way out.** An upload is copied the moment
it lands; a selection is copied as it is saved. Archiving the file being *replaced* instead —
which is what this did first — gets two things wrong at once, and both are visible by counting the
files after one upload and two saves: the untouched upload gets filed as a selection nobody chose,
and the selection actually in force is the one list with no dated copy at all.

Both archives are permanent: ``input/`` is gitignored, so a delete cannot be undone, and nothing
here ever deletes.

A ``README.md`` is written into the client's folder so the layout explains itself to somebody
looking at it in Finder, who has no reason to have read any of this.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from lib.config import ClientConfig

#: The folder under a client that holds everything a run touches. Everything beside it —
#: reference workbooks, test uploads, videos — is there for a person, not for a run.
PROCESS_DIR: Final = "process"

#: The note the tool drops beside the files. Rewritten whenever it changes, so it cannot go stale
#: against the code that writes the files it describes.
README_NAME: Final = "README.md"

_README: Final = """# What is in this folder

Nothing here is edited by hand. The operator shell writes all of it.

Everything a run touches is under **`process/`**. It has two halves: what arrived, and what you
chose from it.

## `process/uploads/` — what you sent, kept forever

| File | What it is |
|---|---|
| `GS1 export/export.xlsx` | The GS1 Data Source export **a run reads**. |
| `GS1 export/export-{stamp}.xlsx` | Every export you ever uploaded, dated. |
| `product-list.xlsx` | Your product list exactly as you sent it, before any ticking. |
| `product-list-{stamp}.xlsx` | Every product list you ever uploaded, dated. |

## `process/selection/` — what you chose

| File | What it is |
|---|---|
| `selections.xlsx` | Your list minus the rows you unticked. **A run reads this.** |
| `selection-{stamp}.xlsx` | Every selection you saved, dated. The newest matches the live one. |

## The other folders

**`reference/`** — workbooks a *setup* step reads, not a run. The GS1 DIY sector datamodel lives
here; `build_brick_map --datamodel` turns it into the category map.

**`test-uploads/`** — files kept on purpose so there is always something safe to practise an
upload with. Nothing reads them.

**`superseded/`** — old inputs set aside before this layout existed, with a note of their own.

**`videos/`** — the video files and `mapping.yml`, at the paths `media.video_folders` names.

## The two rules worth knowing

**A run reads exactly two files:** `process/uploads/GS1 export/export.xlsx` and
`process/selection/selections.xlsx`. They are the two without a date. Everything else is history;
adding a file to any of these folders changes nothing, however new it is.

**Nothing here is ever deleted.** This folder is not in version control, so a delete would be
permanent. Every upload and every save is copied as it happens.

## Going back

The Data screen has **Start again from my uploaded file**, which puts
`process/uploads/product-list.xlsx` back as `process/selection/selections.xlsx` — no need to find
the original again. Nothing is lost: every selection you saved is already dated beside it.

## What a run keeps

A run copies the selection it used, and the list that selection came from, into its own folder
under `output/{client}/runs/{stamp}/`, next to its log and the per-row result sheet. Those copies
describe that run and are never touched by a later batch.
"""


def write_readme(cfg: ClientConfig) -> Path | None:
    """Drop the note beside the client's input files. Returns where, or ``None`` if there is no
    sensible place — a client whose export path has no parent to write into.

    Written by the tool rather than kept in ``docs/`` because the person confused by these files is
    looking at them in a file browser, not reading the repository. The condition the operator set
    when this layout was agreed was that it be intuitive *without* the documentation; a note in the
    folder is the version of documentation that is present at the moment of the question.
    """
    folder = _client_root(Path(cfg.export.path))
    if folder is None or not folder.is_dir():
        return None
    path = folder / README_NAME
    path.write_text(_README, encoding="utf-8")
    return path


def _client_root(path: Path) -> Path | None:
    """Walk up from a configured path to the client folder — the one holding ``process/``.

    Counting parents would work and would break the next time a document gains a subfolder of its
    own, which is exactly what the GS1 export just did. Looking for the named directory does not
    care how deep the file sits under it.
    """
    for parent in path.parents:
        if parent.name == PROCESS_DIR:
            return parent.parent
    return None
