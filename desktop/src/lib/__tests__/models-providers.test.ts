/**
 * Tests for the provider classifiers in src/lib/models.ts.
 *
 * #356 (johny-mnemonic) surfaced that controller-hosted local providers
 * (a manually-added local ollama or llama.cpp endpoint) were silently
 * dropped from the agent / assistant model picker — only cloud providers
 * and worker-attached (`source: worker:*`) network providers survived
 * the filter. localProvidersToAggregated picks up the residual.
 */
import { describe, it, expect } from "vitest";
import {
  cloudProvidersToAggregated,
  localProvidersToAggregated,
  type CloudProvider,
} from "../models";

const cloudOpenAI: CloudProvider = {
  name: "openai-prod",
  type: "openai",
  models: [{ id: "gpt-4o" }, { id: "gpt-4o-mini" }],
};

const networkOllama: CloudProvider = {
  name: "remote-ollama",
  type: "ollama",
  source: "worker:fedora-worker",
  models: [{ id: "llama3:8b" }],
};

const localOllama: CloudProvider = {
  name: "local-ollama",
  type: "ollama",
  // no source, OR an explicit non-worker prefix
  models: [{ id: "qwen2:7b" }],
};

const localLlamaCpp: CloudProvider = {
  name: "local-llama-cpp",
  type: "llama-cpp",
  source: "local",
  models: [{ id: "phi-3-mini" }],
};

const localCustomCompat: CloudProvider = {
  name: "my-server",
  type: "openai-compatible",  // IS in CLOUD_PROVIDER_TYPES — handled by cloudProvidersToAggregated, not local
  models: [{ id: "custom-model" }],
};


describe("localProvidersToAggregated", () => {
  it("includes ollama at controller (no worker: source)", () => {
    const result = localProvidersToAggregated([localOllama]);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      id: "qwen2:7b",
      hostKind: "controller",
      host: "local-ollama",
      backend: "ollama",
    });
  });

  it("includes llama-cpp with explicit local source", () => {
    const result = localProvidersToAggregated([localLlamaCpp]);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      id: "phi-3-mini",
      hostKind: "controller",
      backend: "llama-cpp",
    });
  });

  it("excludes cloud providers (handled by cloudProvidersToAggregated)", () => {
    const result = localProvidersToAggregated([cloudOpenAI]);
    expect(result).toEqual([]);
  });

  it("excludes openai-compatible (it's a cloud type even when local)", () => {
    // Edge case: openai-compatible is in CLOUD_PROVIDER_TYPES so the
    // existing cloud helper handles it. localProvidersToAggregated
    // skips it on purpose to avoid double-counting.
    const result = localProvidersToAggregated([localCustomCompat]);
    expect(result).toEqual([]);
  });

  it("excludes worker-attached network providers", () => {
    const result = localProvidersToAggregated([networkOllama]);
    expect(result).toEqual([]);
  });

  it("emits one entry per model", () => {
    const multiModel: CloudProvider = {
      name: "local",
      type: "ollama",
      models: [{ id: "a" }, { id: "b" }, { id: "c" }],
    };
    const result = localProvidersToAggregated([multiModel]);
    expect(result.map((r) => r.id)).toEqual(["a", "b", "c"]);
  });

  it("emits a default entry when models list is empty", () => {
    const noModels: CloudProvider = {
      name: "empty-local",
      type: "ollama",
      // no models, no model
    };
    const result = localProvidersToAggregated([noModels]);
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("default");
  });

  it("partitions cleanly with cloudProvidersToAggregated — no double counting", () => {
    const all = [cloudOpenAI, networkOllama, localOllama, localLlamaCpp, localCustomCompat];
    const cloud = cloudProvidersToAggregated(all);
    const local = localProvidersToAggregated(all);
    const cloudIds = cloud.map((m) => m.id);
    const localIds = local.map((m) => m.id);
    // Disjoint sets — no model surfaces in both
    for (const id of cloudIds) {
      expect(localIds).not.toContain(id);
    }
  });
});
