/**
 * Resolve a client's GS1 config from `clients.yml` (IMPLEMENTATION_SPEC §9.1).
 *
 * The MCP hides plumbing (`accountNumber`, `resolverSettings`, `auth_scheme`) and
 * resolves it by `client_id`. This is the TS-native counterpart to Phase 3's
 * Python `lib/config.py`; the two read the same file independently.
 */

import { readFileSync } from "node:fs";
import { load as parseYaml } from "js-yaml";

import type { AuthScheme, Environment, GS1ClientConfig } from "./client.js";
import { HOSTS } from "./client.js";

const DEFAULT_BATCH_SIZE = 50;
const DEFAULT_CLIENTS_FILE = "clients.yml";

/** Shape of the `gs1` config block (defaults + per-client), as read from YAML. */
interface RawGs1Block {
  environment?: Environment;
  auth_scheme?: AuthScheme;
  account_number?: string;
  token_env_test?: string;
  token_env_production?: string;
  batch_size?: number;
  resolver_settings?: {
    use_gs1_resolver?: boolean;
    resolver_domain_name?: string | null;
  };
}

interface RawClientsFile {
  defaults?: { gs1?: RawGs1Block };
  clients?: Record<string, { gs1?: RawGs1Block }>;
}

/**
 * Merge defaults with a client's `gs1` block and resolve the token from `env`.
 * Pure (no filesystem) so it is unit-testable.
 */
export function resolveGS1Config(
  raw: RawClientsFile,
  clientId: string,
  env: NodeJS.ProcessEnv,
): GS1ClientConfig {
  const client = raw.clients?.[clientId];
  if (client === undefined) {
    throw new Error(`Unknown client_id: ${clientId}`);
  }
  const defaults = raw.defaults?.gs1 ?? {};
  const merged: RawGs1Block = { ...defaults, ...(client.gs1 ?? {}) };

  const environment: Environment = merged.environment ?? "test";
  const accountNumber = merged.account_number;
  if (accountNumber === undefined) {
    throw new Error(`Missing gs1.account_number for client ${clientId}`);
  }

  const tokenEnvName =
    environment === "production" ? merged.token_env_production : merged.token_env_test;
  if (tokenEnvName === undefined) {
    throw new Error(
      `Missing gs1.token_env_${environment === "production" ? "production" : "test"} ` +
        `for client ${clientId}`,
    );
  }
  const token = env[tokenEnvName];
  if (token === undefined || token === "") {
    throw new Error(`Environment variable ${tokenEnvName} is not set`);
  }

  const resolver = merged.resolver_settings ?? {};
  return {
    host: HOSTS[environment],
    accountNumber,
    authScheme: merged.auth_scheme ?? "Bearer",
    token,
    resolverSettings: {
      useGS1Resolver: resolver.use_gs1_resolver ?? true,
      resolverDomainName: resolver.resolver_domain_name ?? null,
    },
    batchSize: merged.batch_size ?? DEFAULT_BATCH_SIZE,
  };
}

/** Load `clients.yml` and resolve the config for one client. */
export function loadGS1ClientConfig(
  clientId: string,
  options: { clientsPath?: string; env?: NodeJS.ProcessEnv } = {},
): GS1ClientConfig {
  const path = options.clientsPath ?? process.env.GS1_CLIENTS_FILE ?? DEFAULT_CLIENTS_FILE;
  const raw = parseYaml(readFileSync(path, "utf8")) as RawClientsFile;
  return resolveGS1Config(raw, clientId, options.env ?? process.env);
}
