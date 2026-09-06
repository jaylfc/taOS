import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchThread,
  fetchSubreddit,
  searchReddit,
  fetchSaved,
  getAuthStatus,
  saveToLibrary,
} from "./reddit";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("fetchThread", () => {
  it("calls GET /api/reddit/thread with url param and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        post: {
          id: "abc1",
          subreddit: "typescript",
          title: "TS 6.0",
          author: "user1",
          selftext: "content",
          score: 100,
          upvote_ratio: 0.95,
          num_comments: 25,
          created_utc: 1700000000,
          url: "https://reddit.com/r/typescript/comments/abc1",
          permalink: "/r/typescript/comments/abc1",
          flair: "News",
          is_self: true,
        },
        comments: [],
      }),
    });
    global.fetch = fetchMock;

    const result = await fetchThread("https://reddit.com/r/typescript/comments/abc1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/reddit/thread?");
    expect(url).toContain("url=https%3A%2F%2Freddit.com");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
    expect(result).not.toBeNull();
    expect(result!.post.id).toBe("abc1");
    expect(result!.post.subreddit).toBe("typescript");
    expect(result!.comments).toEqual([]);
  });

  it("returns null on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
    });

    const result = await fetchThread("https://reddit.com/r/test");
    expect(result).toBeNull();
  });

  it("returns null on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("network failure"));
    const result = await fetchThread("https://reddit.com/r/test");
    expect(result).toBeNull();
  });

  it("returns null when content-type is not json", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "text/html"]]),
      json: async () => ({}),
    });
    global.fetch = fetchMock;

    const result = await fetchThread("https://reddit.com/r/test");
    expect(result).toBeNull();
  });
});

describe("fetchSubreddit", () => {
  it("calls GET /api/reddit/subreddit with name and sort params", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        posts: [
          {
            id: "p1",
            subreddit: "rust",
            title: "Rust 2024",
            author: "ferris",
            selftext: "",
            score: 500,
            upvote_ratio: 0.98,
            num_comments: 100,
            created_utc: 1700000000,
            url: "https://example.com",
            permalink: "/r/rust/comments/p1",
            flair: "",
            is_self: false,
          },
        ],
        after: "t3_next",
      }),
    });
    global.fetch = fetchMock;

    const result = await fetchSubreddit("rust", "hot");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/reddit/subreddit?");
    expect(url).toContain("name=rust");
    expect(url).toContain("sort=hot");
    expect(result.posts).toHaveLength(1);
    expect(result.posts[0].id).toBe("p1");
    expect(result.after).toBe("t3_next");
  });

  it("includes after param when provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ posts: [], after: null }),
    });
    global.fetch = fetchMock;

    await fetchSubreddit("rust", "new", "t3_cursor");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("after=t3_cursor");
  });

  it("returns empty listing on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
    });

    const result = await fetchSubreddit("rust");
    expect(result.posts).toEqual([]);
    expect(result.after).toBeNull();
  });

  it("returns empty listing on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    const result = await fetchSubreddit("rust");
    expect(result.posts).toEqual([]);
    expect(result.after).toBeNull();
  });

  it("returns empty posts when data.posts is not an array", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ posts: null, after: null }),
    });

    const result = await fetchSubreddit("rust");
    expect(result.posts).toEqual([]);
  });
});

describe("searchReddit", () => {
  it("calls GET /api/reddit/search with query param", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        posts: [
          {
            id: "s1",
            subreddit: "python",
            title: "Python tips",
            author: "dev",
            selftext: "tips",
            score: 200,
            upvote_ratio: 0.9,
            num_comments: 50,
            created_utc: 1700000000,
            url: "https://example.com",
            permalink: "/r/python/comments/s1",
            flair: "Tips",
            is_self: true,
          },
        ],
        after: null,
      }),
    });
    global.fetch = fetchMock;

    const result = await searchReddit("python tips");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/reddit/search?");
    expect(url).toContain("q=python+tips");
    expect(result.posts).toHaveLength(1);
    expect(result.posts[0].title).toBe("Python tips");
  });

  it("includes subreddit param when provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ posts: [], after: null }),
    });
    global.fetch = fetchMock;

    await searchReddit("async", "typescript");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("subreddit=typescript");
  });

  it("returns empty listing on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
    });

    const result = await searchReddit("test");
    expect(result.posts).toEqual([]);
    expect(result.after).toBeNull();
  });

  it("returns empty listing on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    const result = await searchReddit("test");
    expect(result.posts).toEqual([]);
  });
});

describe("fetchSaved", () => {
  it("calls GET /api/reddit/saved and returns parsed listing", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        posts: [
          {
            id: "sv1",
            subreddit: "programming",
            title: "Saved post",
            author: "coder",
            selftext: "saved",
            score: 300,
            upvote_ratio: 0.85,
            num_comments: 10,
            created_utc: 1700000000,
            url: "https://example.com",
            permalink: "/r/programming/comments/sv1",
            flair: "",
            is_self: true,
          },
        ],
        after: "t3_more",
      }),
    });
    global.fetch = fetchMock;

    const result = await fetchSaved();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/reddit/saved");
    expect(result.posts).toHaveLength(1);
    expect(result.posts[0].id).toBe("sv1");
    expect(result.after).toBe("t3_more");
  });

  it("includes after param when provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ posts: [], after: null }),
    });
    global.fetch = fetchMock;

    await fetchSaved("t3_cursor");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("after=t3_cursor");
  });

  it("returns empty listing on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
    });

    const result = await fetchSaved();
    expect(result.posts).toEqual([]);
    expect(result.after).toBeNull();
  });

  it("returns empty listing on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    const result = await fetchSaved();
    expect(result.posts).toEqual([]);
  });
});

describe("getAuthStatus", () => {
  it("calls GET /api/reddit/auth/status and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ authenticated: true, username: "testuser" }),
    });
    global.fetch = fetchMock;

    const result = await getAuthStatus();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/reddit/auth/status");
    expect(result.authenticated).toBe(true);
    expect(result.username).toBe("testuser");
  });

  it("returns authenticated false on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
    });

    const result = await getAuthStatus();
    expect(result.authenticated).toBe(false);
  });

  it("returns authenticated false on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    const result = await getAuthStatus();
    expect(result.authenticated).toBe(false);
  });
});

describe("saveToLibrary", () => {
  it("posts to /api/knowledge/ingest and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: "lib-1", status: "ingested" }),
    });
    global.fetch = fetchMock;

    const result = await saveToLibrary(
      "https://reddit.com/r/test/comments/abc",
      "Test Title",
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/knowledge/ingest");
    expect(opts.method).toBe("POST");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
    const body = JSON.parse(opts.body);
    expect(body.url).toBe("https://reddit.com/r/test/comments/abc");
    expect(body.title).toBe("Test Title");
    expect(body.text).toBe("");
    expect(body.categories).toEqual([]);
    expect(body.source).toBe("reddit-client");
    expect(result).not.toBeNull();
    expect(result!.id).toBe("lib-1");
    expect(result!.status).toBe("ingested");
  });

  it("uses empty string for title when not provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: "lib-2", status: "ok" }),
    });
    global.fetch = fetchMock;

    await saveToLibrary("https://reddit.com/r/test");

    const [, opts] = fetchMock.mock.calls[0];
    const body = JSON.parse(opts.body);
    expect(body.title).toBe("");
  });

  it("returns null on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
    });

    const result = await saveToLibrary("https://reddit.com/r/test");
    expect(result).toBeNull();
  });

  it("returns null on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    const result = await saveToLibrary("https://reddit.com/r/test");
    expect(result).toBeNull();
  });

  it("returns null when content-type is not json", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "text/plain"]]),
      json: async () => ({}),
    });

    const result = await saveToLibrary("https://reddit.com/r/test");
    expect(result).toBeNull();
  });
});
