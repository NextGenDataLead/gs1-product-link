/** Tests for resolving a client's GS1 config from clients.yml (§9.1). */

import { describe, expect, it } from "vitest";

import { resolveGS1Config } from "./config.js";

const RAW = {
  defaults: { gs1: { batch_size: 50 } },
  clients: {
    noviplast: {
      gs1: {
        account_number_test: "8720796420906",
        account_number_production: "8713195000008",
        client_id_env_test: "NOVIPLAST_GS1_CLIENT_SANDBOX_ID",
        client_secret_env_test: "NOVIPLAST_GS1_CLIENT_SANDBOX_SECRET",
        client_id_env_production: "NOVIPLAST_GS1_CLIENT_ID",
        client_secret_env_production: "NOVIPLAST_GS1_CLIENT_SECRET",
      },
    },
  },
};

describe("resolveGS1Config", () => {
  it("resolves the test host, account, and sandbox credentials by default", () => {
    const config = resolveGS1Config(RAW, "noviplast", {
      NOVIPLAST_GS1_CLIENT_SANDBOX_ID: "sandbox-id",
      NOVIPLAST_GS1_CLIENT_SANDBOX_SECRET: "sandbox-secret",
    });

    expect(config.host).toBe("gs1nl-api-acc.gs1.nl");
    expect(config.accountNumber).toBe("8720796420906");
    expect(config.clientId).toBe("sandbox-id");
    expect(config.clientSecret).toBe("sandbox-secret");
    expect(config.resolverSettings).toEqual({ useGS1Resolver: true, resolverDomainName: null });
    expect(config.batchSize).toBe(50);
  });

  it("resolves production host, account, and credentials when environment is production", () => {
    const raw = {
      ...RAW,
      clients: {
        noviplast: {
          gs1: { ...RAW.clients.noviplast.gs1, environment: "production" as const },
        },
      },
    };
    const config = resolveGS1Config(raw, "noviplast", {
      NOVIPLAST_GS1_CLIENT_ID: "prod-id",
      NOVIPLAST_GS1_CLIENT_SECRET: "prod-secret",
    });

    expect(config.host).toBe("gs1nl-api.gs1.nl");
    expect(config.accountNumber).toBe("8713195000008");
    expect(config.clientId).toBe("prod-id");
    expect(config.clientSecret).toBe("prod-secret");
  });

  it("throws for an unknown client", () => {
    expect(() => resolveGS1Config(RAW, "ghost", {})).toThrow(/Unknown client_id/);
  });

  it("throws when a credential env var is unset", () => {
    expect(() => resolveGS1Config(RAW, "noviplast", {})).toThrow(
      /NOVIPLAST_GS1_CLIENT_SANDBOX_ID/,
    );
  });
});
