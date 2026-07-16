"""Client configuration loader (IMPLEMENTATION_SPEC §2.4, §4.2).

Loads and validates ``clients.yml`` into typed, frozen config models, applying the
``defaults`` block and enforcing the column-mapping contract (edge E6). Secrets are
handled lazily: this module never reads environment variables and never returns or
logs secret values — only the *names* of the env vars that hold them.

The Phase-2 GS1 client (``lib/gs1_dl_client.py``) defines a *resolved*,
single-environment ``GS1Config``; here :class:`GS1Config` models the full multi-
environment ``clients.yml`` block and bridges to the resolved shape via
:meth:`GS1Config.resolve`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final, Literal

import jsonschema
import yaml
from pydantic import BaseModel, ConfigDict, Field

from lib.errors import ConfigError, ExportParseError
from lib.gdsn import GdsnSource
from lib.gs1_dl_client import GS1Config as ResolvedGS1Config
from lib.gs1_dl_client import ResolverSettings
from lib.records import ProductRecord, is_valid_target_path

#: Repo-root-relative default locations.
_ROOT: Final = Path(__file__).resolve().parent.parent
DEFAULT_CLIENTS_PATH: Final = _ROOT / "clients.yml"
_SCHEMA_PATH: Final = _ROOT / "schema" / "clients.schema.json"

#: Config sub-blocks that inherit from the top-level ``defaults`` block.
_INHERITED_BLOCKS: Final = ("gs1", "wordpress", "qr", "flow")

#: ProductRecord field names a ``gdsn_map`` may target (``extras`` uses ``gdsn_extras``).
_PRODUCT_FIELDS: Final[frozenset[str]] = frozenset(ProductRecord.model_fields) - {"extras"}

Environment = Literal["test", "production"]


# --- Config models (§2.4) ----------------------------------------------------


class GS1Config(BaseModel):
    """Full multi-environment GS1 config for one client (§2.4).

    Mirrors the ``gs1`` block of ``clients.yml``. Use :meth:`resolve` to obtain the
    single-environment shape the Digital Link client consumes.
    """

    model_config = ConfigDict(frozen=True)

    account_number_test: str
    account_number_production: str | None = None
    client_id_env_test: str
    client_secret_env_test: str
    client_id_env_production: str | None = None
    client_secret_env_production: str | None = None
    environment: Environment = "test"
    api_version: Literal["v2"] = "v2"
    identification_key_type: Literal["Gtin"] = "Gtin"
    digital_link_url_pattern: str = "https://id.gs1.org/01/{gtin14}"
    resolver_settings: ResolverSettings = Field(default_factory=ResolverSettings)
    default_media_type: str = "text/html"
    batch_size: int = 50

    def resolve(self, environment: Environment | None = None) -> ResolvedGS1Config:
        """Produce the resolved, single-environment config for the GS1 client.

        Args:
            environment: Environment to resolve for; defaults to ``self.environment``.

        Returns:
            The ``lib.gs1_dl_client.GS1Config`` for the chosen environment.

        Raises:
            ConfigError: If the chosen environment's account/credentials are absent.
        """
        env = environment or self.environment
        account: str | None
        client_id_env: str | None
        client_secret_env: str | None
        if env == "test":
            account = self.account_number_test
            client_id_env = self.client_id_env_test
            client_secret_env = self.client_secret_env_test
        else:
            account = self.account_number_production
            client_id_env = self.client_id_env_production
            client_secret_env = self.client_secret_env_production
        if not (account and client_id_env and client_secret_env):
            raise ConfigError(f"gs1 config is missing {env} account/credentials")
        return ResolvedGS1Config(
            account_number=account,
            client_id_env=client_id_env,
            client_secret_env=client_secret_env,
            environment=env,
            resolver_settings=self.resolver_settings,
            batch_size=self.batch_size,
        )


class ExportConfig(BaseModel):
    """How a client's product export is located and mapped (§2.4, §3)."""

    model_config = ConfigDict(frozen=True)

    format: Literal["flat", "gdsn"] = "flat"
    path: str
    # Flat single-sheet path (§3.2).
    column_map: dict[str, str] = Field(default_factory=dict)
    extras_columns: list[str] = Field(default_factory=list)
    # GDSN datapool path (§3 extension).
    market_language: dict[str, str] = Field(default_factory=dict)
    gdsn_map: dict[str, GdsnSource] = Field(default_factory=dict)
    gdsn_extras: dict[str, GdsnSource] = Field(default_factory=dict)


class TaxonomyConfig(BaseModel):
    """How a WordPress taxonomy's terms are sourced."""

    model_config = ConfigDict(frozen=True)

    map_from_column: str


class WordPressConfig(BaseModel):
    """WordPress target configuration for one client (§2.4)."""

    model_config = ConfigDict(frozen=True)

    site_url: str
    username: str
    app_password_env: str
    post_type: str = "page"
    post_status: str = "publish"
    multilingual_plugin: Literal["none", "polylang", "wpml"] = "none"
    #: Path to the site-side WPML helper route (``multilingual_plugin: wpml`` only). WPML has
    #: no core REST route for language assignment or translation linking, so each site hosts a
    #: small helper; the namespace is per-site, hence config rather than a constant. See
    #: ``lib.multilingual.WPMLAdapter`` and ``docs/clients/noviplast-page-adapter.md`` §7.
    wpml_helper_path: str = "/wp-json/gs1dl/v1/translations"
    default_language: str = "nl"
    languages: list[str] = Field(default_factory=lambda: ["nl"])
    image_handling: Literal["url_in_export", "local_folder", "manual"] = "url_in_export"
    taxonomies: dict[str, TaxonomyConfig] = Field(default_factory=dict)
    #: ``{acf_field_name: product_record_field}`` for themes that render from ACF rather than
    #: ``post_content`` (Oxygen, and similar page builders). Empty means the client renders
    #: from the body template, as in Phase 5. See :mod:`lib.acf`.
    acf_map: dict[str, str] = Field(default_factory=dict)
    slug_pattern: str | None = None
    target_url_pattern: str | None = None


def _default_qr_formats() -> list[Literal["svg", "png", "eps"]]:
    return ["svg", "png"]


class QRConfig(BaseModel):
    """QR rendering configuration (§2.4)."""

    model_config = ConfigDict(frozen=True)

    formats: list[Literal["svg", "png", "eps"]] = Field(default_factory=_default_qr_formats)
    size_mm: int = 20
    error_correction: Literal["L", "M", "Q", "H"] = "M"
    dpi: int = 300


class TemplateConfig(BaseModel):
    """Template override location and per-language filenames (§2.4)."""

    model_config = ConfigDict(frozen=True)

    override_dir: str | None = None
    files: dict[str, str] = Field(default_factory=dict)


class GS1LinkConfig(BaseModel):
    """One GS1 resolver link definition (§2.4)."""

    model_config = ConfigDict(frozen=True)

    link_type: str
    default: bool = False
    public: bool = True
    per_language: bool = False
    title_pattern: str | None = None


class FlowConfig(BaseModel):
    """Interactive-flow behaviour toggles (§2.4)."""

    model_config = ConfigDict(frozen=True)

    on_change: str = "prompt"
    on_missing_field: str = "prompt"
    batch_size: int = 50


class WebsiteStatusConfig(BaseModel):
    """Operator-maintained website-status control file (create-only gate).

    Not part of the datasource export and not in the original spec: a deliberate,
    per-client extension. The file lists, per product, whether it is already on the
    website and already registered in GS1. ``scripts/run_plan.py`` uses it to gate
    which products are candidates for page/QR creation — eligible when the GTIN is
    already in GS1 (its resolver record exists) and not yet on the website. Columns
    are named here so a client can relabel them without code changes; defaults match
    the Noviplast file.
    """

    model_config = ConfigDict(frozen=True)

    path: str
    gtin_column: str = "Barcode"
    on_website_column: str = "Momenteel op Website"
    in_gs1_column: str = "Al in Gs1"
    site_link_column: str | None = "Link naar site"


class ClientConfig(BaseModel):
    """The full resolved configuration for one client (§2.4)."""

    model_config = ConfigDict(frozen=True)

    client_id: str
    display_name: str
    enabled: bool = True
    gs1: GS1Config
    export: ExportConfig
    wordpress: WordPressConfig
    template: TemplateConfig | None = None
    gs1_links: list[GS1LinkConfig] = Field(default_factory=list)
    qr: QRConfig | None = None
    flow: FlowConfig | None = None
    website_status: WebsiteStatusConfig | None = None


# --- Loading (§4.2) ----------------------------------------------------------


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` onto a copy of ``base`` (override wins)."""
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _validate_column_targets(client_id: str, export: ExportConfig) -> None:
    """Enforce the column-mapping contract at load time (edge E6).

    Raises:
        ExportParseError: If a mapping targets a field ``ProductRecord`` lacks.
    """
    if export.format == "flat":
        for column, target in export.column_map.items():
            if not is_valid_target_path(target):
                raise ExportParseError(
                    f"client {client_id!r}: column {column!r} maps to unknown field {target!r}"
                )
    else:  # gdsn
        for field in export.gdsn_map:
            if field not in _PRODUCT_FIELDS:
                raise ExportParseError(
                    f"client {client_id!r}: gdsn_map targets unknown field {field!r}"
                )


def load_clients(path: str | Path = DEFAULT_CLIENTS_PATH) -> dict[str, ClientConfig]:
    """Load, validate, and normalise ``clients.yml`` (§4.2).

    Args:
        path: Path to the config file.

    Returns:
        Mapping of ``client_id`` to its :class:`ClientConfig`, with ``defaults``
        applied.

    Raises:
        ConfigError: If the file is missing/malformed or fails schema validation.
        ExportParseError: If a client's column mapping targets an unknown field (E6).
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read clients config at {path}: {exc}") from exc
    data = yaml.safe_load(raw) or {}

    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as exc:
        # exc.message names only structural keys / env-var names — safe to surface.
        raise ConfigError(f"clients config is invalid: {exc.message}") from exc

    defaults = data.get("defaults", {})
    clients: dict[str, ClientConfig] = {}
    for client_id, block in data["clients"].items():
        merged = dict(block)
        for key in _INHERITED_BLOCKS:
            inherited = _deep_merge(defaults.get(key, {}), block.get(key, {}))
            if inherited:
                merged[key] = inherited
        merged["client_id"] = client_id
        try:
            config = ClientConfig.model_validate(merged)
        except ValueError as exc:
            raise ConfigError(f"client {client_id!r} is invalid: {exc}") from exc
        _validate_column_targets(client_id, config.export)
        clients[client_id] = config
    return clients


def get_client(client_id: str, path: str | Path = DEFAULT_CLIENTS_PATH) -> ClientConfig:
    """Load one client's configuration by id (§4.2).

    Args:
        client_id: Key under ``clients:`` in the config file.
        path: Path to the config file.

    Returns:
        The client's :class:`ClientConfig`.

    Raises:
        ConfigError: If the client id is unknown (or the file fails to load).
    """
    clients = load_clients(path)
    try:
        return clients[client_id]
    except KeyError as exc:
        raise ConfigError(f"unknown client_id {client_id!r}") from exc
