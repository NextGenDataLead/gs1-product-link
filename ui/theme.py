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

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Final

from nicegui import ui

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

.shell        { display: grid; grid-template-columns: 15rem 1fr; min-height: 100vh; }
@media (max-width: 55rem) { .shell { grid-template-columns: 1fr; } }

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

.gate         { border: 1px solid var(--rule); background: var(--surface);
                padding: var(--space-6); width: 100%; }
.gate-step    { font-family: var(--font-mono); font-size: var(--text-micro);
                color: var(--ink-faint); letter-spacing: 0.08em; }
.gate-title   { font-size: var(--text-lead); font-weight: 620; margin: var(--space-1) 0; }
.gate-why     { font-size: var(--text-small); color: var(--ink-soft); line-height: 1.55;
                max-width: 46rem; margin-bottom: var(--space-4); }
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

#: Left rail: label, route, and the pipeline step it belongs to. Numbered because the order is
#: the workflow, and an operator who has lost their place needs to see where they are in it.
NAV: Final = (
    ("Setup", "/", "1"),
    ("Preflight", "/preflight", "2"),
    ("Data", "/data", "3"),
    ("Content", "/content", "4"),
    ("Publish", "/publish", "5"),
    ("Runs", "/runs", "6"),
)


def install() -> None:
    """Add the tokens to every page. Called once at startup.

    ``shared=True`` because every route is a ``@ui.page``: without it NiceGUI refuses the call
    outright rather than quietly styling only the auto-index page, which is the right refusal —
    a shell where one screen has the design system and the others do not would be worse than one
    with none.
    """
    ui.add_css(TOKENS, shared=True)


@contextmanager
def page(active: str, *, client_id: str | None, environment: str | None) -> Iterator[None]:
    """The shell every screen is rendered inside: left rail, then a canvas.

    The client and environment sit in the rail rather than on each screen, because "which client,
    which environment" is the question whose wrong answer is most expensive and least visible —
    and it must be answerable without navigating anywhere.
    """
    with ui.element("div").classes("shell"):
        with ui.element("nav").classes("rail"):
            with ui.element("div").classes("rail-brand"):
                ui.label("GS1 Digital Link").classes("rail-title")
                ui.label(client_id or "no client configured").classes("rail-sub")
                if environment:
                    colour = "tag-fail" if environment == "production" else "tag-na"
                    ui.label(environment.upper()).classes(f"rail-sub tag {colour}")
            for label, route, step in NAV:
                classes = "rail-link active" if label == active else "rail-link"
                with ui.link(target=route).classes(classes):
                    ui.label(step).classes("rail-num")
                    ui.label(label)
        with ui.element("main").classes("canvas"):
            yield


def heading(eyebrow: str, title: str, lede: str = "") -> None:
    """A screen's opening: what step this is, what it does, and why in one line."""
    ui.label(eyebrow).classes("eyebrow")
    ui.label(title).classes("title")
    if lede:
        ui.label(lede).classes("lede")


@contextmanager
def section(title: str) -> Iterator[None]:
    with ui.element("section").classes("section"):
        ui.label(title).classes("section-head")
        yield


def band(text: str, kind: str = "quiet") -> None:
    """A single-line statement that must not be missed. ``kind``: quiet | warn | danger."""
    ui.label(text).classes(f"band band-{kind}")


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
    """A button.

    ``danger`` — filled red — is reserved for the actions that actually **write**: the real run,
    the production confirmation, saving the pruned process list. Nothing else, ever. Red on a
    merely-negative choice like *Cancel* would put four red buttons on this screen and the one
    that matters would stop standing out, which is the only job red has here.
    """
    button = ui.button(label, on_click=on_click).props("no-caps unelevated")
    return button.props("color=negative" if danger else "color=primary")


def quiet_action(label: str, on_click: Callable[[], object]) -> ui.button:
    """A secondary choice — declining, going back, asking for more. Outlined, not filled."""
    return ui.button(label, on_click=on_click).props("no-caps outline color=grey-8")
