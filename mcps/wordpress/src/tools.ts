/**
 * The five WordPress MCP tools (IMPLEMENTATION_SPEC §9.2).
 *
 * Input/output shapes mirror the `lib/wp_client.py` functions in §4.4 but hide plumbing
 * (`site_url`, credentials, `post_status`), which the handlers resolve from `clients.yml`
 * by `client_id`. `post_type` defaults to the client's configured post type.
 */

import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import type { UpsertPageInput, WordPressClientConfig } from "./client.js";
import { WordPressClient } from "./client.js";
import { loadWordPressClientConfig } from "./config.js";

const upsertShape = {
  client_id: z.string(),
  post_type: z.string().optional(),
  slug: z.string(),
  title: z.string(),
  content: z.string(),
  language: z.string(),
  featured_media: z.number().int().optional(),
  parent: z.number().int().optional(),
  meta: z.record(z.unknown()).optional(),
  existing_id: z.number().int().optional(),
};
const uploadShape = {
  client_id: z.string(),
  file_path: z.string(),
  title: z.string().optional(),
};
const findShape = {
  client_id: z.string(),
  post_type: z.string().optional(),
  slug: z.string(),
};
const verifyShape = { client_id: z.string(), url: z.string().url() };
const detectShape = { client_id: z.string() };

/** Dependencies, injectable so tools can be tested without real config/network. */
export interface ToolDeps {
  loadConfig: (clientId: string) => WordPressClientConfig;
  makeClient: (config: WordPressClientConfig) => WordPressClient;
}

const defaultDeps: ToolDeps = {
  loadConfig: (clientId) => loadWordPressClientConfig(clientId),
  makeClient: (config) => new WordPressClient(config),
};

type ToolResult = {
  content: { type: "text"; text: string }[];
  isError?: boolean;
};

function ok(payload: Record<string, unknown>): ToolResult {
  return {
    content: [{ type: "text", text: JSON.stringify({ ok: true, error: null, ...payload }) }],
  };
}

function fail(error: unknown): ToolResult {
  // Errors reference status codes / env-var names, never the application password.
  const message = error instanceof Error ? error.message : String(error);
  return {
    content: [{ type: "text", text: JSON.stringify({ ok: false, error: message }) }],
    isError: true,
  };
}

/** Register the five WordPress tools on an MCP server. */
export function registerWordPressTools(server: McpServer, deps: ToolDeps = defaultDeps): void {
  server.registerTool(
    "wp_upsert_page",
    {
      description: "Create or update one product page idempotently (lookup by id/slug/meta.gtin).",
      inputSchema: upsertShape,
    },
    async ({ client_id, post_type, ...rest }) => {
      try {
        const config = deps.loadConfig(client_id);
        const client = deps.makeClient(config);
        const input: UpsertPageInput = { post_type: post_type ?? config.postType, ...rest };
        const page = await client.upsertPage(input);
        return ok({ page });
      } catch (err) {
        return fail(err);
      }
    },
  );

  server.registerTool(
    "wp_upload_media",
    {
      description: "Upload a media file, idempotently by content hash + slug. Returns its id.",
      inputSchema: uploadShape,
    },
    async ({ client_id, file_path, title }) => {
      try {
        const client = deps.makeClient(deps.loadConfig(client_id));
        const mediaId = await client.uploadMedia(file_path, title);
        return ok({ media_id: mediaId });
      } catch (err) {
        return fail(err);
      }
    },
  );

  server.registerTool(
    "wp_find_by_slug",
    {
      description: "Find a page by slug under a post type. Returns null when absent.",
      inputSchema: findShape,
    },
    async ({ client_id, post_type, slug }) => {
      try {
        const config = deps.loadConfig(client_id);
        const client = deps.makeClient(config);
        const page = await client.findBySlug(post_type ?? config.postType, slug);
        return ok({ page });
      } catch (err) {
        return fail(err);
      }
    },
  );

  server.registerTool(
    "wp_verify_url",
    {
      description: "Return whether a URL resolves to a 2xx/3xx response via HEAD.",
      inputSchema: verifyShape,
    },
    async ({ client_id, url }) => {
      try {
        const client = deps.makeClient(deps.loadConfig(client_id));
        const okUrl = await client.verifyUrl(url);
        return ok({ ok_url: okUrl });
      } catch (err) {
        return fail(err);
      }
    },
  );

  server.registerTool(
    "wp_detect_multilingual",
    {
      description: "Detect the site's multilingual plugin: polylang, wpml, or none.",
      inputSchema: detectShape,
    },
    async ({ client_id }) => {
      try {
        const client = deps.makeClient(deps.loadConfig(client_id));
        const plugin = await client.detectMultilingualPlugin();
        return ok({ plugin });
      } catch (err) {
        return fail(err);
      }
    },
  );
}
