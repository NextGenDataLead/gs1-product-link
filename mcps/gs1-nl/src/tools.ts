/**
 * The three GS1 NL MCP tools (IMPLEMENTATION_SPEC §9.1).
 *
 * Input schemas mirror the v2 `CreateOrUpdateRequest` body but hide plumbing
 * (`accountNumber`, `resolverSettings`, `auth_scheme`), which the handlers resolve
 * from `clients.yml` by `client_id`.
 */

import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import type { GS1ClientConfig, UpsertEntry } from "./client.js";
import { GS1ApiError, GS1Client } from "./client.js";
import { loadGS1ClientConfig } from "./config.js";

const GTIN_PATTERN = /^[0-9]{8,14}$/;

const linkSchema = z.object({
  link_type: z.string(),
  language: z.string(),
  link_title: z.string(),
  target_url: z.string().url(),
  default_link_type: z.boolean(),
  public: z.boolean(),
  media_type: z.string(),
});

const appIdentifierSchema = z.object({
  identifier: z.string(),
  template_variable: z.string(),
});

const entryFields = {
  gtin: z.string().regex(GTIN_PATTERN),
  item_description: z.string(),
  is_enabled: z.boolean().default(true),
  links: z.array(linkSchema),
  application_identifiers: z.array(appIdentifierSchema).default([]),
};

const upsertShape = { client_id: z.string(), ...entryFields };
const bulkShape = {
  client_id: z.string(),
  entries: z.array(z.object(entryFields)),
};
const getShape = {
  client_id: z.string(),
  gtin: z.string().regex(GTIN_PATTERN),
};

/** Dependencies, injectable so tools can be tested without real config/network. */
export interface ToolDeps {
  loadConfig: (clientId: string) => GS1ClientConfig;
  makeClient: (config: GS1ClientConfig) => GS1Client;
}

const defaultDeps: ToolDeps = {
  loadConfig: (clientId) => loadGS1ClientConfig(clientId),
  makeClient: (config) => new GS1Client(config),
};

type ToolResult = {
  content: { type: "text"; text: string }[];
  isError?: boolean;
};

function ok(payload: Record<string, unknown>): ToolResult {
  return { content: [{ type: "text", text: JSON.stringify({ ok: true, error: null, ...payload }) }] };
}

function fail(error: unknown): ToolResult {
  // GS1ApiError carries no token; other errors reference env-var names, not values.
  const message = error instanceof Error ? error.message : String(error);
  const errorResults = error instanceof GS1ApiError ? error.errorResults : undefined;
  const body = { ok: false, error: message, error_results: errorResults ?? null };
  return { content: [{ type: "text", text: JSON.stringify(body) }], isError: true };
}

/** Register the three GS1 tools on an MCP server. */
export function registerGs1Tools(server: McpServer, deps: ToolDeps = defaultDeps): void {
  server.registerTool(
    "gs1_digital_link_upsert",
    {
      description: "Set or update the resolver target for one GTIN via the v2 API.",
      inputSchema: upsertShape,
    },
    async ({ client_id, gtin, item_description, is_enabled, links, application_identifiers }) => {
      try {
        const client = deps.makeClient(deps.loadConfig(client_id));
        const entry: UpsertEntry = {
          gtin,
          item_description,
          is_enabled,
          links,
          application_identifiers,
        };
        await client.upsert(entry);
        return ok({ gtin });
      } catch (err) {
        return fail(err);
      }
    },
  );

  server.registerTool(
    "gs1_digital_link_upsert_bulk",
    {
      description: "Bulk create/update. Batches into groups of batch_size internally.",
      inputSchema: bulkShape,
    },
    async ({ client_id, entries }) => {
      try {
        const client = deps.makeClient(deps.loadConfig(client_id));
        const result = await client.upsertBulk(entries as UpsertEntry[]);
        return ok({ total: result.total, batches: result.batches });
      } catch (err) {
        return fail(err);
      }
    },
  );

  server.registerTool(
    "gs1_digital_link_get",
    {
      description: "Fetch the current Digital Link entry for a GTIN. Returns null if not found.",
      inputSchema: getShape,
    },
    async ({ client_id, gtin }) => {
      try {
        const client = deps.makeClient(deps.loadConfig(client_id));
        const record = await client.get(gtin);
        return ok({ record });
      } catch (err) {
        return fail(err);
      }
    },
  );
}
