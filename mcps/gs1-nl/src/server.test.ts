/**
 * End-to-end MCP wiring test: a real MCP Client calls each tool over an in-memory
 * transport, with the GS1 backend mocked via injected deps. Verifies the tools are
 * registered and callable and return the documented {ok, error} shape.
 */

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { describe, expect, it } from "vitest";

import {
  GS1ApiError,
  type GS1Client,
  type GS1ClientConfig,
  type UpsertEntry,
} from "./client.js";
import { createServer } from "./server.js";
import type { ToolDeps } from "./tools.js";

const CONFIG: GS1ClientConfig = {
  host: "gs1nl-api-acc.gs1.nl",
  accountNumber: "8720796420906",
  clientId: "client-id",
  clientSecret: "client-secret",
  resolverSettings: { useGS1Resolver: true, resolverDomainName: null },
  batchSize: 50,
};

interface FakeClient {
  upsert: (entry: UpsertEntry) => Promise<void>;
  upsertBulk: (entries: UpsertEntry[]) => Promise<{
    total: number;
    batches: number;
    status_codes: number[];
  }>;
  get: (gtin: string) => Promise<Record<string, unknown> | null>;
}

async function connectClient(deps: ToolDeps): Promise<Client> {
  const server = createServer(deps);
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: "test", version: "1.0.0" });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  return client;
}

function depsFor(fake: Partial<FakeClient>): ToolDeps {
  return {
    loadConfig: () => CONFIG,
    makeClient: () => fake as unknown as GS1Client,
  };
}

function parse(result: { content: unknown[] }): Record<string, unknown> {
  const first = result.content[0] as { type: string; text: string };
  return JSON.parse(first.text) as Record<string, unknown>;
}

describe("gs1-nl MCP tools", () => {
  it("lists the three tools", async () => {
    const client = await connectClient(depsFor({}));
    const { tools } = await client.listTools();
    expect(tools.map((t) => t.name).sort()).toEqual([
      "gs1_digital_link_get",
      "gs1_digital_link_upsert",
      "gs1_digital_link_upsert_bulk",
    ]);
  });

  it("upsert returns ok on success", async () => {
    const client = await connectClient(depsFor({ upsert: async () => {} }));
    const result = await client.callTool({
      name: "gs1_digital_link_upsert",
      arguments: {
        client_id: "noviplast",
        gtin: "8712345678905",
        item_description: "Test",
        links: [
          {
            link_type: "pip",
            language: "nl",
            link_title: "Product",
            target_url: "https://example.com/p",
            default_link_type: true,
            public: true,
            media_type: "text/html",
          },
        ],
      },
    });
    expect(parse(result as { content: unknown[] })).toEqual({
      ok: true,
      error: null,
      gtin: "8712345678905",
    });
  });

  it("get returns the record", async () => {
    const record = { identificationKey: "08712345678905", isEnabled: true };
    const client = await connectClient(depsFor({ get: async () => record }));
    const result = await client.callTool({
      name: "gs1_digital_link_get",
      arguments: { client_id: "noviplast", gtin: "8712345678905" },
    });
    expect(parse(result as { content: unknown[] })).toEqual({ ok: true, error: null, record });
  });

  it("reports a GS1 API error as ok:false", async () => {
    const errorResults = [{ identifier: "08712345678905", errors: [{ code: "X", message: "bad" }] }];
    const client = await connectClient(
      depsFor({
        upsert: async () => {
          throw new GS1ApiError(400, "bad", errorResults);
        },
      }),
    );
    const result = (await client.callTool({
      name: "gs1_digital_link_upsert",
      arguments: {
        client_id: "noviplast",
        gtin: "8712345678905",
        item_description: "Test",
        links: [],
      },
    })) as { content: unknown[]; isError?: boolean };

    expect(result.isError).toBe(true);
    const body = parse(result);
    expect(body.ok).toBe(false);
    expect(body.error_results).toEqual(errorResults);
  });
});
