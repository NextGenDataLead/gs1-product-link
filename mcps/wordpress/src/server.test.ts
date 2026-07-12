/**
 * End-to-end MCP wiring test: a real MCP Client calls each tool over an in-memory
 * transport, with the WordPress backend mocked via injected deps. Verifies the five
 * tools are registered and callable and return the documented {ok, error} shape.
 */

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { describe, expect, it } from "vitest";

import {
  type WordPressClient,
  type WordPressClientConfig,
  WordPressApiError,
} from "./client.js";
import { createServer } from "./server.js";
import type { ToolDeps } from "./tools.js";

const CONFIG: WordPressClientConfig = {
  siteUrl: "https://staging.example.com",
  username: "automation-bot",
  appPassword: "app-pass",
  postType: "noviplast",
  postStatus: "publish",
  multilingualPlugin: "none",
  defaultLanguage: "nl",
  languages: ["nl", "fr"],
};

async function connectClient(deps: ToolDeps): Promise<Client> {
  const server = createServer(deps);
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: "test", version: "1.0.0" });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  return client;
}

function depsFor(fake: Partial<WordPressClient>): ToolDeps {
  return {
    loadConfig: () => CONFIG,
    makeClient: () => fake as unknown as WordPressClient,
  };
}

function parse(result: { content: unknown[] }): Record<string, unknown> {
  const first = result.content[0] as { type: string; text: string };
  return JSON.parse(first.text) as Record<string, unknown>;
}

describe("wordpress MCP tools", () => {
  it("lists the five tools", async () => {
    const client = await connectClient(depsFor({}));
    const { tools } = await client.listTools();
    expect(tools.map((t) => t.name).sort()).toEqual([
      "wp_detect_multilingual",
      "wp_find_by_slug",
      "wp_upload_media",
      "wp_upsert_page",
      "wp_verify_url",
    ]);
  });

  it("upsert_page returns ok with the page", async () => {
    const page = { id: 10, slug: "p-1" };
    const client = await connectClient(depsFor({ upsertPage: async () => page }));
    const result = (await client.callTool({
      name: "wp_upsert_page",
      arguments: {
        client_id: "noviplast",
        slug: "p-1",
        title: "T",
        content: "B",
        language: "nl",
        meta: { gtin: "1" },
      },
    })) as { content: unknown[] };
    expect(parse(result)).toEqual({ ok: true, error: null, page });
  });

  it("detect_multilingual returns the plugin", async () => {
    const client = await connectClient(
      depsFor({ detectMultilingualPlugin: async () => "polylang" }),
    );
    const result = (await client.callTool({
      name: "wp_detect_multilingual",
      arguments: { client_id: "noviplast" },
    })) as { content: unknown[] };
    expect(parse(result)).toEqual({ ok: true, error: null, plugin: "polylang" });
  });

  it("verify_url returns ok_url", async () => {
    const client = await connectClient(depsFor({ verifyUrl: async () => true }));
    const result = (await client.callTool({
      name: "wp_verify_url",
      arguments: { client_id: "noviplast", url: "https://staging.example.com/p/1" },
    })) as { content: unknown[] };
    expect(parse(result)).toEqual({ ok: true, error: null, ok_url: true });
  });

  it("reports a WordPress API error as ok:false", async () => {
    const client = await connectClient(
      depsFor({
        upsertPage: async () => {
          throw new WordPressApiError(409, "slug exists");
        },
      }),
    );
    const result = (await client.callTool({
      name: "wp_upsert_page",
      arguments: {
        client_id: "noviplast",
        slug: "p-1",
        title: "T",
        content: "B",
        language: "nl",
        meta: { gtin: "1" },
      },
    })) as { content: unknown[]; isError?: boolean };

    expect(result.isError).toBe(true);
    const body = parse(result);
    expect(body.ok).toBe(false);
    expect(String(body.error)).toContain("409");
  });
});
