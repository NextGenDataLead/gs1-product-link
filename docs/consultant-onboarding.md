# Start here — setting this up and running your first batch

You are taking over the running of a tool that publishes Noviplast product pages to a live
WordPress site and registers their barcodes with GS1. This page takes you from an empty machine to
a verified first batch. It works the same on **Windows** and **macOS**; where they differ, both are
given.

Read it once end to end before doing anything. It is about an hour of work, most of it waiting.

> ### The one thing to know before anything else
>
> **A GS1 Digital Link record can never be deleted.** A WordPress page can be edited or deleted
> afterwards. A GS1 record cannot — once a barcode has one, it exists permanently; it can be
> emptied and switched off, never removed. This client is configured against **production**, so
> every GS1 write you make is real and permanent from your very first run.
>
> The tool asks you to confirm before it writes one, every time. Nothing here happens by accident,
> but nothing here is undone either.

---

## What is yours and what is the tool's

This is the division that decides whether a run works, and it is the part no software can check
for you.

| | Who does it |
|---|---|
| Producing the GS1 Data Source export, in the right shape, at the right path | **You**, outside the app — §5 |
| Producing the video mapping file and the video folders | **You**, outside the app — §6 |
| Deciding which products this batch covers (the process list) | **You**, in the app |
| Fixing wrong or blank product data | **You**, in **MyGS1** — never in the export file, never in the tool |
| Reading the spreadsheet, planning, publishing, registering barcodes | The tool |
| Deciding what has already been published | The tool's ledger — §4, and it is the file to get right |

The tool never guesses. If something it needs is missing, the product is **held** — quietly left
out of the batch — rather than published half-finished. That is the behaviour you want, and it is
also why a batch can come out much smaller than you expected without anything being wrong.

---

## 1. What you need from the maintainer before you start

Six things. Chase all six before you begin; four of them are not in the repository and cannot be
downloaded.

- [ ] **Access to the GitHub repository** (`NextGenDataLead/gs1-product-link`) — or a zip of it.
- [ ] **`clients.yml`** — the site settings.
- [ ] **`.env`** — the credentials.
- [ ] **`output/noviplast/state.json`** — the ledger of what is already published. **The expensive
      one.** See §4.
- [ ] **`input/noviplast/process-list.xlsx`** — which barcodes a batch may touch.
- [ ] **The video files** — several gigabytes, so they arrive on a disk, not by mail.

You will also need, from Noviplast rather than from the maintainer: a fresh **GS1 Data Source
export** and their **video-to-product mapping**. Those two are §5 and §6.

---

## 2. Get the folder

Put it somewhere you can write and that is **not** inside OneDrive, iCloud Drive, Dropbox or any
other syncing folder. The tool writes files while it runs, and a sync client rewriting them
underneath it causes failures that look like bugs.

**Windows** — install [Git for Windows](https://git-scm.com/download/win) (accept every default),
then open **Command Prompt** and run, one line at a time:

```
cd %USERPROFILE%\Documents
git clone https://github.com/NextGenDataLead/gs1-product-link.git
```

**macOS** — open **Terminal**. It will offer to install the developer command-line tools the first
time you type `git`; accept.

```
cd ~/Documents
git clone https://github.com/NextGenDataLead/gs1-product-link.git
```

You now have a folder called `gs1-product-link`. **Everything below happens inside it**, and every
path this tool uses is relative to it — so it has to stay in one piece and keep its name.

*No GitHub access?* Ask for a zip instead and unzip it into the same place. You lose the ability to
pull updates, which then arrive as a new zip.

---

## 3. Install

Open the folder and double-click:

| | |
|---|---|
| **Windows** | `install.bat`, then later `start.bat` |
| **macOS** | `install.command`, then later `start.command` |

That is the whole install. It takes a few minutes the first time and prints what it is doing. It
needs no administrator rights and installs nothing system-wide: it fetches a small tool called
`uv`, has that fetch its own Python, and builds a `.venv` folder inside the project from a
version-locked list. Safe to run again at any time.

**The machine will probably refuse the first time.** Both refusals are the operating system doing
its job on an unsigned file, and both are one-time:

- **Windows — "Windows protected your PC".** Click **More info** → **Run anyway**.
- **macOS — "cannot be opened because it is from an unidentified developer".** Right-click (or
  Control-click) the file → **Open** → **Open** in the dialog.

If the machine is centrally managed and the dialog offers no way through, that is IT policy, not
something to work around — [`operator-install.md`](operator-install.md#when-the-machine-refuses-to-open-the-file)
has what to ask them for.

---

## 4. Put the handed-over files in place

Create the folders if they do not exist. Paths are relative to the project folder.

| File | Exact path |
|---|---|
| `clients.yml` | `clients.yml` — top level, beside `install.bat` |
| `.env` | `.env` — top level. On macOS, `chmod 600 .env` |
| The ledger | `output/noviplast/state.json` — **exactly there; this path is not configurable** |
| Process list | `input/noviplast/process-list.xlsx` |

The process list is a spreadsheet whose only meaningful column is headed **`Barcode`**. Every other
column is yours to use however you like — the tool reads none of them, and there is no status
column to tick. **Being on the list is the whole meaning**, so you prepare a batch by *deleting the
rows that should not run*.

> ### `state.json` is the one to get right
>
> It records every barcode-and-language this tool has already published. **Without it, a run
> classifies every already-published product as new** — a second WordPress page for each, and
> another *permanent* GS1 record for each. Nothing warns you: the plan looks like a perfectly
> normal first run, and the row count is the only clue.
>
> Take a copy of it before your first run and keep it. It is a small text file, and it is the only
> record of what exists.

`.env` holds live production credentials in plain text. Treat the whole folder accordingly: do not
put it in a shared drive, do not mail the file on, and tell the maintainer immediately if the
machine is lost.

**Two things have no upload button and are copied by hand: the process list and the ledger.** The
Data screen only *edits* a process list that is already on disk. Looking for an upload for either
is time wasted.

---

## 5. Preparing the GS1 Data Source export — your job

This is the input the whole run is built on, and the tool will not repair a bad one.

### Where it goes

```
input/noviplast/products.xlsx
```

Exactly that name, exactly that folder, `.xlsx`. (It is `export.path` in `clients.yml` if you ever
need to confirm it.) There is no flexibility here: a file called `products (1).xlsx` or
`Noviplast_export_2026.xlsx` is not found, and the tool says the file is missing rather than
guessing.

Once you are set up you can also drop the new file on the **Data** screen's upload area, which puts
it at that path for you and keeps the previous one beside it as `products.bak.xlsx`.

### What to export from MyGS1

A **GS1 Data Source / GDSN datapool export** — the multi-worksheet one, not a flat article list.
The difference matters: a flat list has one sheet with a header row on top; the datapool export has
one worksheet per GDSN module (`TradeItemDescription`, `MarketingInformation`,
`TradeItemMeasurements`, `BrickGPCCommercialData`, `ReferencedFileDetailInformation`, …) with
**seven header rows and the data starting on row eight**. This tool is configured for the second
one. If you export the wrong kind, the parse fails immediately rather than half-working.

Include:

- **All four target markets** — `528` (NL), `056` (BE), `276` (DE), `442` (LU). The tool consults
  them in that order and takes the first non-blank value per product, per field, per language, so
  the extra markets are coverage, not noise.
- **Both languages, nl and fr.** Every market row carries every language; which market happens to
  hold a French value varies product by product, which is exactly why all four are wanted.
- **Consumer units.** Only rows whose unit descriptor is `BASE_UNIT_OR_EACH` are read — cases and
  pallets are ignored, so exporting them costs nothing.

### The attributes a product cannot publish without

If any of these is blank for a product, that product is **held** out of the batch. Not an error —
a decision, and a silent one unless you look. The fix is always in MyGS1, never in the file.

| Attribute | What it is | Where it lands |
|---|---|---|
| **3301** | Functional name | The page title |
| **3336** | Brand | |
| **3510** | Net content | Technische details |
| **3498 / 3520 / 3492** | Height / width / depth | Technische details |
| **GpcCategoryCode** | GPC brick | The site category |
| **2485** | Product image URL | The page's hero image |

Marketing copy (**1083** marketing message, **1067** feature/benefit) is not mandatory, but it is
what the generated text is written *from* — a product with neither gets thinner copy.

### What not to do to the file

Once it is exported, treat it as read-only:

- **Do not open it and "Save As"**, and do not save it from Numbers, Google Sheets or LibreOffice.
  Round-tripping can drop the header rows or change the sheet names, and both are how the file is
  identified.
- **Do not rename, reorder or delete worksheets**, and do not delete any of the seven header rows.
- **Do not sort, filter-and-save, or tidy up columns.**
- **Do not fix values in the spreadsheet.** A blank or wrong value gets fixed in **MyGS1** and
  re-exported. Anything typed in here is silently correct once and wrong forever after, because the
  next export overwrites it. This rule has no exceptions.
- **Do not convert to `.xls` or `.csv`.**

### Check it before anything else

In the app: **Data → Check the parse (writes nothing)**, then **Parse and save products.json**.
Then **Data quality** at the bottom of the same screen, which builds a report of everything blank,
inconsistent between markets, or in the wrong language. That report is your MyGS1 worklist, and it
is meant to be sent to Noviplast.

> **A new export invalidates the marketing copy.** The generated text is fingerprinted against the
> product data it was written from, so when the data changes, the old copy no longer matches and
> those products are dropped from the plan. Preflight catches this and says so — but expect to redo
> **Content** after every new export, not just after the first.

---

## 6. Preparing the video mapping — your job

Noviplast supplies video files named by marketing name. Which video belongs to which product is not
in the GS1 feed and cannot be worked out reliably from the filenames, so it is a file a human
confirms. You are that human.

> **This file decides how big a batch can be.** A product needs a confirmed video in **both**
> languages before it can be published at all. One without is not an error and not a warning — it
> is simply never in the batch. If a run comes out at 17 products when you expected 37, this file
> is the first place to look.

### The video files

```
input/noviplast/videos/NL/     <- the Dutch videos
input/noviplast/videos/FR/     <- the French videos
```

- **The folder names are case-sensitive.** `NL`, not `nl` or `Nl`.
- Flat — files go directly in, not in sub-folders.
- Recognised extensions: `.mpg` `.mpeg` `.mp4` `.mov` `.m4v` `.webm`. Anything else is invisible to
  the tool.
- Copy them off the delivery disk; do not leave them on it and do not point at a network share.
  They are read during a run.

### The mapping file

```
input/noviplast/videos/mapping.yml
```

A plain text file. One block per language, one line per video:

```yaml
nl:
  - {file: "Bulbman.mpg", gtin: "8713195007434"}
  - {file: "Aqua Mat v2.mp4", gtin: ""}
  - {file: "Corporate intro.mpg", gtin: "skip"}
fr:
  - {file: "Bulbman FR.mpg", gtin: "8713195007434"}
  - {file: "Aqua Mat v2 FR.mp4", gtin: ""}
```

The rules, all of which have bitten someone:

- **`file` must match the filename on disk exactly** — every space, every capital, the extension
  included. `Bulbman.mpg` and `bulbman.MPG` are two different files as far as this is concerned.
- **Always quote the filename.** These names contain spaces, `&`, `+`, brackets and occasionally a
  colon, and an unquoted colon silently changes the meaning of the line.
- **`gtin: ""` means "nobody has decided yet"** — a gap.
- **`gtin: "skip"` means "this video belongs to no product"** — a decision, not a gap. Use it for
  corporate intros and unrelated footage, or they nag forever.
- **13 or 14 digits both work.** The tool pads them.
- **One product, one video, per language.** If the same barcode is confirmed to two files in one
  language, the tool cannot tell which you meant and treats it as unmapped.
- Two-space indentation, one row per line. Lines starting `#` are comments and are preserved.

### Turning Noviplast's delivery into this file

They will have sent a spreadsheet or a list, not YAML. The route:

1. **Put the video files in the two folders first.** Everything below is checked against what is
   actually on disk.
2. **Get every file listed.** Open the app → **Video mapping** (below the line in the left strip)
   → **Add files that are on disk but not in the mapping**. That appends a row per new file with
   an empty barcode, and leaves every existing row alone.
   *Starting completely from scratch instead?* See §10 for the one command that drafts the whole
   file. It **overwrites** `mapping.yml`, so it is only for a first draft — never once real
   answers are in there.
3. **Fill in the barcodes.** Two ways, and the spreadsheet way is much faster for a long list:
   - **In the app**, one file at a time: click a row, read the suggestions, type or click a
     barcode, then **Save the mapping**. It keeps the previous version as `mapping.yml.bak`.
   - **In Excel**, if Noviplast's delivery is already a list of filename + barcode. Put filenames
     in column A and barcodes in column B, and build the lines with a formula:
     ```
     ="  - {file: """&A2&""", gtin: """&B2&"""}"
     ```
     Fill it down, paste the result under the right language heading in `mapping.yml`, and save as
     **plain text** (not as a spreadsheet).
4. **Check what you produced** — §7.

### Reading the suggestions

Both the app and the candidate report offer ranked guesses. They are guesses: the filenames are
English marketing names, and the product feed is Dutch and French. Two things will look wrong and
are not:

- **The best match is usually against a French field.** This feed's English sits in the *French*
  slots, so a filename like `DrainSticks_NL.mpeg` matches `Drain Sticks 12pc` — which is stored as
  the product's French logistics name. The report shows you the value that matched and which field
  it came from, precisely so a high score next to an unfamiliar name reads as an answer rather than
  a bug.
- **The product's short name is not its recognisable name.** The feed's own name for a product is
  `siliconenbak` or `bezem`. What identifies it to someone looking at a video is the marketing or
  logistics name — `Noviplast Siliconenbak silicone groen`, `Drain Sticks 12pc`. The report shows
  all of them.

A score is a similarity, not a verdict. Anything below about 0.8 is usually a coincidence.

---

## 7. Check the setup before you touch anything live

Everything so far is reversible. This is the point to prove it is right.

1. **Start the app** — double-click `start.bat` / `start.command`. A window opens. If it will not,
   `start.command --browser` serves the same pages in a browser instead.
2. **Check the header.** It names the client and the environment. **PRODUCTION in red** is expected
   here — it means every GS1 record this tool writes is real.
3. **Go to Preflight and press "Run everything, including credentials".** It reads only, and
   writes nothing. It checks the config, how many products are actually in scope and what is
   removing the rest, whether the copy covers them, the process list, the category and video
   mapping, and that the website and GS1 both accept the credentials.
4. **Read the scope sentence** and satisfy yourself the numbers are what you expect. It reads like
   *"37 of 127 product(s) in the export are in scope. 20 of those are held for want of a confirmed
   video in every language, so a run would publish 17"* — three different numbers, and the last one
   is the size of your batch. A gap between the first two is almost always the video mapping.

The equivalent from a terminal, which gives the same answer in more detail, is `doctor` — §10.

Do not go further until Preflight says **Ready**.

---

## 8. Your first batch

Work down the four numbered screens in the left strip, in order: **Data → Content → Preflight →
Publish**. [`operator-guide.md`](operator-guide.md) walks through each screen with screenshots, and
is the page to have open while you do it.

For your *first* batch, do it in this order regardless of what the guide says is possible:

1. **Cut the process list down to two or three products.** Not the whole batch. Data → select rows
   → Remove selected rows → Save the list. Keep the full list somewhere first.
2. **Run in `pages` mode.** Pages are reversible; GS1 records are not. Take the dry run seriously —
   read the output before pressing Proceed.
3. **Look at the actual pages on the live site.** Not the run log: the pages. Open each one in a
   browser, in both languages, and check the title, the text and the image are really there. A
   successful-looking run does not prove the page content landed.
4. **Only then run `links`** for the same products, which registers the barcodes permanently.
5. **Check a barcode resolves**: open `https://id.gs1.org/01/{the 14-digit barcode}` in a browser
   and confirm it lands on the right page.
6. **Restore the full process list**, and repeat at full size.

> **If you publish pages now and register the links later, do them close together.** A `pages`-only
> run updates the website but leaves the GS1 record holding whatever title it had before, and
> nothing warns you about the mismatch. After changing product content, plan to run `links` too.

---

## 9. After every batch

- **Send `output/noviplast/state.json` back to the maintainer.** It is the record of everything
  published and it only exists on the machine that did the publishing. Two out-of-step copies is
  how a product gets published twice — one duplicate page, and one more permanent GS1 record.
- **Send the data-quality report to Noviplast** if it found anything. That is their MyGS1 worklist.
- **Note what went live**, in whatever the maintainer keeps for the purpose.

---

## 10. A terminal, for the three things not in the app

Almost everything is on a screen. Three things are not, and all three are one line.

**Opening a terminal in the project folder:**

- **Windows** — open the project folder in File Explorer, click in the address bar, type `cmd` and
  press Enter.
- **macOS** — open Terminal, type `cd ` (with the space), then drag the project folder onto the
  window and press Enter.

Then, replacing `PY` with `.venv\Scripts\python` on Windows or `.venv/bin/python` on macOS:

| What | Command |
|---|---|
| **The video candidate report** — every file and every mapping row in one spreadsheet, with ranked suggestions. This is the sheet to work through, and the one to send Noviplast when you need them to decide. | `PY -m scripts.report_video_candidates` |
| **The video coverage check** — how many gaps are left, and where. | `PY -m scripts.build_video_map --check` |
| **The full preflight**, in more detail than the screen gives. | `PY -m scripts.doctor` |

The candidate report lands at `output/noviplast/video-map-candidates.xlsx`. Add `--top-n 5` for
more suggestions per file, or `--format csv` if you prefer.

One more, for a **first** draft of `mapping.yml` only — it **overwrites the file**, so never run it
once real answers are in there:

```
PY -m scripts.build_video_map > input/noviplast/videos/mapping.yml
```

---

## Never do these

- **Never fix product data in the export file.** MyGS1, then re-export. Every time.
- **Never run a `links` or `both` publish twice** because you were not sure it worked. Check the
  **Runs** screen instead. Those records cannot be deleted.
- **Never run `install.bat` / `install.command` inside the maintainer's development copy** of this
  project, if you are ever sent one. It replaces the environment there.
- **Never edit `output/noviplast/state.json` by hand**, and never merge two copies of it.
- **Never test whether a barcode resolves with a tool that sends a HEAD request** (some link
  checkers do). GS1's resolver answers 404 to HEAD and works perfectly in a browser. It is not
  broken.
- **Never assume a green run means the page is right.** Open the page.

---

## Where everything else is

| | |
|---|---|
| Running a batch, screen by screen, with screenshots | [`operator-guide.md`](operator-guide.md) |
| Installing, and the files that arrive by hand | [`operator-install.md`](operator-install.md) |
| What an error message means | [`troubleshooting.md`](troubleshooting.md) |
| How the export is read, attribute by attribute | [`data-source-export-schema.md`](data-source-export-schema.md) |
| Video, images and the WordPress side | [`wordpress-onboarding.md`](wordpress-onboarding.md) |

**A note on names.** Some of the documentation refers to the client as `democlient`. That is a
sanitised example name. In this installation the client is **`noviplast`**, and that is what you
type wherever a client name is asked for — including in the production confirmation box on the
Publish screen.
