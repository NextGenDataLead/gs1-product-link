# wordpress-mcp

MCP server wrapping the **WordPress REST API v2**. Exposes five tools
(IMPLEMENTATION_SPEC §9.2):

| Tool | Purpose |
|---|---|
| `wp_upsert_page` | Create/update one product page idempotently (lookup by id → slug → `meta.gtin`) |
| `wp_upload_media` | Upload a media file as `multipart/form-data`, deduped by a content-addressed slug (`{base}-{sha12}`); returns its id |
| `wp_find_by_slug` | Find a page by slug under a post type (`null` if absent) |
| `wp_verify_url` | Whether a URL resolves to a 2xx/3xx via HEAD |
| `wp_detect_multilingual` | Detect the site's multilingual plugin: `polylang`, `wpml`, or `none` |

The tools hide plumbing (`site_url`, credentials, `post_status`) and resolve it from
`clients.yml` by `client_id`. The HTTP client mirrors the authoritative Python client
(`lib/wp_client.py`): HTTP Basic auth with an application password, the retry policy
(§5.1; a `401` is terminal — no token dance), the 3-step upsert lookup with the E8
(mismatched `meta.gtin`) and E11 (non-GTIN slug collision) guards, and the media contract:
a content-addressed slug (`{base}-{sha12}`) so dedup is a pure slug lookup, multipart
uploads, and a stored-size check that refuses and deletes a truncated upload before the
call that would claim its slug (§6.1 / §6.2). Deliberate differences are listed under
[Parity with `lib/wp_client.py`](#parity-with-libwp_clientpy).

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
credentials from `clients.yml`, (2) GTIN-keyed idempotency (`meta.gtin` upsert key + a
content-addressed media slug), (3) Polylang/WPML translation linking, or (4) the E8/E11 guards.
Standardising on the gs1-nl structure (same `ToolDeps` injection, `{ok, error}` envelope,
retry loop) keeps the two MCPs maintainable as one codebase.

## Parity with `lib/wp_client.py`

This client is a **second implementation of the same contract**, so it drifts silently every
time the Python one is fixed against reality. Issue #75 was exactly that: four fixes the Python
client earned on the live pilot site had never crossed, and a reader had no way to know. This
section is the countermeasure — **keep it true, or the next reader inherits the same problem.**

Mirrored, and load-bearing enough to name:

- **Multipart uploads.** A security plugin on the live site refused an ordinary H.264 video sent
  as a raw body with `Content-Disposition`, answering a bare HTML `403`, while accepting the
  identical bytes as `multipart/form-data` (PR #62). `RequestOptions` therefore has no raw-body
  mode at all — its absence is a guarantee, not an oversight.
- **A content-addressed media slug**, `{base}-{sha12}`. Dedup is a pure slug lookup, so it needs
  no read-back of `meta.content_sha256` — which requires REST to expose attachment meta, and the
  live site did not — and a stale attachment sharing only the base slug cannot shadow the match.
- **A stored-size check** before the finalise call, since the finalise is what claims the content
  slug. A truncated upload once returned `201` and left a 1.5 MB fragment of an 8 MB video that
  WordPress served happily (PR #72). The fragment is deleted and `MediaIntegrityError` raised.
- **An ownership predicate**: only attachments carrying a **non-empty** `meta.content_sha256` are
  ours to delete (PR #73). Non-empty rather than present, because the key is registered site-wide
  on the pilot — present on all 406 attachments, non-empty on only the 40 from this tool.

Deliberate differences, decided rather than drifted:

| Difference | Why |
|---|---|
| **No `delete_media`, and no `wp_delete_media` tool.** The ownership predicate is ported because the dedup path needs it to decide whether a wrong-sized attachment is ours to replace; the delete it guards is private and reachable only from the two integrity paths. | The MCP exposes five tools and nothing in the publish path uses it. `src/server.test.ts` asserts that exact list, which is the standing guard that nobody added a destructive one. |
| **`uploadMedia` returns the attachment id**, not Python's `MediaUpload(media_id, created)`. | `created` exists for the Python run loop's rollback, so it deletes only what it added. The MCP has no run loop and no rollback; the field would be one nobody reads. |
| **Diagnostics go to stderr** via an injectable `logger`, not a logging framework. | This is a stdio server — stdout carries the MCP protocol frames. |
| **`WordPressApiError` carries no call label**, where the Python `WordPressAPIError` names the failing call and quotes the scrubbed body (#71). | Not yet ported. Diagnostic quality only; no behavioural difference. |

## Status

Code-complete and unit-tested against mocked HTTP (`fetch` stub) and an in-memory MCP
transport, including the four media behaviours above (#75). The live staging round-trip
(Polylang detection, §6.1/§6.2 idempotency, and the published-page exit gate) runs via the
marked `tests/integration/test_wp_staging.py` once staging WordPress is provisioned — see
IMPLEMENTATION_SPEC §12 Phase 4 and §13.3.

**Nothing in the publish path uses this server**, and it is unpublished by choice
(`docs/OPEN_DECISIONS.md` OD-2). `lib/wp_client.py` is what runs against the live site.
