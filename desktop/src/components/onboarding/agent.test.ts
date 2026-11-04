import { describe, it, expect, vi, afterEach } from "vitest";
import { providerModels, saveKey } from "./agent";

function providers(list: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(list), { status: 200 })));
}

describe("providerModels", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("returns the models the provider found with the key", async () => {
    providers([{ name: "deepseek", models: [{ id: "deepseek-v4-pro" }], models_seeded: false }]);
    await expect(providerModels("deepseek")).resolves.toEqual(["deepseek-v4-pro"]);
  });

  it("refuses the fallback list, which says nothing about the key", async () => {
    providers([{ name: "deepseek", models: [{ id: "deepseek-v4-pro" }], models_seeded: true }]);
    await expect(providerModels("deepseek")).rejects.toThrow(/check the key/i);
  });
});

describe("saveKey", () => {
  afterEach(() => vi.unstubAllGlobals());

  function server(provider: unknown) {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url === "/api/secrets" && init?.method === "POST") return new Response("{}", { status: 409 });
      if (url === "/api/providers") return new Response(JSON.stringify(provider ? [provider] : []), { status: 200 });
      return new Response("{}", { status: 200 });
    }));
    return calls;
  }

  it("never overwrites the key of a provider that already works", async () => {
    const calls = server({ name: "openai", api_key_secret: "provider-openai-key", models: [{ id: "gpt" }], models_seeded: false });
    await expect(saveKey("openai", "sk-new")).rejects.toThrow(/Providers/);
    expect(calls.some((c) => c.startsWith("PUT"))).toBe(false);
  });

  it("replaces a key setup saved earlier that no working provider uses", async () => {
    const calls = server({ name: "openai", api_key_secret: "provider-openai-key", models: [{ id: "gpt" }], models_seeded: true });
    await saveKey("openai", "sk-new");
    expect(calls).toContain("PUT /api/secrets/provider-openai-key");
  });
});
