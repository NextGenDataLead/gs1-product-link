"""Screen 1 — what this machine is configured to do, and to whom.

Read-only for now. The guided forms over ``.env`` and the operator-facing half of ``clients.yml``
land here in the next phase; until then this screen's job is to make the current configuration
legible, because the two most expensive mistakes in this pipeline are both *config* mistakes that
nothing downstream notices: pointing at the wrong export, and pointing at production.

It shows the **names** of credential env vars and never their values, matching ``clients.yml``
itself. Whether a name resolves to something is the preflight's question, on the next screen.
"""

from __future__ import annotations

from nicegui import ui

from ui import REPO_ROOT, context, theme


def render() -> None:
    cid = context.client_id()
    cfg = context.client_config(cid)

    with theme.page("Setup", client_id=cid, environment=cfg.gs1.environment if cfg else None):
        theme.heading(
            "Step 1",
            "Setup",
            "What this machine is configured to publish, where, and with which credentials.",
        )

        if cfg is None:
            _no_config()
            return

        if context.is_production(cfg):
            theme.band(
                "This client points at the GS1 PRODUCTION resolver. Records written there can "
                "never be deleted — retraction only disables them.",
                "danger",
            )

        with theme.section("Client"):
            _rows(
                ("Client id", cfg.client_id),
                ("Display name", cfg.display_name),
                ("Languages", ", ".join(cfg.wordpress.languages)),
                ("Default language", cfg.wordpress.default_language),
            )

        with theme.section("WordPress"):
            _rows(
                ("Site", cfg.wordpress.site_url),
                ("User", cfg.wordpress.username),
                ("Password variable", cfg.wordpress.app_password_env),
                ("Post type", cfg.wordpress.post_type),
                ("Multilingual plugin", cfg.wordpress.multilingual_plugin),
            )
            ui.label(
                "Only the variable name is configured here, never the value — that lives in .env. "
                "Whether it resolves, and whether the account can still publish, is the "
                "preflight's question."
            ).classes("note")

        with theme.section("GS1"):
            _rows(
                ("Environment", cfg.gs1.environment),
                (
                    "Account",
                    cfg.gs1.account_number_production
                    if context.is_production(cfg)
                    else cfg.gs1.account_number_test or "—",
                ),
                ("Digital Link pattern", cfg.gs1.digital_link_url_pattern or "—"),
            )

        with theme.section("Files"):
            _file_row("Product export", cfg.export.path)
            if cfg.process_list is not None:
                _file_row("Process list", cfg.process_list.path)
            _file_row("Parsed products", f"output/{cfg.client_id}/data/products.json")
            if cfg.generator is not None:
                _file_row("Generated copy", f"output/{cfg.client_id}/data/generated_cache.json")
            ui.label(
                "The export path is authoritative and has no command-line override. A fresh "
                "export dropped somewhere new is invisible to the tool — which is why the "
                "modification date is shown beside it here and confirmed again at the intent gate."
            ).classes("note")

        with theme.section("Content generation"):
            if cfg.generator is None:
                theme.band(
                    "No `generator` block. If this client's copy is written by an LLM, that block "
                    "must be present even on a machine with no API key: run_plan derives "
                    "require_generated_copy from it, so deleting it silently publishes blank "
                    "taglines instead of holding the unit back.",
                    "warn",
                )
            else:
                _rows(
                    ("Prompt version", cfg.generator.prompt_version),
                    ("Model", cfg.generator.model or "—"),
                )
                ui.label(
                    "Copy is generated on the maintainer's machine and handed over as a file. "
                    "This shell never calls an LLM, holds no API key, and never runs "
                    "run_generate. Upload the cache on the Content screen."
                ).classes("note")


def _no_config() -> None:
    theme.band("clients.yml did not load, so nothing else on this screen can be trusted.", "danger")
    ui.label(
        "Run the preflight for the full list of what is wrong with it — it reports every "
        "offending field at once rather than stopping at the first."
    ).classes("note")
    theme.command(["-m", "scripts.doctor", "--offline"])
    ui.link("Go to Preflight →", "/preflight").classes("mono")


def _rows(*pairs: tuple[str, str]) -> None:
    with ui.element("dl").classes("grid gap-x-8 gap-y-2").style("grid-template-columns:12rem 1fr"):
        for label, value in pairs:
            ui.label(label).classes("note")
            ui.label(value or "—").classes("mono scroll-x")


def _file_row(label: str, path: str) -> None:
    fact = context.file_fact(path)
    with (
        ui.element("div")
        .classes("grid gap-x-8 gap-y-1 items-baseline")
        .style("grid-template-columns:12rem 1fr auto")
    ):
        ui.label(label).classes("note")
        try:
            shown = str(fact.path.relative_to(REPO_ROOT))
        except ValueError:
            shown = str(fact.path)
        ui.label(shown).classes("mono scroll-x")
        ui.label(fact.age).classes(f"tag {'tag-na' if fact.exists else 'tag-fail'}")
