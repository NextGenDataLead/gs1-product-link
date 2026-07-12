# wordpress-mcp

MCP server wrapping the **WordPress REST API v2**. Exposes five tools
(IMPLEMENTATION_SPEC §9.2):

| Tool | Purpose |
|---|---|
| `wp_upsert_page` | Create/update one product page idempotently (lookup by id → slug → `meta.gtin`) |
| `wp_upload_media` | Upload a media file, deduped by SHA-256 + slug; returns its id |
| `wp_find_by_slug` | Find a page by slug under a post type (`null` if absent) |
| `wp_verify_url` | Whether a URL resolves to a 2xx/3xx via HEAD |
| `wp_detect_multilingual` | Detect the site's multilingual plugin: `polylang`, `wpml`, or `none` |

The tools hide plumbing (`site_url`, credentials, `post_status`) and resolve it from
`clients.yml` by `client_id`. The HTTP client mirrors the authoritative Python client
(`lib/wp_client.py`): HTTP Basic auth with an application password, the retry policy
(§5.1; a `401` is terminal — no token dance), the 3-step upsert lookup with the E8
(mismatched `meta.gtin`) and E11 (non-GTIN slug collision) guards, and SHA-256 media
idempotency (§6.1 / §6.2).

## Configuration

Resolved per call from `clients.yml`:

- **File location** — `clients.yml` in the working directory, or set `GS1_CLIENTS_FILE`.
- **Auth (Application Passwords)** — HTTP Basic with `wordpress.username` and the
  application password read from the env var named by `wordpress.app_password_env`.
  The password and the derived `Authorization` header are never logged.
- **Post type** — `wordpress.post_type` (a custom post type must be registered with
  `show_in_rest => true`); defaults per tool call when a tool omits `post_type`.
- **Multilingual** — `wordpress.multilingual_plugin` (`none` | `polylang` | `wpml`) drives
  the `lang` field on writes; `wp_detect_multilingual` probes the site to confirm.

## Develop

```bash
npm ci                          # from repo root (npm workspaces)
npm -w mcps/wordpress run build # tsc -> dist/
npm -w mcps/wordpress test      # vitest
npm -w mcps/wordpress start     # serve over stdio
```

## Survey: adopt vs. fork

PROJECT_HANDOVER §8.2 requires a time-boxed survey of the WordPress MCP ecosystem before
building. Handover risk R1 anticipated "the WordPress MCP ecosystem is too immature; build
from scratch" and §6.1 recommended **"Adopt, likely fork."** The survey (GitHub, Jul 2026)
confirms that recommendation.

| Candidate | What it is | Why not adopted here |
|---|---|---|
| [Automattic/wordpress-mcp](https://github.com/Automattic/wordpress-mcp) (official; basis of iOSDevSK's WooCommerce fork) | A **WordPress plugin** exposing generic "abilities" with JWT auth, running *inside* the site | Inverts the trust model (server-side plugin vs. our client-side orchestrator); no `meta.gtin`-keyed idempotency, no Polylang translation linking, no per-client `clients.yml` credential resolution |
| [stifli-flex-mcp](https://github.com/estebanstifli/stifli-flex-mcp) (129+ tools), [c-sakel/wp-mcp-server](https://github.com/c-sakel/wp-mcp-server) (190+ tools), [wp-mcp-ultimate](https://github.com/AgriciDaniel/wp-mcp-ultimate) (58 abilities) | Broad generic REST-wrapper management servers | Huge generic tool surface for a 5-tool need; single-site/single-credential; none implement the §6.1/§6.2 idempotency contracts or E8/E11 semantics |
| [autowpmcp](https://github.com/Njengah/autowpmcp), [gopalcnepal/mcp-wordpress](https://github.com/gopalcnepal/mcp-wordpress) | Single-purpose blog publisher / read-only fetchers | Single-site, single-credential; no custom post type + `meta` idempotency; not multi-client |
| WooCommerce MCPs ([techspawn](https://github.com/techspawn/woocommerce-mcp-server), [iOSDevSK](https://github.com/iOSDevSK/mcp-for-woocommerce)) | WooCommerce store automation | Different product model (Woo store, not custom-post-type pages); out of scope |

**Decision: fork the in-repo `gs1-nl` pattern** — build a thin, purpose-built client that
mirrors `lib/wp_client.py`. No off-the-shelf server provides (1) per-call multi-client
credentials from `clients.yml`, (2) GTIN-keyed idempotency (`meta.gtin` upsert key +
SHA-256 media dedupe), (3) Polylang/WPML translation linking, or (4) the E8/E11 guards.
Standardising on the gs1-nl structure (same `ToolDeps` injection, `{ok, error}` envelope,
retry loop) keeps the two MCPs maintainable as one codebase.

## Status

Code-complete and unit-tested against mocked HTTP (`fetch` stub) and an in-memory MCP
transport. The live staging round-trip (Polylang detection, §6.1/§6.2 idempotency, and the
published-page exit gate) runs via the marked `tests/integration/test_wp_staging.py` once
staging WordPress is provisioned — see IMPLEMENTATION_SPEC §12 Phase 4 and §13.3.
