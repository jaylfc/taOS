import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const originalFetch = global.fetch;

let detectHwClass: () => Promise<"rk3588" | "gpu" | "cpu">;

beforeEach(async () => {
  vi.resetModules();
  const mod = await import("./hw-detect");
  detectHwClass = mod.detectHwClass;
});

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("detectHwClass", () => {
  it("calls GET /api/cluster/workers", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [],
    });
    global.fetch = fetchMock;

    await detectHwClass();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/cluster/workers");
  });

  it("returns 'cpu' when response is not ok", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await detectHwClass()).toBe("cpu");
  });

  it("returns 'cpu' on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("network failure"));
    expect(await detectHwClass()).toBe("cpu");
  });

  it("returns 'rk3588' when a worker has rk3588 capability", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [
        { capabilities: ["cpu"] },
        { capabilities: ["rk3588"] },
      ],
    });
    expect(await detectHwClass()).toBe("rk3588");
  });

  it("returns 'rk3588' when a worker has npu capability", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ capabilities: ["npu"] }],
    });
    expect(await detectHwClass()).toBe("rk3588");
  });

  it("returns 'gpu' when a worker has cuda capability", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ capabilities: ["cuda"] }],
    });
    expect(await detectHwClass()).toBe("gpu");
  });

  it("returns 'gpu' when a worker has rocm capability", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ capabilities: ["rocm"] }],
    });
    expect(await detectHwClass()).toBe("gpu");
  });

  it("returns 'gpu' when a worker has metal capability", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ capabilities: ["metal"] }],
    });
    expect(await detectHwClass()).toBe("gpu");
  });

  it("returns 'gpu' when a worker has gpu capability", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ capabilities: ["gpu"] }],
    });
    expect(await detectHwClass()).toBe("gpu");
  });

  it("returns 'cpu' when no workers have npu/gpu capabilities", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [
        { capabilities: ["cpu"] },
        { capabilities: [] },
      ],
    });
    expect(await detectHwClass()).toBe("cpu");
  });

  it("returns 'cpu' when workers array is empty", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [],
    });
    expect(await detectHwClass()).toBe("cpu");
  });

  it("returns 'cpu' when workers lack capabilities field", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{}, { id: "w-1" }],
    });
    expect(await detectHwClass()).toBe("cpu");
  });

  it("returns gpu when a gpu worker comes before any rk3588 worker", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [
        { capabilities: ["cuda"] },
        { capabilities: ["rk3588"] },
      ],
    });
    expect(await detectHwClass()).toBe("gpu");
  });

  it("caches the result and does not fetch again", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ capabilities: ["cuda"] }],
    });
    global.fetch = fetchMock;

    expect(await detectHwClass()).toBe("gpu");
    expect(await detectHwClass()).toBe("gpu");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
