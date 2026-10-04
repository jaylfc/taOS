import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

import { resolveModels } from "./resolver-types";

describe("resolveModels", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  function makeOkResponse(results: Record<string, unknown>) {
    return {
      ok: true,
      json: async () => ({ results }),
    };
  }

  it("chunks 150 ids into two calls (100 then 50) against /api/store/resolve-batch", async () => {
    const ids = Array.from({ length: 150 }, (_, i) => `model-${i}`);
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      makeOkResponse(Object.fromEntries(ids.slice(0, 100).map((id) => [id, { compat: "green" }]))),
    ).mockResolvedValueOnce(
      makeOkResponse(Object.fromEntries(ids.slice(100).map((id) => [id, { compat: "green" }]))),
    );

    const map = await resolveModels(ids, "auto", { chunkSize: 100 });

    expect((globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls).toHaveLength(2);
    expect((globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0]).toBe("/api/store/resolve-batch");
    expect((globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[1][0]).toBe("/api/store/resolve-batch");
    const firstBody = JSON.parse((globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][1].body as string);
    const secondBody = JSON.parse((globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[1][1].body as string);
    expect(firstBody.manifest_ids).toHaveLength(100);
    expect(secondBody.manifest_ids).toHaveLength(50);
    expect(map.size).toBe(150);
  });

  it("excludes error entries and includes ok entries", async () => {
    const ids = ["model-a", "model-b", "model-c"];
    const responseResults: Record<string, { compat: string } | { error: string }> = {
      "model-a": { compat: "green" },
      "model-b": { error: "not found" },
      "model-c": { compat: "amber" },
    };
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(makeOkResponse(responseResults));

    const map = await resolveModels(ids, "auto", { chunkSize: 100 });

    expect(map.has("model-a")).toBe(true);
    expect(map.has("model-b")).toBe(false);
    expect(map.has("model-c")).toBe(true);
    expect(map.size).toBe(2);
  });

  it("does not call fetch when given 0 ids", async () => {
    const map = await resolveModels([], { chunkSize: 100 });
    expect((globalThis.fetch as unknown as ReturnType<typeof vi.fn>)).not.toHaveBeenCalled();
    expect(map.size).toBe(0);
  });

  it("fetch rejection on first chunk does not prevent second chunk resolution", async () => {
    const ids = Array.from({ length: 150 }, (_, i) => `model-${i}`);
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce(
        makeOkResponse(Object.fromEntries(ids.slice(100).map((id) => [id, { compat: "green" }])))
      );

    const map = await resolveModels(ids, "auto", { chunkSize: 100 });

    expect((globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls).toHaveLength(2);
    expect(map.size).toBe(50);
    expect(map.has("model-100")).toBe(true);
    expect(map.has("model-149")).toBe(true);
  });

  it("fetch with 404 response results in empty map", async () => {
    const ids = ["model-a", "model-b"];
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ ok: false });

    const map = await resolveModels(ids, "auto", { chunkSize: 100 });

    expect((globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls).toHaveLength(1);
    expect(map.size).toBe(0);
  });

  it("onProgress is called once per chunk", async () => {
    const ids = Array.from({ length: 150 }, (_, i) => `model-${i}`);
    let callCount = 0;
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      makeOkResponse(Object.fromEntries(ids.slice(0, 100).map((id) => [id, { compat: "green" }])))
    ).mockResolvedValueOnce(
      makeOkResponse(Object.fromEntries(ids.slice(100).map((id) => [id, { compat: "green" }])))
    );

    const map = await resolveModels(ids, "auto", {
      chunkSize: 100,
      onProgress: () => { callCount++; },
    });

    expect(callCount).toBe(2);
  });

  it("isCancelled prevents fetch when true", async () => {
    const ids = ["model-a", "model-b", "model-c"];
    let cancelled = true;
    const map = await resolveModels(ids, "auto", {
      chunkSize: 100,
      isCancelled: () => cancelled,
    });

    expect((globalThis.fetch as unknown as ReturnType<typeof vi.fn>)).not.toHaveBeenCalled();
    expect(map.size).toBe(0);
  });

  it("chunkSize 0 is clamped to 1", { timeout: 2000 }, async () => {
    const ids = ["model-a", "model-b", "model-c"];
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      makeOkResponse({ "model-a": { compat: "green" } })
    ).mockResolvedValueOnce(
      makeOkResponse({ "model-b": { compat: "amber" } })
    ).mockResolvedValueOnce(
      makeOkResponse({ "model-c": { compat: "red" } })
    );

    const map = await resolveModels(ids, "auto", {
      chunkSize: 0,
    });

    expect((globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls).toHaveLength(3);
    expect(map.size).toBe(3);
  });
});
