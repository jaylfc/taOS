import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { loadProgress } from "./progress";

describe("loadProgress", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  function stubGet(body: unknown) {
    globalThis.fetch = (async () => ({
      ok: true,
      json: async () => body,
    })) as typeof fetch;
  }

  it("returns the deploy_key from the saved preferences", async () => {
    stubGet({ in_progress: true, step: "first-chat", completed: [], skipped: [], deploy_key: "k-1" });
    const progress = await loadProgress();
    expect(progress?.deploy_key).toBe("k-1");
  });

  it("drops a non-string deploy_key", async () => {
    stubGet({ in_progress: true, step: "first-chat", completed: [], skipped: [], deploy_key: 42 });
    const progress = await loadProgress();
    expect(progress?.deploy_key).toBeUndefined();
  });
});
