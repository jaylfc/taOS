import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  listPins,
  pinAgent,
  unpinAgent,
  mintCopilotTicket,
  listAgents,
} from "./browser-agent-api";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("listPins", () => {
  it("returns pins on 200", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        pins: [
          { agent_id: "agent-1", pinned_at: "2026-01-01T00:00:00Z" },
          { agent_id: "agent-2", pinned_at: "2026-01-02T00:00:00Z" },
        ],
      }),
    });

    const pins = await listPins("profile-1", "tab-1");
    expect(pins.length).toBe(2);
    expect(pins[0].agent_id).toBe("agent-1");
  });

  it("returns [] on 401", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401 });
    expect(await listPins("profile-1", "tab-1")).toEqual([]);
  });

  it("returns [] on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("network failure"));
    expect(await listPins("profile-1", "tab-1")).toEqual([]);
  });

  it("includes credentials", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ pins: [] }),
    });
    global.fetch = fetchMock;
    await listPins("profile-1", "tab-1");
    const [, opts] = fetchMock.mock.calls[0];
    expect(opts.credentials).toBe("include");
  });

  it("encodes URL params correctly", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ pins: [] }),
    });
    global.fetch = fetchMock;
    await listPins("my profile", "tab/1");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("profile_id=my+profile");
    expect(url).toContain("tab_id=tab%2F1");
  });
});

describe("pinAgent", () => {
  it("returns { pinned: true } on 200", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ pinned: true }),
    });

    const result = await pinAgent("profile-1", "tab-1", "agent-1");
    expect(result).toEqual({ pinned: true });
  });

  it("returns { error } on 400 with body", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: "max pins reached" }),
    });

    const result = await pinAgent("profile-1", "tab-1", "agent-1");
    expect(result).toEqual({ error: "max pins reached" });
  });

  it("returns { error } on 404", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ error: "agent not found" }),
    });

    const result = await pinAgent("profile-1", "tab-1", "agent-unknown");
    expect(result).toEqual({ error: "agent not found" });
  });

  it("returns null on 401", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({}),
    });

    const result = await pinAgent("profile-1", "tab-1", "agent-1");
    expect(result).toBeNull();
  });

  it("returns null on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await pinAgent("profile-1", "tab-1", "agent-1")).toBeNull();
  });

  it("posts JSON body with all three fields", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ pinned: true }),
    });
    global.fetch = fetchMock;

    await pinAgent("profile-1", "tab-1", "agent-1");

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/desktop/browser/pins");
    expect(opts.method).toBe("POST");
    expect(opts.headers["content-type"]).toBe("application/json");
    const body = JSON.parse(opts.body);
    expect(body.profile_id).toBe("profile-1");
    expect(body.tab_id).toBe("tab-1");
    expect(body.agent_id).toBe("agent-1");
  });
});

describe("unpinAgent", () => {
  it("returns true on 204", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 204 });
    global.fetch = fetchMock;

    expect(await unpinAgent("profile-1", "tab-1", "agent-1")).toBe(true);

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/desktop/browser/pins");
    expect(opts.method).toBe("DELETE");
  });

  it("returns false on 401", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401 });
    expect(await unpinAgent("profile-1", "tab-1", "agent-1")).toBe(false);
  });

  it("returns false on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await unpinAgent("profile-1", "tab-1", "agent-1")).toBe(false);
  });
});

describe("listAgents", () => {
  it("returns array on 200", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [
        { id: "agent-1", name: "Alpha" },
        { id: "agent-2", name: "Beta" },
      ],
    });
    const result = await listAgents();
    expect(result).toHaveLength(2);
    expect(result[0].id).toBe("agent-1");
    expect(result[0].name).toBe("Alpha");
  });

  it("returns [] on 401", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401 });
    expect(await listAgents()).toEqual([]);
  });

  it("returns [] on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await listAgents()).toEqual([]);
  });

  it("normalises shape to { id, name } from agent dicts that have only name", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [{ name: "my-agent" }],
    });
    const result = await listAgents();
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("my-agent");
    expect(result[0].name).toBe("my-agent");
  });
});

describe("mintCopilotTicket", () => {
  it("returns { ticket, ttl_seconds } on 200", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ ticket: "tok_abc123", ttl_seconds: 30 }),
    });

    const result = await mintCopilotTicket("profile-1", "tab-1", "agent-1");
    expect(result).toEqual({ ticket: "tok_abc123", ttl_seconds: 30 });
  });

  it("returns null on 403", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 403 });
    expect(await mintCopilotTicket("profile-1", "tab-1", "agent-1")).toBeNull();
  });

  it("returns null on 401", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401 });
    expect(await mintCopilotTicket("profile-1", "tab-1", "agent-1")).toBeNull();
  });

  it("returns null on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await mintCopilotTicket("profile-1", "tab-1", "agent-1")).toBeNull();
  });
});
