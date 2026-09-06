import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fetchStarred,
  fetchNotifications,
  fetchRepo,
  fetchIssues,
  fetchIssue,
  fetchReleases,
  getAuthStatus,
  saveToLibrary,
  startDeviceFlow,
  pollDeviceFlow,
  listIdentities,
  deleteIdentity,
} from "./github";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("fetchStarred", () => {
  it("returns repos and total on 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        repos: [
          { owner: "a", name: "b", description: "d", stars: 1, forks: 2, language: "ts", license: "MIT", updated_at: "2024-01-01", topics: [] },
        ],
        total: 1,
      }),
    });
    global.fetch = fetchMock;

    const result = await fetchStarred(1);
    expect(result.repos).toHaveLength(1);
    expect(result.repos[0].owner).toBe("a");
    expect(result.total).toBe(1);

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/github/starred");
    expect(url).toContain("page=1");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
  });

  it("returns empty result on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      headers: new Map(),
    });
    const result = await fetchStarred();
    expect(result).toEqual({ repos: [], total: 0 });
  });

  it("returns empty result on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("network failure"));
    const result = await fetchStarred();
    expect(result).toEqual({ repos: [], total: 0 });
  });

  it("omits page param when not provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ repos: [], total: 0 }),
    });
    global.fetch = fetchMock;
    await fetchStarred();
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/github/starred");
  });
});

describe("fetchNotifications", () => {
  it("returns notifications and unread_count on 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        notifications: [
          { number: 1, title: "bug", state: "open", author: "user", body: "", labels: [], comments: [], created_at: "2024-01-01", repo: "o/r", is_pull_request: false },
        ],
        unread_count: 5,
      }),
    });
    global.fetch = fetchMock;

    const result = await fetchNotifications();
    expect(result.notifications).toHaveLength(1);
    expect(result.notifications[0].title).toBe("bug");
    expect(result.unread_count).toBe(5);

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/github/notifications");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
  });

  it("returns empty result on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      headers: new Map(),
    });
    const result = await fetchNotifications();
    expect(result).toEqual({ notifications: [], unread_count: 0 });
  });

  it("returns empty result on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    const result = await fetchNotifications();
    expect(result).toEqual({ notifications: [], unread_count: 0 });
  });
});

describe("fetchRepo", () => {
  it("returns repo object on 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        owner: "octocat", name: "hello-world", description: "Hi", stars: 10,
        forks: 2, language: "Python", license: "MIT", updated_at: "2024-01-01", topics: ["demo"],
      }),
    });
    global.fetch = fetchMock;

    const result = await fetchRepo("octocat", "hello-world");
    expect(result).not.toBeNull();
    expect(result!.owner).toBe("octocat");
    expect(result!.name).toBe("hello-world");
    expect(result!.stars).toBe(10);

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/github/repo/octocat/hello-world");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
  });

  it("returns null on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      headers: new Map(),
    });
    expect(await fetchRepo("octocat", "nope")).toBeNull();
  });

  it("returns null on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await fetchRepo("octocat", "hello-world")).toBeNull();
  });

  it("encodes owner and repo in the path", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        owner: "a/b", name: "c d", description: "", stars: 0,
        forks: 0, language: "", license: "", updated_at: "", topics: [],
      }),
    });
    global.fetch = fetchMock;
    await fetchRepo("a/b", "c d");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("a%2Fb");
    expect(url).toContain("c%20d");
  });
});

describe("fetchIssues", () => {
  it("returns issues and total on 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        issues: [
          { number: 1, title: "issue", state: "open", author: "u", body: "", labels: [], comments: [], created_at: "2024-01-01", repo: "o/r", is_pull_request: false },
        ],
        total: 1,
      }),
    });
    global.fetch = fetchMock;

    const result = await fetchIssues("octocat", "hello-world", "open", 2);
    expect(result.issues).toHaveLength(1);
    expect(result.total).toBe(1);

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/github/repo/octocat/hello-world/issues");
    expect(url).toContain("state=open");
    expect(url).toContain("page=2");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
  });

  it("returns empty result on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      headers: new Map(),
    });
    const result = await fetchIssues("octocat", "hello-world");
    expect(result).toEqual({ issues: [], total: 0 });
  });

  it("returns empty result on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    const result = await fetchIssues("octocat", "hello-world");
    expect(result).toEqual({ issues: [], total: 0 });
  });

  it("omits optional params when not provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ issues: [], total: 0 }),
    });
    global.fetch = fetchMock;
    await fetchIssues("octocat", "hello-world");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/github/repo/octocat/hello-world/issues");
  });
});

describe("fetchIssue", () => {
  it("returns issue object on 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        number: 42, title: "bug", state: "open", author: "u", body: "details", labels: ["bug"],
        comments: [], created_at: "2024-01-01", repo: "o/r", is_pull_request: false,
      }),
    });
    global.fetch = fetchMock;

    const result = await fetchIssue("octocat", "hello-world", 42);
    expect(result).not.toBeNull();
    expect(result!.number).toBe(42);
    expect(result!.title).toBe("bug");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/github/repo/octocat/hello-world/issues/42");
  });

  it("returns null on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      headers: new Map(),
    });
    expect(await fetchIssue("octocat", "hello-world", 999)).toBeNull();
  });

  it("returns null on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await fetchIssue("octocat", "hello-world", 1)).toBeNull();
  });
});

describe("fetchReleases", () => {
  it("returns releases array on 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        releases: [
          { tag: "v1.0", name: "v1.0", body: "release", author: "u", published_at: "2024-01-01", assets: [], prerelease: false },
        ],
      }),
    });
    global.fetch = fetchMock;

    const result = await fetchReleases("octocat", "hello-world");
    expect(result).toHaveLength(1);
    expect(result[0].tag).toBe("v1.0");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/github/repo/octocat/hello-world/releases");
  });

  it("returns empty array on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      headers: new Map(),
    });
    const result = await fetchReleases("octocat", "hello-world");
    expect(result).toEqual([]);
  });

  it("returns empty array on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    const result = await fetchReleases("octocat", "hello-world");
    expect(result).toEqual([]);
  });
});

describe("getAuthStatus", () => {
  it("returns auth status on 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ authenticated: true, username: "octocat", method: "oauth" }),
    });
    global.fetch = fetchMock;

    const result = await getAuthStatus();
    expect(result.authenticated).toBe(true);
    expect(result.username).toBe("octocat");
    expect(result.method).toBe("oauth");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/github/auth/status");
  });

  it("returns default on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      headers: new Map(),
    });
    const result = await getAuthStatus();
    expect(result).toEqual({ authenticated: false });
  });

  it("returns default on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    const result = await getAuthStatus();
    expect(result).toEqual({ authenticated: false });
  });
});

describe("saveToLibrary", () => {
  it("returns id and status on 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: "lib-1", status: "ok" }),
    });
    global.fetch = fetchMock;

    const result = await saveToLibrary("https://github.com/octocat/hello-world");
    expect(result).not.toBeNull();
    expect(result!.id).toBe("lib-1");
    expect(result!.status).toBe("ok");

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/knowledge/ingest");
    expect(opts.method).toBe("POST");
    expect((opts.headers?.get?.("Content-Type") ?? opts.headers?.["Content-Type"])).toBe("application/json");
    const body = JSON.parse(opts.body);
    expect(body.url).toBe("https://github.com/octocat/hello-world");
    expect(body.source).toBe("github-browser");
    expect(body.title).toBe("");
    expect(body.text).toBe("");
    expect(body.categories).toEqual([]);
  });

  it("returns null on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      headers: new Map(),
    });
    const result = await saveToLibrary("https://example.com");
    expect(result).toBeNull();
  });

  it("returns null on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    const result = await saveToLibrary("https://example.com");
    expect(result).toBeNull();
  });
});

describe("startDeviceFlow", () => {
  it("returns device start data on 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        user_code: "ABCD-1234",
        verification_uri: "https://github.com/login/device",
        device_code: "dev-code",
        interval: 5,
        expires_in: 900,
      }),
    });
    global.fetch = fetchMock;

    const result = await startDeviceFlow();
    expect(result.user_code).toBe("ABCD-1234");
    expect(result.verification_uri).toBe("https://github.com/login/device");
    expect(result.interval).toBe(5);

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/github/oauth/device/start");
    expect(opts.method).toBe("POST");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
  });

  it("throws on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      headers: new Map(),
    });
    await expect(startDeviceFlow()).rejects.toThrow("Failed to start GitHub connect");
  });
});

describe("pollDeviceFlow", () => {
  it("returns identity on connected", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        status: "connected",
        identity: { id: "gid-1", login: "octocat", avatar_url: "https://img", created_at: 1700000000 },
      }),
    });
    global.fetch = fetchMock;

    const result = await pollDeviceFlow("dev-code");
    expect(result.status).toBe("connected");
    if (result.status === "connected") {
      expect(result.identity.login).toBe("octocat");
    }

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/github/oauth/device/poll");
    expect(opts.method).toBe("POST");
    expect((opts.headers?.get?.("Content-Type") ?? opts.headers?.["Content-Type"])).toBe("application/json");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
    const body = JSON.parse(opts.body);
    expect(body.device_code).toBe("dev-code");
  });

  it("returns error on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      headers: new Map(),
    });
    const result = await pollDeviceFlow("dev-code");
    expect(result).toEqual({ status: "error", error: "poll_failed" });
  });
});

describe("listIdentities", () => {
  it("returns identities array on 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ([
        { id: "gid-1", login: "octocat", avatar_url: "https://img", created_at: 1700000000 },
      ]),
    });
    global.fetch = fetchMock;

    const result = await listIdentities();
    expect(result).toHaveLength(1);
    expect(result[0].login).toBe("octocat");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/github/identities");
  });

  it("returns empty array on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      headers: new Map(),
    });
    const result = await listIdentities();
    expect(result).toEqual([]);
  });

  it("returns empty array on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    const result = await listIdentities();
    expect(result).toEqual([]);
  });
});

describe("deleteIdentity", () => {
  it("returns true on 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map(),
    });
    global.fetch = fetchMock;

    const result = await deleteIdentity("gid-1");
    expect(result).toBe(true);

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/github/identities/gid-1");
    expect(opts.method).toBe("DELETE");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
  });

  it("returns false on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      headers: new Map(),
    });
    const result = await deleteIdentity("gid-1");
    expect(result).toBe(false);
  });

  it("encodes id in the path", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map(),
    });
    global.fetch = fetchMock;
    await deleteIdentity("id/with/slashes");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("id%2Fwith%2Fslashes");
  });
});
