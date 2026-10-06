"""The video mapping screen — now only a frame around the two panels that do the work.

:mod:`ui.video_map_panel` (coverage, every file and what it maps to) and
:mod:`ui.video_signoff_panel` (the client's sign-off sheet) hold everything this screen used to.
They were lifted out so the Data screen can render them where the batch is chosen; this frame
keeps ``/videos`` working until it does.
"""

from __future__ import annotations

from nicegui import ui

from ui import context, theme, video_map_panel, video_signoff_panel


def render() -> None:
    cid = context.client_id()
    cfg = context.client_config(cid)

    with theme.page(
        "Video mapping",
        client_id=cid,
        environment=cfg.gs1.environment if cfg else None,
        facts=context.rail_facts(cid, cfg),
    ):
        theme.heading(
            theme.eyebrow("Video mapping"),
            "Video mapping",
            "Which video belongs to which product, in each language.",
        )
        if cfg is None or cid is None:
            theme.blocked(
                "clients.yml did not load, so this screen has nothing to work from.",
                link_label="Open Setup →",
                route="/",
            )
            return
        session = video_map_panel.session_for(cid, cfg)
        if session is None:
            theme.band(
                "No `media.video_map_path` in clients.yml — this client attaches no videos.",
                "quiet",
            )
            return

        ui.link("← back to Data", "/data").classes("note")
        coverage = ui.column().classes("w-full")
        signoff = ui.column().classes("w-full")
        rows = ui.column().classes("w-full")

        def changed() -> None:
            coverage.clear()
            rows.clear()
            with coverage, theme.section("Coverage"):
                video_map_panel.coverage(cfg, cid, session)
            with rows, theme.section("Every file, and what it maps to"):
                video_map_panel.rows(cfg, cid, session, changed)

        changed()
        with signoff:
            video_signoff_panel.render(cfg, cid, session, applied=changed, archived=lambda: None)
