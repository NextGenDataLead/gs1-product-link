# Handing this over: the maintainer's briefing

For the person doing the handing over. The consultant's own page is
[`consultant-onboarding.md`](consultant-onboarding.md) — send them that; this one is yours.

It covers what to decide before the session, what to send, how to run the session, the eight things
they will get wrong (each one has cost this project real time), and how to check they are actually
ready.

---

## 1. What you are handing over

Not a spreadsheet tool. A tool that writes to a **live** WordPress site and to the **production**
GS1 resolver, where records are permanent. The consultant needs to hold three ideas from the first
five minutes:

1. **GS1 records cannot be deleted.** Only emptied and disabled.
2. **The ledger (`state.json`) is the whole memory.** Lose it and everything already published
   looks new — which means a duplicate page and a second permanent record, per product, with no
   warning.
3. **Blank data is fixed in MyGS1, never downstream.** The tool holds a product rather than
   publishing it half-finished, and that is the feature, not a fault.

Everything else they can look up.

## 2. Decide these six things before the session

None has a default. Each one is easier to settle now than after something has gone wrong.

**Which WordPress credential they get.** `.env` currently holds one application password for
`automation-bot`. Create a **second** application password for the consultant instead of sharing
yours: WordPress supports several per user, and a separate one can be revoked the day the
engagement ends without breaking your own. Costs two minutes.

**What to do about the GS1 credentials.** OAuth2 client credentials are per *account*, not per
person, so there is no per-consultant equivalent — you are handing over the real ones. Decide up
front whether you rotate them at the end of the engagement, and put a date on it.

**Whether the Anthropic key travels.** With `ANTHROPIC_API_KEY` set, their machine writes the
marketing copy itself and bills you for it. Without it, the machine makes no calls to Anthropic at
all and you write the copy and send them a `generation_results.json` per batch. The second is
cheaper and slower and keeps a review point in your hands. Pick one deliberately — do not let the
answer be "whatever was in the file I copied".

**Who publishes.** This is the important one, and it is not a technical question. Whichever machine
publishes owns `state.json` from that moment, and **two people publishing from two copies is the
one failure mode that costs permanent records.** The workable arrangements are:
- *They publish, you do not.* Cleanest. You get the ledger back after each batch.
- *You publish, they only prepare data.* Also clean, and the safest during the first weeks.
- *Both* — only with an explicit rule about who holds the ledger this week, written down.

**Whether they get repository access or a zip.** Access means they can pull fixes; a zip means you
hand over a new one each time. Either is fine. If it is access, add them as a collaborator before
the session, because it will not be quick on the day.

**How much of the batch they may run unsupervised, and from when.** Suggest: first two batches in
`pages` mode with you on a call, then unsupervised.

## 3. The pack to send

Four files are gitignored, so none of them is in a clone or a zip, and **a missing one does not
always announce itself**.

| What | Where you get it | Where it goes | If it is missing |
|---|---|---|---|
| `clients.yml` | your copy | project root | Nothing runs; the app says the config did not load |
| `.env` | your copy, edited per §2 | project root | Every live check fails |
| `output/noviplast/state.json` | **your most recent copy** | exactly that path | **Everything republishes as new.** Silent |
| `input/noviplast/process-list.xlsx` | your copy | that path | Red band on the Data screen; no upload exists for it |
| The video files | the delivery disk | `input/noviplast/videos/NL` and `/FR` | Products silently out of scope |

Send `.env` over something that is not email. Say out loud that it holds production credentials in
plain text.

**Before you send `state.json`, make sure it is the newest one.** If you are not certain, run the
site comparison (**Runs → Does the site match the ledger?**) on your own machine first and settle
it there. Sending a stale ledger is the most expensive mistake available in this handover.

## 4. A session plan

Two and a half hours, in this order. The order matters: each block is verifiable before the next
one starts, so a misunderstanding surfaces immediately rather than at the first publish.

| | Block | What "done" looks like |
|---|---|---|
| 20 min | **Why any of this exists.** A barcode leads a phone to a product page. Show them one working: scan a real Noviplast product, land on the page. Then show `id.gs1.org/01/...` in a browser. | They can explain what the tool produces without using the word "publish" |
| 15 min | **The three rules** from §1. Do not soften them. | They repeat the `state.json` consequence back to you unprompted |
| 30 min | **Clone and install**, on their machine, while you watch. Expect the Windows SmartScreen dialog. | The app window opens and the header reads **PRODUCTION** in red |
| 15 min | **Put the four files in place** together. Point at `state.json` and say what it is again. | Preflight runs and reports a scope figure |
| 30 min | **The export.** Have them produce a fresh one from MyGS1 *during the session*, drop it on the Data screen, parse it, and read the data-quality report. | They can name three things that would hold a product |
| 30 min | **The video mapping.** Open the Video mapping screen. Generate the candidate report. Walk two or three real rows: read the score, the matched value, the field it came from. | They correctly explain why a French field won an English filename |
| 20 min | **A `pages` run on two products**, end to end, gates and all — then open the two pages in a browser together. | They looked at the page, not at the log |

Leave `links` for the second session. It is the irreversible half, and there is nothing to gain
from doing it while they are still finding the buttons.

## 5. The eight things they will get wrong

Every one of these has already cost this project time. Say each out loud during the session; a
document they have not read yet will not catch them.

1. **They will fix a blank value in the spreadsheet.** It is the obvious thing to do and it is
   wrong: the next export overwrites it, and nobody remembers the value was invented. MyGS1 or
   nothing.
2. **They will believe a green run means the page is right.** The custom-field write path fails
   *silently* — a `200` proves the page exists, not that its content landed. The habit to install:
   open the page.
3. **They will check a barcode with something that sends a HEAD request.** GS1's resolver answers
   404 to HEAD and 307 to GET, so a link checker reports every record broken. A browser is the
   test.
4. **They will read a small plan as a bug.** Products drop out for four legitimate reasons: not on
   the process list, unchanged since last time, no confirmed video in both languages, or missing
   mandatory data. The plan gate says which. Show them a drop happening on purpose.
5. **They will look for an upload button for the process list.** There is not one anywhere, and the
   Data screen's table only edits a file already on disk. Two minutes of confusion, every time,
   unless you say it first.
6. **They will name the video folders `nl` and `fr`.** The configured names are `NL` and `FR`, and
   the mismatch is invisible: no error, the folders simply read as empty, and every product goes
   quietly out of scope.
7. **They will publish pages after a data change and stop there.** A `pages`-only run leaves the
   GS1 record holding the *previous* title, and nothing anywhere warns about the mismatch. This one
   has actually happened here.
8. **They will find the docs saying `democlient`.** That is the sanitised example name; the live
   client id is `noviplast`, and it is what the production confirmation box wants typed in full.

## 6. Checking they are ready

Ask these six. They are all things a document cannot confirm.

- *What happens if you run a batch without `state.json`?* — Every published product republishes as
  new: a duplicate page each, and a second permanent GS1 record each.
- *A product is missing its width in MyGS1. What does the tool do, and what do you do?* — Holds it
  out of the batch, silently. Fix it in MyGS1 and re-export.
- *Which of the three publish modes can you undo?* — `pages`. Not the other two.
- *The plan has 17 rows and you expected 37. First place you look?* — The video mapping; a product
  needs a confirmed video in **both** languages.
- *You have just published pages for a product whose name changed. Are you finished?* — No: the GS1
  record still carries the old title until a `links` run.
- *You are not sure whether last night's run completed. What do you do?* — Runs → *Does the site
  match the ledger?*. Not run it again.

If any answer is shaky, that is the block to go back over — not the whole session.

## 7. The standing rules after the handover

Agree these explicitly, and write them into whatever you use to track the engagement.

- **The ledger comes back after every batch**, and the machine that published owns it. Never merge
  two copies by hand; if in doubt, compare against the live site.
- **Nothing is published on a Friday afternoon** unless someone is available on Monday. Half a
  batch is recoverable; half a batch that nobody notices for three days is worse.
- **Data-quality reports go to Noviplast**, not into a folder. They are the only route by which the
  source data actually improves.
- **New export → redo the copy.** Generated text is fingerprinted against the product data it was
  written from; when the data changes the copy goes stale and those products drop out of the plan.
  Preflight catches it, but only if they read it.
- **When something looks wrong, stop rather than retry.** Retrying is safe for pages and permanent
  for links.

## 8. When they break something

In rough order of likelihood:

**A duplicate page.** Reversible. Delete the extra page in WordPress and get the ledger back in
step with the site.

**A GS1 record written for the wrong product.** Not reversible. It can be retracted — links
cleared, record disabled — and that is the whole remedy. `run_unpublish` does it properly, including
marking the product so a later run cannot quietly republish it. Do not attempt this by hand in
MyGS1.

**A run that stopped half-way.** Normal and recoverable. The Runs screen shows exactly which rows
landed; re-running is safe for pages, and for links only after checking what already exists.

**Two divergent ledgers.** Stop all publishing on both machines. Compare each against the live site
before deciding which wins. Do not merge them.

For anything technical, [`troubleshooting.md`](troubleshooting.md) is the reference, and
[`verifying-live.md`](verifying-live.md) is how to establish what is actually true on the site
without changing it.

---

## Your own copy

Keep it. Two reasons: it is the fallback if their machine dies mid-engagement, and it is where the
code lives if anything needs fixing.

- **Do not run `install.command` / `install.bat` in your development clone.** It replaces `.venv`
  with an operator environment that has the app but not the test tools, and they disappear without
  saying so. Your clone is set up with `pip install -e ".[dev,ui]"` — see [`setup.md`](setup.md).
- **If you both hold the folder, only one of you holds the ledger.** See §2.
