import { afterEach, describe, expect, it, vi } from "vitest";
import {
  patchChannel,
  addChannelMember,
  removeChannelMember,
  muteAgent,
  unmuteAgent,
} from "./channel-admin-api";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("patchChannel", () => {
  it("sends PATCH with correct URL and body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    await patchChannel("ch-1", { topic: "hello", max_hops: 3 });

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/chat/channels/ch-1");
    expect(opts.method).toBe("PATCH");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    const body = JSON.parse(opts.body);
    expect(body.topic).toBe("hello");
    expect(body.max_hops).toBe(3);
  });

  it("throws on non-ok with server error message", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: "bad request" }),
    });

    await expect(patchChannel("ch-1", { topic: "x" })).rejects.toThrow(
      "bad request",
    );
  });

  it("throws on non-ok without error body", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => null,
    });

    await expect(patchChannel("ch-1", {})).rejects.toThrow("HTTP 500");
  });

  it("encodes channelId in the path", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    await patchChannel("ch/with/slashes", {});

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain(encodeURIComponent("ch/with/slashes"));
  });
});

describe("addChannelMember", () => {
  it("sends POST with action add and correct slug", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    await addChannelMember("ch-1", "agent-alpha");

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/chat/channels/ch-1/members");
    expect(opts.method).toBe("POST");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    const body = JSON.parse(opts.body);
    expect(body.action).toBe("add");
    expect(body.slug).toBe("agent-alpha");
  });

  it("throws on non-ok", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({ error: "forbidden" }),
    });

    await expect(addChannelMember("ch-1", "agent-x")).rejects.toThrow(
      "forbidden",
    );
  });

  it("encodes channelId in the path", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    await addChannelMember("ch/1", "slug");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain(encodeURIComponent("ch/1"));
  });
});

describe("removeChannelMember", () => {
  it("sends POST with action remove and correct slug", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    await removeChannelMember("ch-1", "agent-beta");

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/chat/channels/ch-1/members");
    expect(opts.method).toBe("POST");
    const body = JSON.parse(opts.body);
    expect(body.action).toBe("remove");
    expect(body.slug).toBe("agent-beta");
  });

  it("throws on non-ok", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ error: "not found" }),
    });

    await expect(removeChannelMember("ch-1", "agent-x")).rejects.toThrow(
      "not found",
    );
  });

  it("encodes channelId in the path", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    await removeChannelMember("ch/1", "slug");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain(encodeURIComponent("ch/1"));
  });
});

describe("muteAgent", () => {
  it("sends POST to muted endpoint with action add", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    await muteAgent("ch-1", "agent-gamma");

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/chat/channels/ch-1/muted");
    expect(opts.method).toBe("POST");
    const body = JSON.parse(opts.body);
    expect(body.action).toBe("add");
    expect(body.slug).toBe("agent-gamma");
  });

  it("throws on non-ok", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => null,
    });

    await expect(muteAgent("ch-1", "agent-x")).rejects.toThrow("HTTP 500");
  });

  it("encodes channelId in the path", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    await muteAgent("ch/1", "slug");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain(encodeURIComponent("ch/1"));
  });
});

describe("unmuteAgent", () => {
  it("sends POST to muted endpoint with action remove", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    await unmuteAgent("ch-1", "agent-delta");

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/chat/channels/ch-1/muted");
    expect(opts.method).toBe("POST");
    const body = JSON.parse(opts.body);
    expect(body.action).toBe("remove");
    expect(body.slug).toBe("agent-delta");
  });

  it("throws on non-ok", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ error: "conflict" }),
    });

    await expect(unmuteAgent("ch-1", "agent-x")).rejects.toThrow("conflict");
  });

  it("encodes channelId in the path", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    await unmuteAgent("ch/1", "slug");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain(encodeURIComponent("ch/1"));
  });
});
