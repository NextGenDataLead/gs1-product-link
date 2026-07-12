/**
 * TypeScript client for the WordPress REST API v2.
 *
 * Mirrors `lib/wp_client.py` (IMPLEMENTATION_SPEC §4.4 / §5.1 / §6.1-6.2 / §7):
 * HTTP Basic auth with an application password, the same retry policy (429/5xx with
 * independent budgets; a 401 is terminal — no token dance), the 3-step upsert lookup
 * (existing_id → slug → meta.gtin) with E8/E11 guards, and SHA-256 media idempotency.
 * Kept dependency-free (global `fetch`, `node:crypto`, `node:fs`) so it is unit-testable
 * without the MCP SDK.
 */

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { basename } from "node:path";

/** REST base for posts of any (custom) post type (§4.4). */
export const WP_API_PREFIX = "/wp-json/wp/v2";
const MEDIA_PATH = `${WP_API_PREFIX}/media`;
/** Polylang detection route — a 200 means the plugin is active (§4.4). */
export const PLL_LANGUAGES_PATH = "/wp-json/pll/v1/languages";
/** WPML detection route — its presence means WPML is active (§4.4). */
export const WPML_PROBE_PATH = "/wp-json/sitepress-multilingual-cms/v1/languages";

const GTIN_META_KEY = "gtin";
const CONTENT_HASH_META_KEY = "content_sha256";

const RETRY_429_MAX_ATTEMPTS = 5;
const RETRY_429_BASE_MS = 1000;
const RETRY_429_MAX_MS = 60000;
const RETRY_5XX_MAX_ATTEMPTS = 3;
const RETRY_5XX_BASE_MS = 500;
const RETRY_5XX_MAX_MS = 30000;

const HTTP_SUCCESS_MIN = 200;
const HTTP_SUCCESS_MAX = 300;
const HTTP_REDIRECT_MAX = 400;
const HTTP_NOT_FOUND = 404;
const HTTP_CONFLICT = 409;
const HTTP_TOO_MANY_REQUESTS = 429;
const HTTP_SERVER_ERROR_MIN = 500;
const HTTP_SERVER_ERROR_MAX = 600;
const NETWORK_ERROR_STATUS = 0;

export type MultilingualPlugin = "polylang" | "wpml" | "none";

/** Fully-resolved config for one client (application password already resolved). */
export interface WordPressClientConfig {
  siteUrl: string;
  username: string;
  appPassword: string;
  postType: string;
  postStatus: string;
  multilingualPlugin: MultilingualPlugin;
  defaultLanguage: string;
  languages: string[];
}

/** A WordPress post/page as returned by the REST API (`context=edit`). */
export interface WordPressPage {
  id: number;
  slug?: string;
  status?: string;
  type?: string;
  link?: string;
  title?: { rendered?: string; raw?: string };
  content?: { rendered?: string; raw?: string };
  parent?: number;
  featured_media?: number;
  meta?: Record<string, unknown>;
}

/** A WordPress media attachment as returned by the REST API. */
export interface WordPressMedia {
  id: number;
  slug?: string;
  source_url?: string;
  meta?: Record<string, unknown>;
}

/** Input to `upsertPage`, mirroring the §4.4 Python signature. */
export interface UpsertPageInput {
  post_type: string;
  slug: string;
  title: string;
  content: string;
  language: string;
  featured_media?: number;
  parent?: number;
  meta?: Record<string, unknown>;
  existing_id?: number;
}

/** Error raised for a non-success WordPress API response. Never carries the password. */
export class WordPressApiError extends Error {
  constructor(
    readonly statusCode: number,
    readonly responseBody: string,
  ) {
    super(`WordPress API error ${statusCode}`);
    this.name = "WordPressApiError";
  }
}

/** Raised when a matched page's meta.gtin differs from the row's GTIN (edge E8). */
export class WordPressGtinMismatchError extends Error {
  constructor(
    readonly gtin: string,
    readonly existingGtin: string,
    readonly wpPageId: number,
  ) {
    super(
      `WordPress page ${wpPageId} has meta.gtin ${existingGtin}, which does not match ` +
        `row GTIN ${gtin}; skipping to avoid overwriting`,
    );
    this.name = "WordPressGtinMismatchError";
  }
}

export interface WordPressClientOptions {
  /** Injectable fetch (defaults to global fetch) — used by tests. */
  fetchImpl?: typeof fetch;
  /** Injectable sleep (defaults to setTimeout) — makes retry backoff instant in tests. */
  sleep?: (ms: number) => Promise<void>;
}

interface RequestOptions {
  params?: Record<string, string>;
  jsonBody?: unknown;
  content?: Uint8Array;
  extraHeaders?: Record<string, string>;
}

const defaultSleep = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

function backoff429(attempt: number, retryAfterMs: number | null): number {
  if (retryAfterMs !== null) {
    return Math.min(retryAfterMs, RETRY_429_MAX_MS);
  }
  return Math.min(RETRY_429_BASE_MS * 2 ** (attempt - 1), RETRY_429_MAX_MS);
}

function backoff5xx(attempt: number): number {
  return Math.min(RETRY_5XX_BASE_MS * 2 ** (attempt - 1), RETRY_5XX_MAX_MS);
}

function retryAfterMs(response: Response): number | null {
  const value = response.headers.get("Retry-After");
  if (value === null) {
    return null;
  }
  const seconds = Number(value);
  return Number.isFinite(seconds) ? seconds * 1000 : null;
}

function metaGtin(meta: Record<string, unknown> | undefined): string | null {
  const value = meta?.[GTIN_META_KEY];
  return value === undefined || value === null || value === "" ? null : String(value);
}

/** Derive a deterministic media slug from the title (or filename) (§6.2). */
export function mediaSlug(title: string | undefined, fileName: string): string {
  const source = title && title.length > 0 ? title : fileName.replace(/\.[^.]+$/, "");
  const slug = source
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug.length > 0 ? slug : "media";
}

export class WordPressClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly sleep: (ms: number) => Promise<void>;
  multilingualPlugin: MultilingualPlugin;

  constructor(
    private readonly config: WordPressClientConfig,
    options: WordPressClientOptions = {},
  ) {
    this.baseUrl = config.siteUrl.replace(/\/+$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.sleep = options.sleep ?? defaultSleep;
    this.multilingualPlugin = config.multilingualPlugin;
  }

  private authHeader(): Record<string, string> {
    const token = Buffer.from(`${this.config.username}:${this.config.appPassword}`).toString(
      "base64",
    );
    return { Authorization: `Basic ${token}` };
  }

  /** Detect which multilingual plugin the site runs (§4.4). */
  async detectMultilingualPlugin(): Promise<MultilingualPlugin> {
    let detected: MultilingualPlugin;
    if (await this.probe(PLL_LANGUAGES_PATH)) {
      detected = "polylang";
    } else if (await this.probe(WPML_PROBE_PATH)) {
      detected = "wpml";
    } else {
      detected = "none";
    }
    this.multilingualPlugin = detected;
    return detected;
  }

  /** Return the page with `slug` under `postType`, or null (§4.4). */
  async findBySlug(postType: string, slug: string): Promise<WordPressPage | null> {
    const pages = await this.getList(`${WP_API_PREFIX}/${postType}`, {
      slug,
      context: "edit",
    });
    return pages.length > 0 ? (pages[0] as WordPressPage) : null;
  }

  /** Create or update one product page, idempotently (§6.1). */
  async upsertPage(input: UpsertPageInput): Promise<WordPressPage> {
    const gtin = metaGtin(input.meta);
    const found = await this.lookupExisting(input.post_type, input.slug, gtin, input.existing_id);
    if (found !== null) {
      this.guardGtinMatch(found, gtin);
      return this.writePage(input, found.id);
    }
    return this.writePage(input, null);
  }

  /** Upload a media file, idempotently by content hash + slug (§6.2). */
  async uploadMedia(filePath: string, title?: string): Promise<number> {
    const data = readFileSync(filePath);
    const digest = createHash("sha256").update(data).digest("hex");
    const slug = mediaSlug(title, basename(filePath));

    const existing = await this.findMediaBySlug(slug);
    if (existing !== null && mediaHash(existing) === digest) {
      return existing.id;
    }
    return this.createMedia(data, basename(filePath), title, slug, digest);
  }

  /** Return whether `url` resolves to a 2xx/3xx via HEAD (§4.4, §5.1). */
  async verifyUrl(url: string): Promise<boolean> {
    let response: Response;
    try {
      response = await this.fetchImpl(url, { method: "HEAD" });
    } catch (err) {
      throw new WordPressApiError(NETWORK_ERROR_STATUS, `verify_url network error: ${String(err)}`);
    }
    if (response.status >= HTTP_SUCCESS_MIN && response.status < HTTP_REDIRECT_MAX) {
      return true;
    }
    throw new WordPressApiError(response.status, await response.text());
  }

  // -- Lookup / write internals -------------------------------------------

  private async lookupExisting(
    postType: string,
    slug: string,
    gtin: string | null,
    existingId: number | undefined,
  ): Promise<WordPressPage | null> {
    if (existingId !== undefined) {
      const page = await this.getPage(postType, existingId);
      if (page !== null) {
        return page;
      }
    }
    const bySlug = await this.findBySlug(postType, slug);
    if (bySlug !== null) {
      return bySlug;
    }
    if (gtin !== null) {
      return this.findByMetaGtin(postType, gtin);
    }
    return null;
  }

  private guardGtinMatch(found: WordPressPage, gtin: string | null): void {
    if (gtin === null) {
      return;
    }
    const existingGtin = metaGtin(found.meta);
    if (existingGtin === null) {
      // E11: slug collision with a non-GTIN page — needs human intervention.
      throw new WordPressApiError(
        HTTP_CONFLICT,
        `slug collision with non-GTIN WordPress page ${found.id}`,
      );
    }
    if (existingGtin !== gtin) {
      throw new WordPressGtinMismatchError(gtin, existingGtin, found.id); // E8
    }
  }

  private async getPage(postType: string, pageId: number): Promise<WordPressPage | null> {
    try {
      const response = await this.request("GET", `${WP_API_PREFIX}/${postType}/${pageId}`, {
        params: { context: "edit" },
      });
      return (await response.json()) as WordPressPage;
    } catch (err) {
      if (err instanceof WordPressApiError && err.statusCode === HTTP_NOT_FOUND) {
        return null;
      }
      throw err;
    }
  }

  private async findByMetaGtin(postType: string, gtin: string): Promise<WordPressPage | null> {
    const pages = await this.getList(`${WP_API_PREFIX}/${postType}`, {
      meta_key: GTIN_META_KEY,
      meta_value: gtin,
      context: "edit",
    });
    return pages.length > 0 ? (pages[0] as WordPressPage) : null;
  }

  private async findMediaBySlug(slug: string): Promise<WordPressMedia | null> {
    const items = await this.getList(MEDIA_PATH, { slug, context: "edit" });
    return items.length > 0 ? (items[0] as WordPressMedia) : null;
  }

  private async getList(path: string, params: Record<string, string>): Promise<unknown[]> {
    try {
      const response = await this.request("GET", path, { params });
      const data = await response.json();
      return Array.isArray(data) ? data : [];
    } catch (err) {
      if (err instanceof WordPressApiError && err.statusCode === HTTP_NOT_FOUND) {
        return [];
      }
      throw err;
    }
  }

  private async writePage(input: UpsertPageInput, pageId: number | null): Promise<WordPressPage> {
    const body: Record<string, unknown> = {
      title: input.title,
      content: input.content,
      status: this.config.postStatus,
      slug: input.slug,
    };
    if (input.meta !== undefined) {
      body.meta = input.meta;
    }
    if (input.featured_media !== undefined) {
      body.featured_media = input.featured_media;
    }
    if (input.parent !== undefined) {
      body.parent = input.parent;
    }
    if (this.multilingualPlugin === "polylang") {
      body.lang = input.language;
    }
    const path =
      pageId === null
        ? `${WP_API_PREFIX}/${input.post_type}`
        : `${WP_API_PREFIX}/${input.post_type}/${pageId}`;
    const response = await this.request("POST", path, { jsonBody: body });
    return (await response.json()) as WordPressPage;
  }

  private async createMedia(
    data: Uint8Array,
    fileName: string,
    title: string | undefined,
    slug: string,
    digest: string,
  ): Promise<number> {
    const response = await this.request("POST", MEDIA_PATH, {
      content: data,
      extraHeaders: {
        "Content-Type": "application/octet-stream",
        "Content-Disposition": `attachment; filename="${fileName}"`,
      },
    });
    const media = (await response.json()) as WordPressMedia;
    const updateBody: Record<string, unknown> = {
      slug,
      meta: { [CONTENT_HASH_META_KEY]: digest },
    };
    if (title !== undefined) {
      updateBody.title = title;
    }
    await this.request("POST", `${MEDIA_PATH}/${media.id}`, { jsonBody: updateBody });
    return media.id;
  }

  private async probe(path: string): Promise<boolean> {
    try {
      await this.request("GET", path, {});
      return true;
    } catch (err) {
      if (err instanceof WordPressApiError) {
        return false;
      }
      throw err;
    }
  }

  /** Issue one HTTP call with the retry policy in §5.1. */
  private async request(method: string, path: string, opts: RequestOptions): Promise<Response> {
    const query =
      opts.params === undefined ? "" : `?${new URLSearchParams(opts.params).toString()}`;
    const url = this.baseUrl + path + query;
    let attempts429 = 0;
    let attempts5xx = 0;

    for (;;) {
      const headers: Record<string, string> = { ...this.authHeader() };
      if (opts.jsonBody !== undefined) {
        headers["Content-Type"] = "application/json";
      }
      Object.assign(headers, opts.extraHeaders ?? {});
      const init: RequestInit = { method, headers };
      if (opts.content !== undefined) {
        init.body = opts.content;
      } else if (opts.jsonBody !== undefined) {
        init.body = JSON.stringify(opts.jsonBody);
      }

      let response: Response;
      try {
        response = await this.fetchImpl(url, init);
      } catch (err) {
        attempts5xx += 1;
        if (attempts5xx >= RETRY_5XX_MAX_ATTEMPTS) {
          throw new WordPressApiError(NETWORK_ERROR_STATUS, `network error: ${String(err)}`);
        }
        await this.sleep(backoff5xx(attempts5xx));
        continue;
      }

      const status = response.status;
      if (status >= HTTP_SUCCESS_MIN && status < HTTP_SUCCESS_MAX) {
        return response;
      }
      if (status === HTTP_TOO_MANY_REQUESTS) {
        attempts429 += 1;
        if (attempts429 >= RETRY_429_MAX_ATTEMPTS) {
          throw await this.toError(response);
        }
        await this.sleep(backoff429(attempts429, retryAfterMs(response)));
        continue;
      }
      if (status >= HTTP_SERVER_ERROR_MIN && status < HTTP_SERVER_ERROR_MAX) {
        attempts5xx += 1;
        if (attempts5xx >= RETRY_5XX_MAX_ATTEMPTS) {
          throw await this.toError(response);
        }
        await this.sleep(backoff5xx(attempts5xx));
        continue;
      }
      // Any other 4xx (400/401/403/404/409): terminal per §5.1.
      throw await this.toError(response);
    }
  }

  private async toError(response: Response): Promise<WordPressApiError> {
    return new WordPressApiError(response.status, await response.text());
  }
}

function mediaHash(media: WordPressMedia): string | null {
  const value = media.meta?.[CONTENT_HASH_META_KEY];
  return typeof value === "string" && value.length > 0 ? value : null;
}
