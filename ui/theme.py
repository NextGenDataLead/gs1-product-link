"""The shell's visual language, and the layout every screen sits inside.

**Direction: Swiss / International, as a control room.** Flush-left, dense, typographic. A
generous type-scale contrast does the hierarchy so almost nothing needs a border, and colour is
reserved for status — never decoration. Light ink-on-paper by default, because this is a tool used
in an office in daylight, and dark mode is an option rather than a personality.

Two rules the palette exists to serve:

* **Permanence is red, and only permanence.** A run that writes GS1 records shows a red band the
  whole time it is in flight. If red also meant "a field is invalid" or "this button is primary",
  the band would stop being read.
* **Nothing important is colour alone.** Every status carries a word as well as a hue, so the
  report survives a monochrome screen, a screenshot in a ticket, and colour-blindness.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final

from nicegui import ui

#: How often a running button repaints its elapsed count. One second: fast enough to read as
#: alive, slow enough that nobody watches the digits instead of the log.
_TICK_SECONDS: Final = 1.0

#: Design tokens. Defined once, referenced by name — no hardcoded hexes in the screens.
TOKENS: Final = """
:root {
  color-scheme: light dark;

  --ink:        oklch(21% 0.012 260);
  --ink-soft:   oklch(46% 0.010 260);
  --ink-faint:  oklch(64% 0.008 260);
  --paper:      oklch(98.5% 0.003 260);
  --surface:    oklch(100% 0 0);
  --rule:       oklch(90% 0.005 260);

  /* Semantic only. See the module docstring. */
  --ok:         oklch(52% 0.13 155);
  --warn:       oklch(60% 0.14 75);
  --danger:     oklch(54% 0.20 27);
  --accent:     oklch(45% 0.13 250);

  --font-sans:  "Inter", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-mono:  ui-monospace, "SF Mono", "JetBrains Mono", Menlo, monospace;

  --text-micro: 0.6875rem;
  --text-small: 0.8125rem;
  --text-base:  0.9375rem;
  --text-lead:  1.0625rem;
  --text-title: clamp(1.35rem, 1.1rem + 0.8vw, 1.9rem);
  --text-hero:  clamp(2rem, 1.4rem + 2.2vw, 3rem);

  --space-1: 0.25rem; --space-2: 0.5rem;  --space-3: 0.75rem;
  --space-4: 1rem;    --space-6: 1.5rem;  --space-8: 2rem;   --space-12: 3rem;

  --duration: 160ms;
  --ease: cubic-bezier(0.16, 1, 0.3, 1);
}

@media (prefers-color-scheme: dark) {
  :root {
    --ink:       oklch(93% 0.006 260);
    --ink-soft:  oklch(72% 0.008 260);
    --ink-faint: oklch(55% 0.008 260);
    --paper:     oklch(17% 0.012 260);
    --surface:   oklch(21% 0.012 260);
    --rule:      oklch(30% 0.010 260);
    --ok:        oklch(70% 0.14 155);
    --warn:      oklch(78% 0.13 75);
    --danger:    oklch(68% 0.17 27);
    --accent:    oklch(72% 0.11 250);
  }
}

body { background: var(--paper) !important; color: var(--ink) !important;
       font-family: var(--font-sans) !important; font-size: var(--text-base) !important; }

.shell        { display: grid; grid-template-columns: 15rem 1fr; min-height: 100vh;
                position: relative; }

/* Reachable by keyboard, invisible until it has focus. The rail is seven links deep, so without
   this every screen costs seven tab stops before the content it is about. */
.skip         { position: absolute; left: var(--space-4); top: -4rem; z-index: 20;
                background: var(--surface); color: var(--ink); border: 1px solid var(--rule);
                padding: var(--space-2) var(--space-4); text-decoration: none;
                font-size: var(--text-small); transition: top var(--duration) var(--ease); }
.skip:focus   { top: var(--space-4); }

.rail         { border-right: 1px solid var(--rule); padding: var(--space-6) 0;
                background: var(--surface); }
.rail-brand   { padding: 0 var(--space-6) var(--space-6); }
.rail-title   { font-size: var(--text-small); font-weight: 620; letter-spacing: 0.02em; }
.rail-sub     { font-size: var(--text-micro); color: var(--ink-faint);
                font-family: var(--font-mono); }
.rail-link    { display: flex; gap: var(--space-3); align-items: baseline;
                padding: var(--space-2) var(--space-6); color: var(--ink-soft);
                text-decoration: none; transition: color var(--duration) var(--ease); }
.rail-link:hover  { color: var(--ink);
                    background: color-mix(in oklab, var(--accent) 7%, transparent); }
.rail-link.active { color: var(--ink); font-weight: 600;
                    box-shadow: inset 3px 0 0 var(--accent); }
.rail-num     { font-family: var(--font-mono); font-size: var(--text-micro);
                color: var(--ink-faint); min-width: 1.1rem; }
.rail-text    { display: flex; flex-direction: column; gap: 0; min-width: 0; }
/* One stat-cheap fact per step, so "have I done Data yet?" is answerable from any screen. A
   fact and not a tick: see `ui.context.rail_facts`. */
.rail-fact    { font-size: var(--text-micro); color: var(--ink-faint);
                font-family: var(--font-mono); }
/* The tools are not steps. The heading and the rule above it are the whole distinction between
   "the batch you are running" and "this machine", so they carry more weight than they look. */
.rail-group   { margin-top: var(--space-6); padding: var(--space-4) var(--space-6) var(--space-1);
                border-top: 1px solid var(--rule); font-size: var(--text-micro);
                letter-spacing: 0.14em; text-transform: uppercase; color: var(--ink-faint); }
.rail-toggle  { display: none; background: none; border: 1px solid var(--rule);
                color: var(--ink-soft); font-size: var(--text-base); line-height: 1;
                padding: var(--space-1) var(--space-3); cursor: pointer; }

/* Narrow only. Above this the rail is always open and nothing costs a click; below it the rail
   used to stack as a full-width block with no way to get past it to the screen. */
@media (max-width: 55rem) {
  .shell      { grid-template-columns: 1fr; }
  .rail       { border-right: none; border-bottom: 1px solid var(--rule);
                padding: var(--space-4) 0 var(--space-2); }
  .rail-brand { padding-bottom: var(--space-2); }
  .rail-toggle{ display: inline-flex; }
  .rail-links { display: none; }
  .rail.rail-open .rail-links { display: block; }
}

/* align-items: stretch overrides NiceGUI's items-start on the content wrapper, so a card is as
   wide as the column rather than as wide as its longest line. */
.canvas       { padding: var(--space-8) var(--space-12) var(--space-12); max-width: 68rem;
                align-items: stretch; }
@media (max-width: 55rem) { .canvas { padding: var(--space-6) var(--space-4); } }

.eyebrow      { font-family: var(--font-mono); font-size: var(--text-micro);
                letter-spacing: 0.14em; text-transform: uppercase; color: var(--ink-faint); }
.title        { font-size: var(--text-title); font-weight: 640; letter-spacing: -0.018em;
                line-height: 1.1; margin: var(--space-2) 0 var(--space-3); }
.lede         { font-size: var(--text-lead); color: var(--ink-soft); max-width: 44rem;
                line-height: 1.5; }
.section      { margin-top: var(--space-8); }

/* A section's heading line: an optional step number, the title, and the ⓘ that holds the
   explanation. A row rather than a bare label so all three sit on one baseline and the ⓘ reads
   as belonging to the heading rather than to the first control under it. */
.head-row     { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2);
                padding-bottom: var(--space-2); border-bottom: 1px solid var(--rule);
                margin-bottom: var(--space-4); }
.head-row .section-head { border: 0; padding: 0; margin: 0; }
/* The opened explanation is a sibling of the heading row, never a child of it. Inside a flex row
   it shared the line with its own title: `flex-basis: 100%` looks like it would wrap, but
   `max-width` clamps the hypothetical size the line-breaking uses, so it fitted and did not. */

/* The number is the whole navigational claim: do this one, then that one. Filled, so it reads as
   a position in a sequence and not as a count of something. */
.step-num     { flex: 0 0 auto; width: 1.45rem; height: 1.45rem; border-radius: 50%;
                background: var(--ink); color: var(--paper); font-size: var(--text-micro);
                font-weight: 700; display: grid; place-items: center;
                font-variant-numeric: tabular-nums; }

/* Hover shows it, press keeps it. Both, because hover does not exist on a touch screen or a
   keyboard, and this is where the sentence explaining the step now lives. */
.info-dot     { flex: 0 0 auto; width: 1.15rem; height: 1.15rem; border-radius: 50%;
                border: 1px solid var(--ink-faint); color: var(--ink-faint); background: none;
                font-family: var(--font-serif); font-size: 0.72rem; font-weight: 700;
                line-height: 1; cursor: help; display: grid; place-items: center;
                transition: color var(--duration) var(--ease),
                            border-color var(--duration) var(--ease); }
.info-dot:hover, .info-dot:focus-visible { color: var(--accent); border-color: var(--accent); }
.info-dot[aria-expanded="true"] { color: var(--accent); border-color: var(--accent); }
.explain      { font-size: var(--text-small); color: var(--ink-soft); line-height: 1.55;
                max-width: 48rem; margin: calc(-1 * var(--space-2)) 0 var(--space-4);
                padding-left: var(--space-3); border-left: 2px solid var(--rule); }
.section-head { font-size: var(--text-small); font-weight: 640; letter-spacing: 0.01em;
                padding-bottom: var(--space-2); border-bottom: 1px solid var(--rule);
                margin-bottom: var(--space-4); }
.note         { font-size: var(--text-small); color: var(--ink-soft); line-height: 1.55;
                max-width: 48rem; }
.mono         { font-family: var(--font-mono); font-size: var(--text-small); }

.figure       { font-size: var(--text-hero); font-weight: 300; letter-spacing: -0.03em;
                line-height: 1; font-variant-numeric: tabular-nums; }
.figure-label { font-size: var(--text-micro); color: var(--ink-faint);
                letter-spacing: 0.09em; text-transform: uppercase; }

.check        { display: grid; grid-template-columns: 4.5rem 1fr; gap: var(--space-4);
                padding: var(--space-3) 0; border-bottom: 1px solid var(--rule); }
.tag          { font-family: var(--font-mono); font-size: var(--text-micro); font-weight: 600;
                letter-spacing: 0.06em; text-transform: uppercase; }
.tag-ok    { color: var(--ok); }
.tag-warn  { color: var(--warn); }
.tag-fail  { color: var(--danger); }
.tag-na    { color: var(--ink-faint); }
.remedy    { font-size: var(--text-small); color: var(--ink-soft); margin-top: var(--space-1);
             padding-left: var(--space-3); border-left: 2px solid var(--rule); }

/* A form row: label left, control right, reason underneath. The reason is part of the row and
   not a tooltip — every field on the Setup screen has a wrong value that costs a live mistake,
   and a control with no stated consequence is one an operator changes to see what happens. */
.field        { display: grid; grid-template-columns: 12rem minmax(0, 1fr); gap: var(--space-4);
                padding: var(--space-2) 0; align-items: baseline; }
.field-label  { font-size: var(--text-small); color: var(--ink-soft); padding-top: var(--space-2); }
.field-hint   { font-size: var(--text-micro); color: var(--ink-faint); line-height: 1.55;
                max-width: 44rem; }
.field-edited > .field-label { color: var(--accent); font-weight: 600; }
@media (max-width: 55rem) { .field { grid-template-columns: 1fr; gap: var(--space-1); } }

.band         { padding: var(--space-3) var(--space-4); font-size: var(--text-small);
                border-left: 3px solid; line-height: 1.5; }
.band-danger  { border-color: var(--danger); color: var(--danger);
                background: color-mix(in oklab, var(--danger) 8%, transparent); }
.band-warn    { border-color: var(--warn);
                background: color-mix(in oklab, var(--warn) 10%, transparent); }
.band-quiet   { border-color: var(--rule); color: var(--ink-soft);
                background: color-mix(in oklab, var(--ink) 3%, transparent); }
.band-link    { display: inline-block; margin-top: var(--space-2); color: inherit;
                font-weight: 600; text-underline-offset: 0.2em; }

/* An aside the operator can open when they want it and ignore when they do not. A sentence that
   only matters the first time still costs a line of reading on every visit afterwards; behind
   this it costs a glance. Sized down deliberately — it must not compete with the control it is
   explaining, which is the thing on the screen that actually does something. */

/* A row of jumps to the sections below it. Only the Setup screen has enough of them to need it. */
.jumps        { display: flex; flex-wrap: wrap; gap: var(--space-1) var(--space-4);
                margin-top: var(--space-6); font-size: var(--text-small); }
.jumps a      { color: var(--ink-soft); text-underline-offset: 0.2em; }
.jumps a:hover{ color: var(--ink); }

/* A bordered panel. A publish gate is one, and inherits its box from here — three screens were
   reaching for `.gate` when all they wanted was a card, which made the class name a lie about
   what the thing on screen was. */
.card, .gate  { border: 1px solid var(--rule); background: var(--surface);
                padding: var(--space-6); width: 100%; }
.gate-step    { font-family: var(--font-mono); font-size: var(--text-micro);
                color: var(--ink-faint); letter-spacing: 0.08em; }
.gate-title   { font-size: var(--text-lead); font-weight: 620; margin: var(--space-1) 0; }
.gate-why     { font-size: var(--text-small); color: var(--ink-soft); line-height: 1.55;
                max-width: 46rem; margin-bottom: var(--space-4); }
/* Rendered from markdown, so it arrives wrapped in paragraphs that would otherwise inherit the
   browser's own spacing and pull away from the title above them. */
.gate-why p       { margin: 0 0 var(--space-2); }
.gate-why p:last-child { margin-bottom: 0; }
.gate-why code    { font-family: var(--font-mono); font-size: 0.92em;
                    background: color-mix(in oklab, var(--ink) 6%, transparent);
                    padding: 0.05em 0.3em; }
.gate-done    { opacity: 0.55; }

.console      { font-family: var(--font-mono); font-size: var(--text-micro); line-height: 1.6;
                background: oklch(19% 0.012 260); color: oklch(92% 0.01 260);
                padding: var(--space-4); overflow: auto; max-height: 26rem;
                white-space: pre-wrap; word-break: break-word; }

.cmd          { font-family: var(--font-mono); font-size: var(--text-micro);
                color: var(--ink-soft); background: color-mix(in oklab, var(--ink) 5%, transparent);
                padding: var(--space-2) var(--space-3); overflow-x: auto; white-space: pre; }

.scroll-x     { overflow-x: auto; max-width: 100%; }
"""


@dataclass(frozen=True)
class Screen:
    """One rail entry: what it is called, where it goes, and how its own heading opens."""

    label: str
    route: str
    #: The heading eyebrow. ``"Step 3"`` for a wave screen; the screen's own standing for a tool.
    eyebrow: str


#: **The batch.** These four are the loop an operator repeats, in the order they repeat it, and
#: the numbers say so.
#:
#: **The order is an assertion, so it has to be true.** Preflight used to sit ahead of Data and
#: Content — yet four of its checks answer "Run `parse_export` first", which is the Data screen.
#: It told you to go and do a later step and come back, and on a machine being set up from
#: scratch most of the list could not answer its own questions yet. The doctor's headline is
#: "N of M in scope", a statement *about the export just loaded*, so it belongs after the
#: loading rather than before. The credential half is not lost by moving it: the Setup screen's
#: Test buttons run those same checks, at the moment the field is edited.
#:
#: Load this batch's inputs · review its copy · check *this batch* · publish it.
WAVE: Final = (
    Screen("Data", "/data", "Step 1"),
    Screen("Content", "/content", "Step 2"),
    Screen("Preflight", "/preflight", "Step 3"),
    Screen("Publish", "/publish", "Step 4"),
)

#: **Not the batch.** Setup is configured once and then left alone, Runs is read afterwards, and
#: the video mapping is one input file's editor. Numbering these 1-6 alongside the four above
#: said they were one sequence, which buried the work an operator actually repeats between
#: machine configuration at one end and history at the other. They keep a permanent place in the
#: rail — a click away, never behind one — because a tool nobody can find is a tool nobody uses.
TOOLS: Final = (
    Screen("Setup", "/", "This machine"),
    Screen("Runs", "/runs", "History"),
    Screen("Video mapping", "/videos", "Video mapping"),
)

#: Every screen the rail reaches. The contract test checks this against the registered routes in
#: both directions, so a screen added to one and not the other fails the build.
NAV: Final = WAVE + TOOLS


def eyebrow(label: str) -> str:
    """A screen's heading eyebrow — ``"Step 3"``, or ``"History"`` — read from :data:`NAV`.

    Every screen used to spell its own number into ``theme.heading``, which meant the rail and
    the headings were two lists that had to be renumbered together. They are one list now: a
    reorder is an edit to :data:`WAVE` and nothing else.

    This was called ``step`` while all six screens were numbered. It stopped being a step the
    moment Setup and Runs left the numbering, and a function that returns ``"This machine"`` from
    something called ``step`` is the kind of small lie that later gets believed.

    Args:
        label: The rail label of the screen.

    Returns:
        The eyebrow, or an empty one if the label is not in the rail — a missing eyebrow is a
        cosmetic loss, and raising here would take a screen down over one.
    """
    for screen in NAV:
        if screen.label == label:
            return screen.eyebrow
    return ""


def install() -> None:
    """Add the tokens to every page. Called once at startup.

    ``shared=True`` because every route is a ``@ui.page``: without it NiceGUI refuses the call
    outright rather than quietly styling only the auto-index page, which is the right refusal —
    a shell where one screen has the design system and the others do not would be worse than one
    with none.
    """
    ui.add_css(TOKENS, shared=True)


@contextmanager
def page(
    active: str,
    *,
    client_id: str | None,
    environment: str | None,
    facts: Mapping[str, str] | None = None,
) -> Iterator[None]:
    """The shell every screen is rendered inside: left rail, then a canvas.

    The client and environment sit in the rail rather than on each screen, because "which client,
    which environment" is the question whose wrong answer is most expensive and least visible —
    and it must be answerable without navigating anywhere.

    Args:
        active: The rail label of the screen being rendered.
        client_id: The configured client, or ``None`` when the config cannot say.
        environment: ``production`` or the test environment, shown as a tag.
        facts: One short fact per rail label, from :func:`ui.context.rail_facts`. Passed in
            rather than read here, so the theme keeps importing nothing but NiceGUI and cannot
            grow a way to run a subprocess on every page load.
    """
    facts = facts or {}

    def link(screen: Screen, *, numbered: bool) -> None:
        current = screen.label == active
        anchor = ui.link(target=screen.route).classes(
            "rail-link active" if current else "rail-link"
        )
        if current:
            # `.active` is a class, which tells a sighted operator where they are and a screen
            # reader nothing at all.
            anchor.props("aria-current=page")
        with anchor:
            if numbered:
                ui.label(screen.eyebrow.removeprefix("Step ")).classes("rail-num")
            with ui.element("div").classes("rail-text"):
                ui.label(screen.label)
                if fact := facts.get(screen.label):
                    ui.label(fact).classes("rail-fact")

    with ui.element("div").classes("shell"):
        ui.link("Skip to content", "#canvas").classes("skip")
        rail = ui.element("nav").classes("rail")
        with rail:
            with ui.element("div").classes("rail-brand"):
                toggle = (
                    ui.element("button")
                    .classes("rail-toggle")
                    .props(
                        'aria-label="Show navigation" aria-expanded=false aria-controls=rail-links'
                    )
                )
                with toggle:
                    ui.label("☰")
                ui.label("GS1 Digital Link").classes("rail-title")
                ui.label(client_id or "no client configured").classes("rail-sub")
                if environment:
                    colour = "tag-fail" if environment == "production" else "tag-na"
                    ui.label(environment.upper()).classes(f"rail-sub tag {colour}")

            opened = False

            def show_or_hide() -> None:
                # Only reachable below 55rem, where the CSS hides the links. Above it the button
                # is `display: none`, so this never fires and the rail is never in either state.
                nonlocal opened
                opened = not opened
                rail.classes(add="rail-open") if opened else rail.classes(remove="rail-open")
                toggle.props(f"aria-expanded={'true' if opened else 'false'}")

            toggle.on("click", show_or_hide)

            with ui.element("div").classes("rail-links").props("id=rail-links"):
                for screen in WAVE:
                    link(screen, numbered=True)
                ui.label("This machine").classes("rail-group")
                for screen in TOOLS:
                    link(screen, numbered=False)

        with ui.element("main").classes("canvas").props("id=canvas"):
            yield


def heading(eyebrow: str, title: str, lede: str = "") -> None:
    """A screen's opening: what step this is, what it does, and why in one line."""
    ui.label(eyebrow).classes("eyebrow")
    ui.label(title).classes("title")
    if lede:
        ui.label(lede).classes("lede")


@contextmanager
def section(
    title: str,
    *,
    anchor: str | None = None,
    collapsed: bool = False,
    step: int | None = None,
    explain: str = "",
) -> Iterator[None]:
    """A titled block of a screen.

    Args:
        title: The heading, which is also what a doc has to call it for an operator to find it.
        anchor: An ``id`` to jump to, for a screen long enough to need :func:`jumps`.
        collapsed: Start folded. For a section nobody can act on — the Setup screen's read-only
            block is eight of its own sections' worth of scroll between the operator and the Test
            buttons, and none of it is editable here.
        step: A position in a sequence, shown as a filled numeral before the title. For a screen
            that is a procedure rather than a set of facts: the number is the instruction to do
            this one before that one, and it is the cheapest navigation there is.
        explain: The paragraph that would otherwise sit under the heading, moved behind an ⓘ.
            See :func:`explanation` for why anything is hidden at all.
    """
    if collapsed:
        expansion = ui.expansion(title).classes("section w-full").props("dense")
        if anchor:
            expansion.props(f"id={anchor}")
        with expansion:
            yield
        return
    element = ui.element("section").classes("section")
    if anchor:
        element.props(f"id={anchor}")
    with element:
        subhead(title, step=step, explain=explain)
        yield


def subhead(title: str, *, step: int | None = None, explain: str = "") -> None:
    """A heading line — optional step numeral, title, optional ⓘ — with the ⓘ's text below it.

    The heading is a flex row and the explanation is its **sibling**, not its child. It was a child
    for one round and shared the line with its own title: ``flex-basis: 100%`` reads as "wrap me",
    but ``max-width`` clamps the hypothetical size flex uses to break lines, so it fitted beside
    the title and stayed there.

    Used by :func:`section` and directly by a screen that needs the same heading below a section —
    the Data screen's two tables are one section with two headed halves.
    """
    with ui.element("div").classes("head-row"):
        if step is not None:
            ui.label(str(step)).classes("step-num")
        ui.label(title).classes("section-head")
        dot = _info_dot(explain, about=title) if explain else None
    if dot is not None:
        _reveals(dot, explain)


def explanation(text: str, *, about: str) -> None:
    """An ⓘ that shows its text on hover and keeps it on press.

    **Both, deliberately.** Hover answers it without a click for an operator already reaching past
    it; the press is what makes it reachable at all on a touch screen and by keyboard, where there
    is no hover. A tooltip alone would put this screen's only account of what a file is somewhere a
    keyboard cannot go.

    It is here rather than inlined at each call site because it carries a decision about *what*
    gets hidden. Only text that is **true every time and needed once** — which of the two uploads
    this is, what the path has to be, why a barcode is missing. Never a warning, never a count,
    never anything that is true only today: something an operator must not miss cannot live behind
    an affordance they have to discover. Those stay on the page, in a band.

    Args:
        text: The explanation. One or two sentences — anything longer belongs in a doc.
        about: What it explains, used to label the control for a screen reader, which otherwise
               announces every one of these identically as "i".
    """
    _reveals(_info_dot(text, about=about), text)


def _info_dot(text: str, *, about: str) -> ui.element:
    """The ⓘ itself: a real button, so it is focusable, with the text also on hover."""
    dot = (
        ui.element("button")
        .classes("info-dot")
        .props(f'type=button aria-expanded=false aria-label="About {about}"')
    )
    with dot:
        ui.label("i")
    dot.tooltip(text)
    return dot


def _reveals(dot: ui.element, text: str) -> None:
    """Put ``text`` in the current container, hidden, and let ``dot`` toggle it."""
    body = ui.label(text).classes("explain")
    body.set_visibility(False)

    shown = False

    def toggle() -> None:
        nonlocal shown
        shown = not shown
        body.set_visibility(shown)
        dot.props(f"aria-expanded={'true' if shown else 'false'}")

    dot.on("click", toggle)


def jumps(targets: list[tuple[str, str]]) -> None:
    """Links to the sections of a long screen, as ``(label, anchor)``."""
    with ui.element("div").classes("jumps"):
        for label, anchor in targets:
            ui.link(label, f"#{anchor}")


def band(text: str, kind: str = "quiet") -> None:
    """A single-line statement that must not be missed. ``kind``: quiet | warn | danger."""
    ui.label(text).classes(f"band band-{kind}")


def blocked(text: str, *, link_label: str, route: str) -> None:
    """A band that names what is wrong and offers the screen that fixes it.

    Five screens said "Fix that on the Setup screen first" as plain text. An operator who does not
    already know the rail is a navigation is told to go somewhere and given no way to go there —
    and this band is shown at exactly the moment the config is too broken for the rail's facts to
    render, which is the worst moment to be relying on the operator's sense of the layout.
    """
    with ui.element("div").classes("band band-danger"):
        ui.label(text)
        ui.link(link_label, route).classes("band-link")


#: How long a toast stays up, by how much the operator loses if they miss it. A success is
#: confirming something they just watched happen; a failure is the only account of why nothing
#: did, and is often long enough to need reading twice.
_NOTIFY_MS: Final = {"positive": 5000, "warning": 10000, "negative": 15000}


def _notify(text: str, kind: str) -> None:
    ui.notify(text, type=kind, timeout=_NOTIFY_MS[kind], multi_line=True, close_button="Dismiss")


def notify_ok(text: str) -> None:
    """It worked, and here is what it did."""
    _notify(text, "positive")


def notify_warning(text: str) -> None:
    """It did not run, and the reason is something the operator can change."""
    _notify(text, "warning")


def notify_problem(text: str) -> None:
    """It failed. Longest on screen, because this is the only place the reason is said.

    The screens reached for ``ui.notify`` directly for a long time, with four different timeouts,
    two spellings of the type argument and no ``close_button`` — so the message that mattered most
    was the one most likely to have already vanished. One helper per outcome, and the duration is
    a property of the outcome rather than of whoever wrote the call.
    """
    _notify(text, "negative")


def figure(value: str, label: str) -> None:
    """A number worth reading at a glance, with its unit spelled out beneath."""
    with ui.column().classes("gap-0"):
        ui.label(value).classes("figure")
        ui.label(label).classes("figure-label")


#: Status → the class that colours it, and the word that carries it without colour.
_CHECK_TAG: Final = {"ok": "tag-ok", "warn": "tag-warn", "fail": "tag-fail", "n/a": "tag-na"}
_CHECK_WORD: Final = {"ok": "ok", "warn": "warn", "fail": "FAIL", "n/a": "—"}


def check_row(status: str, title: str, detail: str, remedy: str = "") -> None:
    """One preflight check, rendered the same way wherever it appears.

    The Preflight screen shows every check and the Setup screen shows the two or three that
    answer the field the operator just edited. Same data, same source, so it renders here rather
    than twice — a check that looked different in two places would read as two different checks.
    """
    with ui.element("div").classes("check w-full"):
        ui.label(_CHECK_WORD.get(status, status)).classes(f"tag {_CHECK_TAG.get(status, 'tag-na')}")
        with ui.column().classes("gap-1"):
            ui.label(title).classes("font-medium")
            ui.label(detail).classes("note")
            if remedy:
                ui.label(remedy).classes("remedy")


def command(argv: list[str]) -> None:
    """Show the exact command being run.

    On every screen that runs something, without exception. An operator who can see the command
    can reproduce it in a terminal, check it against the docs, or paste it to somebody who knows
    — none of which is possible if the shell only reports what it decided to say about the result.
    """
    ui.label(" ".join(["python", *argv])).classes("cmd")


def action(label: str, on_click: Callable[[], object], *, danger: bool = False) -> ui.button:
    """A button, which shows for as long as its work lasts that the work is happening.

    ``danger`` — filled red — is reserved for the actions that actually **write**: the real run,
    the production confirmation, saving the pruned process list. Nothing else, ever. Red on a
    merely-negative choice like *Cancel* would put four red buttons on this screen and the one
    that matters would stop standing out, which is the only job red has here.
    """
    button = ui.button(label).props("no-caps unelevated")
    button.props("color=negative" if danger else "color=primary")
    return _while_running(button, on_click)


def quiet_action(label: str, on_click: Callable[[], object]) -> ui.button:
    """A secondary choice — declining, going back, asking for more. Outlined, not filled."""
    button = ui.button(label).props("no-caps outline color=grey-8")
    return _while_running(button, on_click)


def _while_running(button: ui.button, handler: Callable[[], object]) -> ui.button:
    """Disable the button while its handler runs, and say how many seconds that has been.

    **This is the fix for a publish that ran twice.** ``run_execute`` prints one line when it starts
    and one when it finishes, so a twenty-row run leaves the console silent for about ninety
    seconds. Nothing disabled the button and nothing said the work had begun, so the operator
    reasonably concluded it had not worked and clicked again — two complete runs over twenty live
    pages. No damage that time: pages are matched by slug and ``meta.gtin`` and updated in place.
    The same second click in ``links`` or ``both`` mode is aimed at records that can never be
    deleted.

    It lives here rather than at the call sites because there are two dozen of them, and a guard on
    the execute button alone would leave the defect on the other twenty-three. Every screen inherits
    it without an edit, including the buttons added after this was written.

    Two things it deliberately is not. Not a **progress bar** — ``run_execute`` reports every ten
    rows by design, so a bar would be fake precision on a twenty-row run; the seconds are the honest
    version of the same reassurance. And not a **page-wide lock**: it disables the button that was
    clicked, which is what actually happened, and locking a whole screen raises a question about
    what a redraw does mid-run that no incident has asked yet.

    On its own it cannot make a *blocking* handler feel any different. :func:`ui.runner.run` holds
    the event loop, so every UI change queued before it — including this one — reaches the browser
    only once the command has already finished. That half is fixed in the screens, with
    :func:`ui.runner.run_off_the_loop`; without it the spinner below would never animate.
    """
    # Quasar hides the label while `loading` is set and renders this slot centred over the button
    # instead, so the seconds have to be short enough to fit a width the label chose. No colour on
    # either: they inherit the button's, which is white on a filled one and grey on an outlined one.
    with button.add_slot("loading"):
        ui.spinner(size="1.1em", color=None)
        counter = ui.label("0s").classes("ml-2 mono")

    seconds = 0

    def tick() -> None:
        # The same deleted-element guard as the restore below, for the same reason: a redraw
        # during a run takes the counter with it, and the timer is cancelled a moment later.
        nonlocal seconds
        seconds += 1
        if not counter.is_deleted:
            counter.text = f"{seconds}s"

    timer = ui.timer(_TICK_SECONDS, tick, active=False)

    async def guarded() -> None:
        # Handlers here are a mix of sync and async — `on_click` takes `Callable[[], object]` — so
        # the result is awaited when it is awaitable and taken as done when it is not.
        nonlocal seconds
        seconds = 0
        counter.text = "0s"
        button.disable()
        button.props("loading")
        timer.activate()
        try:
            result = handler()
            if inspect.isawaitable(result):
                await result
        finally:
            timer.deactivate()
            # Restored in a `finally`, so a handler that raises does not leave the button dead —
            # but only if the button still exists. Several handlers end in a redraw, which deletes
            # and rebuilds every element on the screen; touching a deleted one warns loudly in
            # NiceGUI, and there is nothing to restore, since its replacement is already up.
            if not button.is_deleted:
                button.props(remove="loading")
                button.enable()

    button.on_click(guarded)
    return button
