import { describe, it, expect, vi, afterEach } from "vitest";
import { providerModels } from "./agent";

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
