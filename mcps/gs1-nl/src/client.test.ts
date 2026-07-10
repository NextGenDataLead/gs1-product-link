/** Tests for the TS GS1 client: path anomalies, retry, auth, scrubbing. */

import { describe, expect, it } from "vitest";

import {
  GS1ApiError,
  GS1Client,
  type GS1ClientConfig,
  type LinkInput,
  type UpsertEntry,
} from "./client.js";

const TOKEN = "SECRET-TOKEN-XYZ";

const CONFIG: GS1ClientConfig = {
  host: "gs1nl-api-acc.gs1.nl",
  accountNumber: "8712345000003",
  authScheme: "Bearer",
  token: TOKEN,
  resolverSettings: { useGS1Resolver: true, resolverDomainName: null },
  batchSize: 50,
};

const LINK: LinkInput = {
  link_type: "pip",
  language: "nl",
  link_title: "Product page",
  target_url: "https://example.com/p/123",
  default_link_type: true,
  public: true,
  media_type: "text/html",
};

const ENTRY: UpsertEntry = { gtin: "8712345678905", item_description: "Test", links: [LINK] };

interface Call {
  url: string;
  init: RequestInit;
}

/** A fetch stub that returns queued responses/errors in order and records calls. */
function stubFetch(queue: (Response | Error)[]): { fetchImpl: typeof fetch; calls: Call[] } {
  const calls: Call[] = [];
  const fetchImpl = (async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init: init ?? {} });
    const next = queue.shift();
    if (next === undefined) {
      throw new Error("no more queued responses");
    }
    if (next instanceof Error) {
      throw next;
    }
    return next;
  }) as unknown as typeof fetch;
  return { fetchImpl, calls };
}

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

const noSleep = async (): Promise<void> => {};

function makeClient(queue: (Response | Error)[], config: GS1ClientConfig = CONFIG) {
  const { fetchImpl, calls } = stubFetch(queue);
  const client = new GS1Client(config, { fetchImpl, sleep: noSleep });
  return { client, calls };
}

describe("path anomalies and body", () => {
  it("upsert posts lowercase digitallink with camelCase body and zero-padded gtin", async () => {
    const { client, calls } = makeClient([json(200, {})]);

    await client.upsert(ENTRY);

    expect(calls[0].url).toBe("https://gs1nl-api-acc.gs1.nl/digitallinkv2/v2/digitallink");
    expect((calls[0].init.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
    const body = JSON.parse(calls[0].init.body as string);
    expect(body.identificationKey).toBe("08712345678905");
    expect(body.identificationKeyType).toBe("Gtin");
    expect(body.resolverSettings).toEqual({ useGS1Resolver: true, resolverDomainName: null });
    expect(body.links[0]).toEqual({
      linkType: "pip",
      language: "nl",
      linkTitle: "Product page",
      targetUrl: "https://example.com/p/123",
      defaultLinkType: true,
      public: true,
      mediaType: "text/html",
    });
  });

  it("get uses capital-L digitalLink path and zero-padded gtin", async () => {
    const { client, calls } = makeClient([json(200, { identificationKey: "x" })]);

    await client.get("8712345678905");

    expect(calls[0].url).toBe(
      "https://gs1nl-api-acc.gs1.nl/digitallinkv2/v2/digitalLink/Gtin/08712345678905",
    );
  });

  it("get returns null on 404", async () => {
    const { client } = makeClient([new Response("not found", { status: 404 })]);
    expect(await client.get("00000000000000")).toBeNull();
  });

  it("upsertBulk batches into groups of batchSize", async () => {
    const { client, calls } = makeClient(
      [json(200, {}), json(200, {}), json(200, {})],
      { ...CONFIG, batchSize: 2 },
    );
    const entries: UpsertEntry[] = Array.from({ length: 5 }, (_, i) => ({
      gtin: `1234567890${i}`,
      item_description: `p${i}`,
      links: [LINK],
    }));

    const result = await client.upsertBulk(entries);

    expect(calls.map((c) => JSON.parse(c.init.body as string).length)).toEqual([2, 2, 1]);
    expect(result).toEqual({ total: 5, batches: 3, status_codes: [200, 200, 200] });
  });
});

describe("retry policy", () => {
  it("retries once on 429 then succeeds", async () => {
    const { client, calls } = makeClient([
      new Response("", { status: 429, headers: { "Retry-After": "0" } }),
      json(200, {}),
    ]);

    await client.upsert(ENTRY);
    expect(calls).toHaveLength(2);
  });

  it("retries on 5xx then succeeds", async () => {
    const { client, calls } = makeClient([json(500, {}), json(503, {}), json(200, {})]);

    await client.upsert(ENTRY);
    expect(calls).toHaveLength(3);
  });

  it("throws after 5xx retries are exhausted", async () => {
    const { client, calls } = makeClient([json(500, {}), json(500, {}), json(500, {})]);

    await expect(client.upsert(ENTRY)).rejects.toBeInstanceOf(GS1ApiError);
    expect(calls).toHaveLength(3);
  });

  it("retries network errors as 5xx", async () => {
    const { client, calls } = makeClient([
      new TypeError("fetch failed"),
      new TypeError("fetch failed"),
      json(200, {}),
    ]);

    await client.upsert(ENTRY);
    expect(calls).toHaveLength(3);
  });
});

describe("auth and errors", () => {
  it("falls back from Bearer to raw on 401", async () => {
    const { client, calls } = makeClient([new Response("", { status: 401 }), json(200, {})]);

    await client.upsert(ENTRY);

    expect((calls[0].init.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
    expect((calls[1].init.headers as Record<string, string>).Authorization).toBe(TOKEN);
  });

  it("populates errorResults from a standard 400 body", async () => {
    const errorBody = [
      { identifier: "08712345678905", errors: [{ code: "X", message: "bad" }] },
    ];
    const { client } = makeClient([json(400, errorBody)]);

    await expect(client.upsert(ENTRY)).rejects.toMatchObject({
      statusCode: 400,
      errorResults: errorBody,
    });
  });

  it("never leaks the token in thrown errors", async () => {
    const { client } = makeClient([json(400, { message: "bad request" })]);

    let caught: unknown;
    try {
      await client.upsert(ENTRY);
    } catch (err) {
      caught = err;
    }
    const serialized = JSON.stringify({
      message: (caught as Error).message,
      body: (caught as GS1ApiError).responseBody,
    });
    expect(serialized).not.toContain(TOKEN);
  });
});
