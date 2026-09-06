import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  fetchStarred,
  fetchNotifications,
  fetchRepo,
  fetchIssues,
  fetchIssue,
  fetchReleases,
  getAuthStatus,
  saveToLibrary,
} from "../src/lib/github";
import type {
  GitHubRepo,
  GitHubIssue,
  GitHubRelease,
  GitHubAuthStatus,
} from "../src/lib/github";

/* ------------------------------------------------------------------ */
/*  Mock data                                                          */
/* ------------------------------------------------------------------ */

const MOCK_REPO: GitHubRepo = {
  owner: "anthropics",
  name: "anthropic-sdk-python",
  description: "The official Python library for the Anthropic API",
  stars: 1200,
  forks: 120,
  language: "Python",
  license: "MIT",
  updated_at: "2024-01-01T00:00:00Z",
  topics: ["ai", "llm"],
  readme_content: "# Anthropic SDK",
};

const MOCK_ISSUE: GitHubIssue = {
  number: 42,
  title: "Fix streaming responses",
  state: "open",
  author: "octocat",
  body: "Streaming breaks when...",
  labels: ["bug", "priority:high"],
  comments: [
    {
      author: "maintainer",
      body: "Thanks for the report",
      created_at: "2024-01-02T00:00:00Z",
      reactions: { "+1": 3 },
    },
  ],
  created_at: "2024-01-01T00:00:00Z",
  repo: "anthropics/anthropic-sdk-python",
  is_pull_request: false,
};

const MOCK_RELEASE: GitHubRelease = {
  tag: "v1.2.0",
  name: "Version 1.2.0",
  body: "## What's New\n- Streaming support",
  author: "octocat",
  published_at: "2024-01-01T00:00:00Z",
  assets: [
    { name: "sdk-1.2.0.tar.gz", size: 1024000, download_count: 500 },
  ],
  prerelease: false,
};

const MOCK_AUTH: GitHubAuthStatus = {
  authenticated: true,
  username: "octocat",
  method: "token",
};

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

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

/* ------------------------------------------------------------------ */
/*  fetchStarred                                                       */
/* ------------------------------------------------------------------ */

describe("fetchStarred", () => {
  it("returns starred repos from the API", async () => {
    globalThis.fetch = mockFetchJson({ repos: [MOCK_REPO], total: 1 });
    const result = await fetchStarred();
    expect(result.repos).toHaveLength(1);
    expect(result.repos[0].name).toBe("anthropic-sdk-python");
    expect(result.total).toBe(1);
  });

  it("passes page param", async () => {
    globalThis.fetch = mockFetchJson({ repos: [], total: 0 });
    await fetchStarred(2);
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("page=2");
  });

  it("returns empty on network error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await fetchStarred();
    expect(result.repos).toEqual([]);
    expect(result.total).toBe(0);
  });
});

/* ------------------------------------------------------------------ */
/*  fetchNotifications                                                 */
/* ------------------------------------------------------------------ */

describe("fetchNotifications", () => {
  it("returns notifications", async () => {
    globalThis.fetch = mockFetchJson({ notifications: [MOCK_ISSUE], unread_count: 1 });
    const result = await fetchNotifications();
    expect(result.notifications).toHaveLength(1);
    expect(result.unread_count).toBe(1);
  });

  it("returns empty on error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await fetchNotifications();
    expect(result.notifications).toEqual([]);
    expect(result.unread_count).toBe(0);
  });
});

/* ------------------------------------------------------------------ */
/*  fetchRepo                                                          */
/* ------------------------------------------------------------------ */

describe("fetchRepo", () => {
  it("returns a single repo", async () => {
    globalThis.fetch = mockFetchJson(MOCK_REPO);
    const result = await fetchRepo("anthropics", "anthropic-sdk-python");
    expect(result).not.toBeNull();
    expect(result!.owner).toBe("anthropics");
    expect(result!.name).toBe("anthropic-sdk-python");
  });

  it("uses correct URL", async () => {
    globalThis.fetch = mockFetchJson(MOCK_REPO);
    await fetchRepo("myowner", "myrepo");
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("/api/github/repo/myowner/myrepo");
  });

  it("returns null on 404", async () => {
    globalThis.fetch = mockFetchJson({ error: "not found" }, 404);
    const result = await fetchRepo("bad", "repo");
    expect(result).toBeNull();
  });

  it("returns null on network error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await fetchRepo("owner", "repo");
    expect(result).toBeNull();
  });
});

/* ------------------------------------------------------------------ */
/*  fetchIssues                                                        */
/* ------------------------------------------------------------------ */

describe("fetchIssues", () => {
  it("returns issues list", async () => {
    globalThis.fetch = mockFetchJson({ issues: [MOCK_ISSUE], total: 1 });
    const result = await fetchIssues("anthropics", "anthropic-sdk-python");
    expect(result.issues).toHaveLength(1);
    expect(result.issues[0].number).toBe(42);
  });

  it("passes state and page params", async () => {
    globalThis.fetch = mockFetchJson({ issues: [], total: 0 });
    await fetchIssues("owner", "repo", "closed", 3);
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("state=closed");
    expect(url).toContain("page=3");
  });

  it("uses correct URL", async () => {
    globalThis.fetch = mockFetchJson({ issues: [], total: 0 });
    await fetchIssues("myowner", "myrepo");
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("/api/github/repo/myowner/myrepo/issues");
  });

  it("returns empty on error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await fetchIssues("owner", "repo");
    expect(result.issues).toEqual([]);
    expect(result.total).toBe(0);
  });
});

/* ------------------------------------------------------------------ */
/*  fetchIssue                                                         */
/* ------------------------------------------------------------------ */

describe("fetchIssue", () => {
  it("returns a single issue", async () => {
    globalThis.fetch = mockFetchJson(MOCK_ISSUE);
    const result = await fetchIssue("anthropics", "anthropic-sdk-python", 42);
    expect(result).not.toBeNull();
    expect(result!.number).toBe(42);
    expect(result!.title).toBe("Fix streaming responses");
  });

  it("uses correct URL", async () => {
    globalThis.fetch = mockFetchJson(MOCK_ISSUE);
    await fetchIssue("owner", "repo", 99);
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("/api/github/repo/owner/repo/issues/99");
  });

  it("returns null on 404", async () => {
    globalThis.fetch = mockFetchJson({ error: "not found" }, 404);
    const result = await fetchIssue("owner", "repo", 9999);
    expect(result).toBeNull();
  });
});

/* ------------------------------------------------------------------ */
/*  fetchReleases                                                      */
/* ------------------------------------------------------------------ */

describe("fetchReleases", () => {
  it("returns releases list", async () => {
    globalThis.fetch = mockFetchJson({ releases: [MOCK_RELEASE] });
    const result = await fetchReleases("anthropics", "anthropic-sdk-python");
    expect(result).toHaveLength(1);
    expect(result[0].tag).toBe("v1.2.0");
  });

  it("uses correct URL", async () => {
    globalThis.fetch = mockFetchJson({ releases: [] });
    await fetchReleases("myowner", "myrepo");
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("/api/github/repo/myowner/myrepo/releases");
  });

  it("returns empty on error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await fetchReleases("owner", "repo");
    expect(result).toEqual([]);
  });
});

/* ------------------------------------------------------------------ */
/*  getAuthStatus                                                      */
/* ------------------------------------------------------------------ */

describe("getAuthStatus", () => {
  it("returns authenticated status", async () => {
    globalThis.fetch = mockFetchJson(MOCK_AUTH);
    const result = await getAuthStatus();
    expect(result.authenticated).toBe(true);
    expect(result.username).toBe("octocat");
    expect(result.method).toBe("token");
  });

  it("returns unauthenticated on error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await getAuthStatus();
    expect(result.authenticated).toBe(false);
  });
});

/* ------------------------------------------------------------------ */
/*  saveToLibrary                                                      */
/* ------------------------------------------------------------------ */

describe("saveToLibrary", () => {
  it("posts to knowledge ingest with github-browser source", async () => {
    globalThis.fetch = mockFetchJson({ id: "gh-123", status: "pending" });
    const result = await saveToLibrary("https://github.com/owner/repo");
    expect(result).toEqual({ id: "gh-123", status: "pending" });
    const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[0]).toContain("/api/knowledge/ingest");
    const body = JSON.parse(call[1].body as string);
    expect(body.url).toBe("https://github.com/owner/repo");
    expect(body.source).toBe("github-browser");
  });

  it("returns null on error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await saveToLibrary("https://github.com/owner/repo");
    expect(result).toBeNull();
  });
});
