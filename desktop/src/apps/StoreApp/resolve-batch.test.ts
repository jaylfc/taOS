import { describe, it, expect, vi, beforeEach } from "vitest";

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
    const results: Record<string, { compat: string }> = {};
    for (const id of ids) {
      results[id] = { compat: "green" };
    }
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      makeOkResponse(Object.fromEntries(ids.slice(0, 100).map((id) => [id, { compat: "green" }]))),
    ).mockResolvedValueOnce(
      makeOkResponse(Object.fromEntries(ids.slice(100).map((id) => [id, { compat: "green" }]))),
    );

    const map = await resolveModels(ids, "auto", 100);

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

    const map = await resolveModels(ids, "auto", 100);

    expect(map.has("model-a")).toBe(true);
    expect(map.has("model-b")).toBe(false);
    expect(map.has("model-c")).toBe(true);
    expect(map.size).toBe(2);
  });

  it("does not call fetch when given 0 ids", async () => {
    const map = await resolveModels([], "auto", 100);
    expect((globalThis.fetch as unknown as ReturnType<typeof vi.fn>)).not.toHaveBeenCalled();
    expect(map.size).toBe(0);
  });
});
