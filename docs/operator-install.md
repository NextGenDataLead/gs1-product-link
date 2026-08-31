# Installing on the operator's machine

Two double-clicks, on a machine with no Python, no Homebrew and no developer tools.

| | |
|---|---|
| **macOS** | `install.command`, then `start.command` |
| **Windows** | `install.bat`, then `start.bat` |

That is the whole install. This page exists for the three things around it: what the maintainer
has to hand over besides the folder, the two ways a corporate machine refuses to run the file, and
what IT is being asked to allow.

> Developing on this repository instead? Use [`setup.md`](setup.md). **Do not run
> `install.command` in a development clone** — it replaces `.venv` with a Python 3.11 environment
> holding the `ui` extra and *not* `dev`, so `pytest` and `mypy` disappear from it.

---

## 1. Get the folder

The maintainer sends it — a zip, a shared drive, or a `git clone`. It goes anywhere the operator
can write: Desktop, Documents, wherever. Not inside a synced folder that rewrites files underneath
it, though; `output/` is written during a run.

**The folder must stay together.** Every path this tool uses is relative to it, so
`install.command` on its own, moved to the Desktop, installs nothing — it says so rather than
guessing.

## 2. Run the installer

Double-click `install.command` (macOS) or `install.bat` (Windows). It takes a few minutes on a
first run, and prints what it is doing.

It:

1. installs **`uv`** into `~/.local/bin` — a single static binary, unless one is already on the
   machine, in which case that one is used;
2. has `uv` fetch its own **CPython 3.11**, into `~/.local/share/uv/python` (macOS/Linux) or
   `%LOCALAPPDATA%\uv\data\python` (Windows);
3. builds **`.venv`** inside the folder from the committed `uv.lock`.

No administrator rights, nothing installed system-wide, and nothing written outside the folder and
the user's own home directory. The versions come from `uv.lock` rather than from a fresh
resolution, so this machine gets what was tested — the installer passes `--locked`, which means it
would rather stop than quietly install a different set.

Re-run it any time. It is idempotent, and re-running it is how you pick up an updated folder.

## 3. The five files that are not in the download

Everything in this list is gitignored, so none of it is in a zip or a clone. The maintainer sends
each one separately, and **a missing one does not always announce itself** — the first two stop the
shell dead, the last three fail in three different and much quieter ways.

| File | Goes | What it is | Missing it |
|---|---|---|---|
| `clients.yml` | top level, beside `install.command` | the site settings | Setup says the config did not load; nothing runs |
| `.env` | top level, `chmod 600` | the credentials | every live check fails; nothing runs |
| `input/{client}/process-list.xlsx` | the path in `process_list.path` | the barcodes this run may touch | Data shows a red band. It has an upload — see below |
| `output/{client}/state.json` | exactly there — the path is not configurable | **the ledger of what is already published** | **see the warning below. This is the expensive one.** |
| `input/{client}/videos/mapping.yml` | the path in `media.video_map_path` | which video belongs to which product | the preflight **fails** (`cannot read …`); the machine cannot reach a runnable state |

> **`state.json` is the one to get right.** It records every `(GTIN, language)` this tool has
> already published. Without it, a run classifies **every already-published GTIN as NEW** — a
> second WordPress page for each, and another **permanent** GS1 Digital Link record for each. A GS1
> record can never be deleted, only disabled. Nothing downstream notices: the plan looks like a
> normal first run, and the counts are the only clue.

`.env` should be `chmod 600`; see [`setup.md`](setup.md#secrets).

**If this client publishes video, the video files come too** — the folders named under
`media.video_folders`, usually several gigabytes, so they travel on a disk rather than by mail. The
mapping above is only the index to them. And with `media.restrict_to_mapped_gtins` on, a product
with no confirmed video in **every** language is silently out of scope: it never reaches the plan,
and the run reports success having skipped it.

### What arrives through the app, and what does not

Three files change hands every batch — the export, the scope list, and the generated copy. **All
three have an upload control**, so nothing needs copying into a folder by hand:

- **The GS1 Data Source export** — uploaded on **Data**. It replaces the configured `export.path`
  in place, keeping the previous file as `.bak.xlsx`.
- **The product scope list (`process-list.xlsx`)** — uploaded on **Data**, in its own section
  below the export. It is read before it is installed, so a file that will not open is refused
  while the list you were using is still there. Your upload is kept beside it as
  `process-list.source.xlsx`, and **Restore the uploaded list** puts it back — which is what makes
  unticking rows safe to get wrong.
- **`generation_results.json`** — uploaded on **Content**. Written fresh for each batch, not
  accumulated: a newer one replaces the run's copy rather than adding to it.

The state file below is the one that still travels by hand, and it is the one to be careful with.

> Until recently the scope list had no upload and the docs said so. If you learned this tool from
> an older copy of this page, that is the sentence to unlearn — along with the button that used to
> say *Remove selected rows*. **A tick now means keep.**

### Returning the ledger

`state.json` travels **both ways**. After a publish from the operator's machine, that copy knows
things the maintainer's does not — new page ids, new URLs, new GS1 link hashes — and it is the only
record of them.

**The machine that published owns the file.** Send it back after every wave, and let it overwrite
the other copy rather than merging by hand. Two divergent ledgers is how the same product gets
published twice, which costs a duplicate page and a second permanent GS1 record. If you are unsure
which copy is newer, the safe move is to check the live site before running anything — see
[`verifying-live.md`](verifying-live.md).

The human-readable live log (`docs/clients/{client}-live-log.md`) is a convenience, not the source
of truth. Where the two disagree, `state.json` wins: it is what the pipeline actually wrote.

## 4. Start it

Double-click `start.command` / `start.bat`. A desktop window opens on `127.0.0.1:8477` — no
browser tab, no URL anyone else can reach.

**[`operator-guide.md`](operator-guide.md) takes it from here** — the walkthrough of a batch,
screen by screen, with screenshots. Read that next.

`./start.command --browser` serves the same pages in a browser instead, for a machine where the
webview will not open.

---

## When the machine refuses to open the file

Both are the operating system doing its job on an unsigned file downloaded from elsewhere, and
both are one-time.

**macOS — "cannot be opened because it is from an unidentified developer".** Right-click (or
Control-click) the file → **Open** → **Open** in the dialog. That records an exception for that
file; double-clicking works from then on. From a terminal the equivalent is
`xattr -dr com.apple.quarantine /path/to/the/folder`.

**Windows — "Windows protected your PC".** **More info** → **Run anyway**.

**If the machine is managed and the dialog offers no way through,** it is MDM policy rather than
Gatekeeper or SmartScreen, and no incantation gets around it. The options are to have IT sign and
notarise the two scripts, to have IT package the install, or to run the four commands by hand once
(they are the contents of `install.command` — a `curl`, a `uv python install`, and a `uv sync`).

## When the downloads are blocked

The installer fetches from `astral.sh` (the `uv` binary), `github.com` (the CPython build) and
`pypi.org` (the packages). On a network that blocks the middle one, point `uv` at an internal
mirror before running it:

```bash
export UV_PYTHON_INSTALL_MIRROR=https://internal.mirror.example/python-build-standalone
./install.command
```

For PyPI the equivalent is `UV_DEFAULT_INDEX`. If all three are blocked, the install has to be
prepared on a machine that can reach them and copied over whole — `.venv` included.

---

## For IT

The security posture of the running application is in
[`ui-operator-shell.md`](ui-operator-shell.md#for-it) — loopback-only socket, no telemetry, no
auto-update, and no Anthropic egress or LLM credential unless the variable named by the client's
`generator.api_key_env` is deliberately given a value (a fresh install does not). What the
*install* adds to that:

- **Everything downloaded is version-pinned and reviewable.** `uv` is pinned to an exact version
  in both installers; the Python build is 3.11, the same one CI runs the test suite on; every
  package comes from `uv.lock`, which is committed — 86 packages with hashes, in the repository,
  vettable before anything is installed.
- **User-scope only.** No administrator rights, no service, no scheduled task, no PATH change
  beyond `uv`'s own line in the user's shell profile.
- **The credentials predate this.** `.env` holds a WordPress application password with editor
  rights and GS1 production OAuth credentials, plaintext at mode 600. That is how the tool has
  always worked from a terminal; the installer neither improves nor worsens it, and a secret
  manager is the answer if that is the blocker.
- **One unconstrained outbound behaviour**, worth knowing about: when publishing, the tool fetches
  product images from whatever URLs the GS1 feed carries, with no allowlist.

---

## For maintainers

**After changing `pyproject.toml`, run `uv lock` and commit the result.** Otherwise the operator's
`uv sync --locked` refuses to install — deliberately, because the alternative is that machine
silently getting different versions from everyone else. `tests/test_packaging.py` catches it
offline and CI's `uv lock --check` catches it properly.

The uv version and the Python version are written out in `install.command`, `install.bat`,
`start.command`, `start.bat` and `.github/workflows/ci.yml`. To bump either, change every copy —
the same test fails if one moves alone. There is no `.python-version` file on purpose: pyenv reads
that file too, and it would break `python` in this directory for anyone who has pyenv without the
pinned version installed.
