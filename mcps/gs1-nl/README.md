# gs1-nl-mcp

MCP server wrapping the **GS1 NL Digital Link API v2**. Exposes three tools
(IMPLEMENTATION_SPEC §9.1):

| Tool | Purpose |
|---|---|
| `gs1_digital_link_upsert` | Set/update the resolver target for one GTIN |
| `gs1_digital_link_upsert_bulk` | Bulk variant; batches into `batch_size` internally |
| `gs1_digital_link_get` | Fetch the current entry for a GTIN (`null` if not found) |

The tools hide plumbing (`accountNumber`, `resolverSettings`, `auth_scheme`) and
resolve it from `clients.yml` by `client_id`. The HTTP client mirrors the
authoritative Python client (`lib/gs1_dl_client.py`): identical hosts, path prefix,
path-case anomalies (capital-L `digitalLink` for GET, lowercase for POST), the
Bearer/raw auth-scheme switch, and the retry policy (§4.3 / §5.1).

## Configuration

Resolved per call from `clients.yml`:

- **File location** — `clients.yml` in the working directory, or set `GS1_CLIENTS_FILE`.
- **Token** — read from the environment variable named by `gs1.token_env_test`
  (or `gs1.token_env_production` when `environment: production`). Never logged.

## Develop

```bash
npm ci                       # from repo root (npm workspaces)
npm -w mcps/gs1-nl run build # tsc -> dist/
npm -w mcps/gs1-nl test      # vitest
npm -w mcps/gs1-nl start     # serve over stdio
```

## Status

Code-complete and unit-tested against mocked HTTP and an in-memory MCP transport.
The real-GTIN test-environment call (and confirmation of Bearer-vs-raw and the
not-found status code) is pending captured fixtures / a test token — see
IMPLEMENTATION_SPEC §13.2.
