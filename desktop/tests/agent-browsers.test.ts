import { describe, it, expect, beforeEach, vi } from "vitest";
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
} from "../src/lib/agent-browsers";
import type { BrowserProfile, LoginStatus, CookieEntry } from "../src/lib/agent-browsers";

const MOCK_PROFILE: BrowserProfile = {
  id: "prof-1",
  agent_name: "research-agent",
  profile_name: "research-profile",
  node: "local",
  status: "stopped",
  container_id: null,
  created_at: 1700000000,
  updated_at: 1700000000,
};

const MOCK_LOGIN_STATUS: LoginStatus = {
  x: false,
  github: true,
  youtube: false,
  reddit: false,
};

const MOCK_COOKIE: CookieEntry = {
  name: "session",
  value: "abc123",
  domain: "github.com",
  path: "/",
  expires: 1800000000,
  httpOnly: true,
  secure: true,
};

function mockFetchJson(data: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    json: () => Promise.resolve(data),
  });
}

function mockFetchFail() {
  return vi.fn().mockRejectedValue(new Error("network error"));
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("listProfiles", () => {
  it("returns profiles from the API", async () => {
    globalThis.fetch = mockFetchJson({ profiles: [MOCK_PROFILE] });
    const result = await listProfiles();
    expect(result).toHaveLength(1);
    expect(result[0].profile_name).toBe("research-profile");
  });

  it("filters by agent name", async () => {
    globalThis.fetch = mockFetchJson({ profiles: [] });
    await listProfiles("research-agent");
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("agent_name=research-agent");
  });

  it("returns empty array on network error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await listProfiles();
    expect(result).toEqual([]);
  });
});

describe("createProfile", () => {
  it("posts to create profile and returns id", async () => {
    globalThis.fetch = mockFetchJson({ id: "new-prof-1", status: "created" });
    const result = await createProfile("my-profile", "research-agent", "local");
    expect(result).toEqual({ id: "new-prof-1", status: "created" });
  });

  it("sends correct body", async () => {
    globalThis.fetch = mockFetchJson({ id: "x", status: "created" });
    await createProfile("my-profile", "research-agent", "local");
    const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const body = JSON.parse(call[1].body as string);
    expect(body.profile_name).toBe("my-profile");
    expect(body.agent_name).toBe("research-agent");
    expect(body.node).toBe("local");
  });

  it("returns null on error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await createProfile("fail-profile");
    expect(result).toBeNull();
  });
});

describe("deleteProfile", () => {
  it("returns true on success", async () => {
    globalThis.fetch = mockFetchJson({ status: "deleted" });
    const result = await deleteProfile("prof-1");
    expect(result).toBe(true);
  });

  it("returns false on 404", async () => {
    globalThis.fetch = mockFetchJson({ error: "not found" }, 404);
    const result = await deleteProfile("bad-id");
    expect(result).toBe(false);
  });

  it("calls correct URL", async () => {
    globalThis.fetch = mockFetchJson({ status: "deleted" });
    await deleteProfile("prof-1");
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("/api/agent-browsers/profiles/prof-1");
  });
});

describe("startBrowser", () => {
  it("posts to start and returns profile", async () => {
    const running = { ...MOCK_PROFILE, status: "running" as const, container_id: "ctr-abc" };
    globalThis.fetch = mockFetchJson(running);
    const result = await startBrowser("prof-1");
    expect(result).not.toBeNull();
    expect(result!.status).toBe("running");
  });

  it("returns null on error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await startBrowser("prof-1");
    expect(result).toBeNull();
  });
});

describe("stopBrowser", () => {
  it("posts to stop and returns profile", async () => {
    const stopped = { ...MOCK_PROFILE, status: "stopped" as const };
    globalThis.fetch = mockFetchJson(stopped);
    const result = await stopBrowser("prof-1");
    expect(result).not.toBeNull();
    expect(result!.status).toBe("stopped");
  });

  it("returns null on error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await stopBrowser("prof-1");
    expect(result).toBeNull();
  });
});

describe("getScreenshot", () => {
  it("returns base64 data URL on success", async () => {
    globalThis.fetch = mockFetchJson({ data: "data:image/png;base64,abc123" });
    const result = await getScreenshot("prof-1");
    expect(result).toBe("data:image/png;base64,abc123");
  });

  it("returns null on error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await getScreenshot("prof-1");
    expect(result).toBeNull();
  });
});

describe("getCookies", () => {
  it("returns cookies filtered by domain", async () => {
    globalThis.fetch = mockFetchJson({ cookies: [MOCK_COOKIE] });
    const result = await getCookies("research-agent", "research-profile", "github.com");
    expect(result).toHaveLength(1);
    expect(result[0].name).toBe("session");
  });

  it("passes domain query param", async () => {
    globalThis.fetch = mockFetchJson({ cookies: [] });
    await getCookies("research-agent", "research-profile", "github.com");
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("domain=github.com");
    expect(url).toContain("research-agent");
    expect(url).toContain("research-profile");
  });
});

describe("getLoginStatus", () => {
  it("returns login status", async () => {
    globalThis.fetch = mockFetchJson(MOCK_LOGIN_STATUS);
    const result = await getLoginStatus("prof-1");
    expect(result).not.toBeNull();
    expect(result!.github).toBe(true);
    expect(result!.x).toBe(false);
  });

  it("returns null on error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await getLoginStatus("prof-1");
    expect(result).toBeNull();
  });
});

describe("assignAgent", () => {
  it("puts agent assignment and returns profile", async () => {
    const updated = { ...MOCK_PROFILE, agent_name: "new-agent" };
    globalThis.fetch = mockFetchJson(updated);
    const result = await assignAgent("prof-1", "new-agent");
    expect(result).not.toBeNull();
    expect(result!.agent_name).toBe("new-agent");
  });

  it("sends correct body", async () => {
    globalThis.fetch = mockFetchJson(MOCK_PROFILE);
    await assignAgent("prof-1", "new-agent");
    const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const body = JSON.parse(call[1].body as string);
    expect(body.agent_name).toBe("new-agent");
  });

  it("returns null on error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await assignAgent("prof-1", "some-agent");
    expect(result).toBeNull();
  });
});

describe("moveToNode", () => {
  it("puts node move and returns profile", async () => {
    const updated = { ...MOCK_PROFILE, node: "remote" };
    globalThis.fetch = mockFetchJson(updated);
    const result = await moveToNode("prof-1", "remote");
    expect(result).not.toBeNull();
    expect(result!.node).toBe("remote");
  });
});

describe("deleteProfileData", () => {
  it("returns true on success", async () => {
    globalThis.fetch = mockFetchJson({ status: "deleted" });
    const result = await deleteProfileData("prof-1");
    expect(result).toBe(true);
  });

  it("calls correct URL", async () => {
    globalThis.fetch = mockFetchJson({ status: "deleted" });
    await deleteProfileData("prof-1");
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("/api/agent-browsers/profiles/prof-1/data");
  });
});
