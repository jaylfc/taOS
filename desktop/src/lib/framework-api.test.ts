import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchFrameworkState,
  startFrameworkUpdate,
  fetchLatestFrameworks,
  fetchPermittedModels,
  setPermittedModels,
} from "./framework-api";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("fetchFrameworkState", () => {
  it("calls the right URL and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        framework: "openclaw",
        installed: { tag: "v1.0.0", sha: "abc123" },
        latest: { tag: "v1.1.0", sha: "def456", published_at: "2025-01-01" },
        update_available: true,
        update_status: "idle",
        update_started_at: null,
        last_error: null,
        last_snapshot: null,
      }),
    });
    global.fetch = fetchMock;

    const slug = "openclaw";
    const result = await fetchFrameworkState(slug);

    expect(fetchMock).toHaveBeenCalledWith(
      `/api/agents/${encodeURIComponent(slug)}/framework`,
    );
    expect(result.framework).toBe("openclaw");
    expect(result.installed.tag).toBe("v1.0.0");
    expect(result.latest?.tag).toBe("v1.1.0");
    expect(result.update_available).toBe(true);
  });

  it("throws on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
    });

    await expect(fetchFrameworkState("openclaw")).rejects.toThrow(
      "framework fetch 500",
    );
  });

  it("encodes slug with special characters", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        framework: "test",
        installed: { tag: null, sha: null },
        latest: null,
        update_available: false,
        update_status: "idle",
        update_started_at: null,
        last_error: null,
        last_snapshot: null,
      }),
    });
    global.fetch = fetchMock;

    await fetchFrameworkState("my agent");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain(encodeURIComponent("my agent"));
  });
});

describe("startFrameworkUpdate", () => {
  it("POSTs without target version when none given", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
    });
    global.fetch = fetchMock;

    const slug = "openclaw";
    await startFrameworkUpdate(slug);

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe(
      `/api/agents/${encodeURIComponent(slug)}/framework/update`,
    );
    expect(opts.method).toBe("POST");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(opts.body)).toEqual({});
  });

  it("POSTs with target version when provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
    });
    global.fetch = fetchMock;

    await startFrameworkUpdate("openclaw", "v2.0.0");

    const [, opts] = fetchMock.mock.calls[0];
    expect(JSON.parse(opts.body)).toEqual({
      target_version: "v2.0.0",
    });
  });

  it("throws body.error on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: "already updating" }),
    });

    await expect(startFrameworkUpdate("openclaw")).rejects.toThrow(
      "already updating",
    );
  });

  it("throws fallback message when non-ok and no body error", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({}),
    });

    await expect(startFrameworkUpdate("openclaw")).rejects.toThrow(
      "update start 500",
    );
  });
});

describe("fetchLatestFrameworks", () => {
  it("fetches without refresh by default", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        openclaw: { tag: "v1.1.0", sha: "def456", published_at: "2025-01-01" },
      }),
    });
    global.fetch = fetchMock;

    const result = await fetchLatestFrameworks();

    expect(fetchMock).toHaveBeenCalledWith("/api/frameworks/latest");
    expect(result.openclaw.tag).toBe("v1.1.0");
  });

  it("appends refresh=true when refresh is true", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({}),
    });
    global.fetch = fetchMock;

    await fetchLatestFrameworks(true);

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/frameworks/latest?refresh=true");
  });

  it("throws on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
    });

    await expect(fetchLatestFrameworks()).rejects.toThrow(
      "latest frameworks 503",
    );
  });
});

describe("fetchPermittedModels", () => {
  it("calls the right URL and returns parsed state", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        permitted: ["gpt-4", "claude-3"],
        current: "gpt-4",
      }),
    });
    global.fetch = fetchMock;

    const name = "my-agent";
    const result = await fetchPermittedModels(name);

    expect(fetchMock).toHaveBeenCalledWith(
      `/api/agents/${encodeURIComponent(name)}/permitted-models`,
    );
    expect(result.permitted).toEqual(["gpt-4", "claude-3"]);
    expect(result.current).toBe("gpt-4");
  });

  it("throws body.error on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ error: "agent not found" }),
    });

    await expect(fetchPermittedModels("unknown")).rejects.toThrow(
      "agent not found",
    );
  });

  it("throws fallback when non-ok and no body error", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({}),
    });

    await expect(fetchPermittedModels("agent")).rejects.toThrow(
      "permitted-models fetch 500",
    );
  });
});

describe("setPermittedModels", () => {
  it("PUTs models and returns updated state", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        permitted: ["claude-3"],
        current: "claude-3",
      }),
    });
    global.fetch = fetchMock;

    const name = "my-agent";
    const models = ["claude-3"];
    const result = await setPermittedModels(name, models);

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe(
      `/api/agents/${encodeURIComponent(name)}/permitted-models`,
    );
    expect(opts.method).toBe("PUT");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(opts.body)).toEqual({ models: ["claude-3"] });
    expect(result.current).toBe("claude-3");
  });

  it("throws body.error on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({ error: "forbidden" }),
    });

    await expect(setPermittedModels("agent", [])).rejects.toThrow("forbidden");
  });

  it("throws fallback when non-ok and no body error", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({}),
    });

    await expect(setPermittedModels("agent", [])).rejects.toThrow(
      "permitted-models set 500",
    );
  });
});
