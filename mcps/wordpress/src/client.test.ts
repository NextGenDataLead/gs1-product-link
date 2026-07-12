/** Tests for the TS WordPress client: auth, detection, idempotency, E8/E11, retry, scrubbing. */

import { createHash } from "node:crypto";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  type WordPressClientConfig,
  WordPressApiError,
  WordPressClient,
  WordPressGtinMismatchError,
} from "./client.js";

const APP_PASS = "abcd EFGH ijkl MNOP";
const USERNAME = "automation-bot";
const SITE = "https://staging.example.com";
const POST_TYPE = "noviplast";

const CONFIG: WordPressClientConfig = {
  siteUrl: SITE,
  username: USERNAME,
  appPassword: APP_PASS,
  postType: POST_TYPE,
  postStatus: "publish",
  multilingualPlugin: "none",
  defaultLanguage: "nl",
  languages: ["nl", "fr"],
};

interface Call {
  url: string;
  init: RequestInit;
}

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

function makeClient(queue: (Response | Error)[], config: WordPressClientConfig = CONFIG) {
  const { fetchImpl, calls } = stubFetch(queue);
  return { client: new WordPressClient(config, { fetchImpl, sleep: noSleep }), calls };
}

function authOf(call: Call): string | undefined {
  return (call.init.headers as Record<string, string> | undefined)?.Authorization;
}

describe("multilingual detection", () => {
  it("detects polylang when the pll route responds", async () => {
    const { client } = makeClient([json(200, [{ slug: "nl" }])]);
    expect(await client.detectMultilingualPlugin()).toBe("polylang");
  });

  it("detects wpml when only the wpml route responds", async () => {
    const { client } = makeClient([new Response("", { status: 404 }), json(200, {})]);
    expect(await client.detectMultilingualPlugin()).toBe("wpml");
  });

  it("detects none when neither route responds", async () => {
    const { client } = makeClient([
      new Response("", { status: 404 }),
      new Response("", { status: 404 }),
    ]);
    expect(await client.detectMultilingualPlugin()).toBe("none");
  });
});

describe("auth", () => {
  it("sends an HTTP Basic Authorization header", async () => {
    const { client, calls } = makeClient([json(200, [])]);

    await client.findBySlug(POST_TYPE, "p-1");

    const token = Buffer.from(`${USERNAME}:${APP_PASS}`).toString("base64");
    expect(authOf(calls[0])).toBe(`Basic ${token}`);
  });
});

describe("find_by_slug", () => {
  it("returns the first matching page", async () => {
    const { client } = makeClient([json(200, [{ id: 42, slug: "p-1" }])]);
    const page = await client.findBySlug(POST_TYPE, "p-1");
    expect(page?.id).toBe(42);
  });

  it("returns null on an empty list", async () => {
    const { client } = makeClient([json(200, [])]);
    expect(await client.findBySlug(POST_TYPE, "p-1")).toBeNull();
  });

  it("returns null on a 404 route", async () => {
    const { client } = makeClient([new Response("no route", { status: 404 })]);
    expect(await client.findBySlug(POST_TYPE, "p-1")).toBeNull();
  });
});

describe("upsert idempotency (§6.1)", () => {
  it("creates at the collection when absent", async () => {
    const { client, calls } = makeClient([
      json(200, []),
      json(200, []),
      json(201, { id: 10, meta: { gtin: "1" } }),
    ]);

    const page = await client.upsertPage({
      post_type: POST_TYPE,
      slug: "p-1",
      title: "T",
      content: "B",
      language: "nl",
      meta: { gtin: "1" },
    });

    expect(page.id).toBe(10);
    const post = calls.find((c) => c.init.method === "POST");
    expect(post?.url).toBe(`${SITE}/wp-json/wp/v2/${POST_TYPE}`);
  });

  it("updates by id when found, keeping the same id", async () => {
    const { client, calls } = makeClient([
      json(200, [{ id: 10, slug: "p-1", meta: { gtin: "1" } }]),
      json(200, { id: 10 }),
    ]);

    const page = await client.upsertPage({
      post_type: POST_TYPE,
      slug: "p-1",
      title: "T",
      content: "NEW",
      language: "nl",
      meta: { gtin: "1" },
    });

    expect(page.id).toBe(10);
    const post = calls.find((c) => c.init.method === "POST");
    expect(post?.url).toBe(`${SITE}/wp-json/wp/v2/${POST_TYPE}/10`);
    expect(JSON.parse(post?.init.body as string).content).toBe("NEW");
  });
});

describe("edge cases E8, E11 (§7)", () => {
  it("raises GtinMismatchError on a different meta.gtin, without writing (E8)", async () => {
    const { client, calls } = makeClient([json(200, [{ id: 99, meta: { gtin: "999" } }])]);

    await expect(
      client.upsertPage({
        post_type: POST_TYPE,
        slug: "p-1",
        title: "T",
        content: "B",
        language: "nl",
        meta: { gtin: "1" },
      }),
    ).rejects.toBeInstanceOf(WordPressGtinMismatchError);
    expect(calls.some((c) => c.init.method === "POST")).toBe(false);
  });

  it("raises WordPressApiError on a non-GTIN slug collision (E11 proactive)", async () => {
    const { client } = makeClient([json(200, [{ id: 7, meta: {} }])]);

    await expect(
      client.upsertPage({
        post_type: POST_TYPE,
        slug: "p-1",
        title: "T",
        content: "B",
        language: "nl",
        meta: { gtin: "1" },
      }),
    ).rejects.toMatchObject({ statusCode: 409 });
  });

  it("raises WordPressApiError on a create-time 409, not retried (E11)", async () => {
    const { client, calls } = makeClient([
      json(200, []),
      json(200, []),
      new Response("slug exists", { status: 409 }),
    ]);

    await expect(
      client.upsertPage({
        post_type: POST_TYPE,
        slug: "p-1",
        title: "T",
        content: "B",
        language: "nl",
        meta: { gtin: "1" },
      }),
    ).rejects.toMatchObject({ statusCode: 409 });
    expect(calls.filter((c) => c.init.method === "POST")).toHaveLength(1);
  });
});

describe("upload_media idempotency (§6.2)", () => {
  const dir = mkdtempSync(join(tmpdir(), "wpmcp-"));
  const img = join(dir, "photo.png");
  writeFileSync(img, Buffer.from("PNGDATA"));
  const digest = createHash("sha256").update(Buffer.from("PNGDATA")).digest("hex");

  it("uploads once and finalises the hash meta when new", async () => {
    const { client, calls } = makeClient([json(200, []), json(201, { id: 5 }), json(200, { id: 5 })]);

    const mediaId = await client.uploadMedia(img, "Photo");

    expect(mediaId).toBe(5);
    const creates = calls.filter(
      (c) => c.init.method === "POST" && c.url === `${SITE}/wp-json/wp/v2/media`,
    );
    expect(creates).toHaveLength(1);
    const finalise = calls.find((c) => c.url === `${SITE}/wp-json/wp/v2/media/5`);
    expect(JSON.parse(finalise?.init.body as string).meta).toEqual({ content_sha256: digest });
  });

  it("reuses the existing media when the hash matches", async () => {
    const { client, calls } = makeClient([
      json(200, [{ id: 5, slug: "photo", meta: { content_sha256: digest } }]),
    ]);

    const mediaId = await client.uploadMedia(img, "Photo");

    expect(mediaId).toBe(5);
    expect(calls.some((c) => c.init.method === "POST")).toBe(false);
  });
});

describe("verify_url", () => {
  it("returns true for a 2xx", async () => {
    const { client } = makeClient([new Response("", { status: 200 })]);
    expect(await client.verifyUrl(`${SITE}/p/1`)).toBe(true);
  });

  it("throws for a 404", async () => {
    const { client } = makeClient([new Response("", { status: 404 })]);
    await expect(client.verifyUrl(`${SITE}/p/1`)).rejects.toMatchObject({ statusCode: 404 });
  });
});

describe("retry policy (§5.1)", () => {
  it("retries once on 429 then succeeds", async () => {
    const { client, calls } = makeClient([
      new Response("", { status: 429, headers: { "Retry-After": "0" } }),
      json(200, []),
    ]);
    await client.findBySlug(POST_TYPE, "p-1");
    expect(calls).toHaveLength(2);
  });

  it("throws after 5xx retries are exhausted", async () => {
    const { client, calls } = makeClient([json(500, {}), json(500, {}), json(500, {})]);
    await expect(client.findBySlug(POST_TYPE, "p-1")).rejects.toBeInstanceOf(WordPressApiError);
    expect(calls).toHaveLength(3);
  });

  it("treats a 401 as terminal (not retried)", async () => {
    const { client, calls } = makeClient([new Response("", { status: 401 })]);
    await expect(client.findBySlug(POST_TYPE, "p-1")).rejects.toMatchObject({ statusCode: 401 });
    expect(calls).toHaveLength(1);
  });

  it("retries network errors then raises status 0", async () => {
    const { client } = makeClient([
      new TypeError("fetch failed"),
      new TypeError("fetch failed"),
      new TypeError("fetch failed"),
    ]);
    await expect(client.findBySlug(POST_TYPE, "p-1")).rejects.toMatchObject({ statusCode: 0 });
  });
});

describe("scrubbing", () => {
  it("never leaks the application password in thrown errors", async () => {
    const { client } = makeClient([json(400, { code: "rest_invalid" })]);

    let caught: unknown;
    try {
      await client.findBySlug(POST_TYPE, "p-1");
    } catch (err) {
      caught = err;
    }
    const serialized = JSON.stringify({
      message: (caught as Error).message,
      body: (caught as WordPressApiError).responseBody,
    });
    expect(serialized).not.toContain(APP_PASS);
    expect(serialized).not.toContain(Buffer.from(`${USERNAME}:${APP_PASS}`).toString("base64"));
  });
});
