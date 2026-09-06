import { afterEach, describe, expect, it, vi } from "vitest";
import {
  listProfiles,
  createProfile,
  deleteProfile,
  deleteProfileData,
  startBrowser,
  stopBrowser,
  getScreenshot,
  getCookies,
  getLoginStatus,
  assignAgent,
  moveToNode,
} from "./agent-browsers";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("listProfiles", () => {
  it("returns profiles array on 200", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        profiles: [
          { id: "p-1", agent_name: "a1", profile_name: "prof1", node: "local", status: "stopped", container_id: null, created_at: 1, updated_at: 2 },
          { id: "p-2", agent_name: null, profile_name: "prof2", node: "remote", status: "running", container_id: "c1", created_at: 3, updated_at: 4 },
        ],
      }),
    });

    const result = await listProfiles();
    expect(result).toHaveLength(2);
    expect(result[0].id).toBe("p-1");
    expect(result[1].status).toBe("running");
  });

  it("returns [] on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await listProfiles()).toEqual([]);
  });

  it("returns [] on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("network failure"));
    expect(await listProfiles()).toEqual([]);
  });

  it("returns [] when body.profiles is not an array", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ profiles: null }),
    });
    expect(await listProfiles()).toEqual([]);
  });

  it("includes agent_name as query param when provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ profiles: [] }),
    });
    global.fetch = fetchMock;
    await listProfiles("my-agent");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("agent_name=my-agent");
  });

  it("encodes agent_name with spaces", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ profiles: [] }),
    });
    global.fetch = fetchMock;
    await listProfiles("my agent");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("agent_name=my%20agent");
  });
});

describe("createProfile", () => {
  it("returns parsed body on 200", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: "p-new", status: "stopped" }),
    });

    const result = await createProfile("prof1", "agent1", "node1");
    expect(result).toEqual({ id: "p-new", status: "stopped" });
  });

  it("returns null on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 400 });
    expect(await createProfile("prof1")).toBeNull();
  });

  it("returns null on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await createProfile("prof1")).toBeNull();
  });

  it("returns null when content-type is not json", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "text/html"]]),
      json: async () => ({ id: "p-new", status: "stopped" }),
    });
    expect(await createProfile("prof1")).toBeNull();
  });

  it("posts JSON body with profile_name, agent_name, node", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: "p-1", status: "stopped" }),
    });
    global.fetch = fetchMock;

    await createProfile("my-prof", "my-agent", "my-node");

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/agent-browsers/profiles");
    expect(opts.method).toBe("POST");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    const body = JSON.parse(opts.body);
    expect(body.profile_name).toBe("my-prof");
    expect(body.agent_name).toBe("my-agent");
    expect(body.node).toBe("my-node");
  });

  it("defaults agent_name to null and node to local", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: "p-1", status: "stopped" }),
    });
    global.fetch = fetchMock;

    await createProfile("my-prof");

    const [, opts] = fetchMock.mock.calls[0];
    const body = JSON.parse(opts.body);
    expect(body.agent_name).toBeNull();
    expect(body.node).toBe("local");
  });
});

describe("deleteProfile", () => {
  it("returns true on 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    expect(await deleteProfile("p-1")).toBe(true);

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/agent-browsers/profiles/p-1");
    expect(opts.method).toBe("DELETE");
  });

  it("returns false on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 404 });
    expect(await deleteProfile("p-1")).toBe(false);
  });

  it("returns false on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await deleteProfile("p-1")).toBe(false);
  });

  it("encodes id in the path", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;
    await deleteProfile("p/with/slashes");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("p%2Fwith%2Fslashes");
  });
});

describe("deleteProfileData", () => {
  it("returns true on 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    expect(await deleteProfileData("p-1")).toBe(true);

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/agent-browsers/profiles/p-1/data");
    expect(opts.method).toBe("DELETE");
  });

  it("returns false on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 404 });
    expect(await deleteProfileData("p-1")).toBe(false);
  });

  it("returns false on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await deleteProfileData("p-1")).toBe(false);
  });
});

describe("startBrowser", () => {
  it("returns parsed body on 200", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: "p-1", status: "running" }),
    });

    const result = await startBrowser("p-1");
    expect(result).toEqual({ id: "p-1", status: "running" });
  });

  it("returns null on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await startBrowser("p-1")).toBeNull();
  });

  it("returns null on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await startBrowser("p-1")).toBeNull();
  });

  it("posts to the start endpoint with empty body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: "p-1", status: "running" }),
    });
    global.fetch = fetchMock;

    await startBrowser("p-1");

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/agent-browsers/profiles/p-1/start");
    expect(opts.method).toBe("POST");
    expect(opts.body).toBe("{}");
  });
});

describe("stopBrowser", () => {
  it("returns parsed body on 200", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: "p-1", status: "stopped" }),
    });

    const result = await stopBrowser("p-1");
    expect(result).toEqual({ id: "p-1", status: "stopped" });
  });

  it("returns null on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await stopBrowser("p-1")).toBeNull();
  });

  it("returns null on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await stopBrowser("p-1")).toBeNull();
  });

  it("posts to the stop endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: "p-1", status: "stopped" }),
    });
    global.fetch = fetchMock;

    await stopBrowser("p-1");

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/agent-browsers/profiles/p-1/stop");
    expect(opts.method).toBe("POST");
  });
});

describe("getScreenshot", () => {
  it("returns data string on 200", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ data: "base64data" }),
    });

    const result = await getScreenshot("p-1");
    expect(result).toBe("base64data");
  });

  it("returns null when data field is missing", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({}),
    });

    expect(await getScreenshot("p-1")).toBeNull();
  });

  it("returns null on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await getScreenshot("p-1")).toBeNull();
  });

  it("returns null on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await getScreenshot("p-1")).toBeNull();
  });

  it("encodes id in the path", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ data: "x" }),
    });
    global.fetch = fetchMock;
    await getScreenshot("p-1");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/agent-browsers/profiles/p-1/screenshot");
  });
});

describe("getCookies", () => {
  it("returns cookies array on 200", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        cookies: [
          { name: "sid", value: "abc", domain: ".example.com", path: "/", expires: 100, httpOnly: true, secure: true },
        ],
      }),
    });

    const result = await getCookies("agent1", "prof1", ".example.com");
    expect(result).toHaveLength(1);
    expect(result[0].name).toBe("sid");
  });

  it("returns [] on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await getCookies("agent1", "prof1", ".example.com")).toEqual([]);
  });

  it("returns [] on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await getCookies("agent1", "prof1", ".example.com")).toEqual([]);
  });

  it("returns [] when body.cookies is not an array", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ cookies: null }),
    });
    expect(await getCookies("agent1", "prof1", ".example.com")).toEqual([]);
  });

  it("encodes agent, profile, and domain in the URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ cookies: [] }),
    });
    global.fetch = fetchMock;
    await getCookies("my agent", "my prof", ".example.com");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/agent-browsers/my%20agent/my%20prof/cookies");
    expect(url).toContain("domain=.example.com");
  });
});

describe("getLoginStatus", () => {
  it("returns parsed body on 200", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ x: true, github: false, youtube: true, reddit: false }),
    });

    const result = await getLoginStatus("p-1");
    expect(result).toEqual({ x: true, github: false, youtube: true, reddit: false });
  });

  it("returns null on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401 });
    expect(await getLoginStatus("p-1")).toBeNull();
  });

  it("returns null on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await getLoginStatus("p-1")).toBeNull();
  });

  it("returns null when content-type is not json", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "text/html"]]),
      json: async () => ({ x: true }),
    });
    expect(await getLoginStatus("p-1")).toBeNull();
  });

  it("encodes id in the path", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ x: false, github: false, youtube: false, reddit: false }),
    });
    global.fetch = fetchMock;
    await getLoginStatus("p-1");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/agent-browsers/profiles/p-1/login-status");
  });
});

describe("assignAgent", () => {
  it("returns parsed body on 200", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: "p-1", agent_name: "new-agent", profile_name: "prof1", node: "local", status: "running", container_id: null, created_at: 1, updated_at: 2 }),
    });

    const result = await assignAgent("p-1", "new-agent");
    expect(result).toEqual(expect.objectContaining({ id: "p-1", agent_name: "new-agent" }));
  });

  it("returns null on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await assignAgent("p-1", "new-agent")).toBeNull();
  });

  it("returns null on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await assignAgent("p-1", "new-agent")).toBeNull();
  });

  it("puts agent_name in the body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: "p-1", agent_name: "new-agent", profile_name: "prof1", node: "local", status: "running", container_id: null, created_at: 1, updated_at: 2 }),
    });
    global.fetch = fetchMock;

    await assignAgent("p-1", "new-agent");

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/agent-browsers/profiles/p-1/assign");
    expect(opts.method).toBe("PUT");
    const body = JSON.parse(opts.body);
    expect(body.agent_name).toBe("new-agent");
  });
});

describe("moveToNode", () => {
  it("returns parsed body on 200", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: "p-1", agent_name: "a1", profile_name: "prof1", node: "remote", status: "running", container_id: null, created_at: 1, updated_at: 2 }),
    });

    const result = await moveToNode("p-1", "remote");
    expect(result).toEqual(expect.objectContaining({ id: "p-1", node: "remote" }));
  });

  it("returns null on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await moveToNode("p-1", "remote")).toBeNull();
  });

  it("returns null on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await moveToNode("p-1", "remote")).toBeNull();
  });

  it("puts node in the body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: "p-1", agent_name: "a1", profile_name: "prof1", node: "remote", status: "running", container_id: null, created_at: 1, updated_at: 2 }),
    });
    global.fetch = fetchMock;

    await moveToNode("p-1", "remote");

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/agent-browsers/profiles/p-1/move");
    expect(opts.method).toBe("PUT");
    const body = JSON.parse(opts.body);
    expect(body.node).toBe("remote");
  });
});
