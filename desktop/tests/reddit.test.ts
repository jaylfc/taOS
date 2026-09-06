import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  fetchThread,
  fetchSubreddit,
  searchReddit,
  fetchSaved,
  getAuthStatus,
  saveToLibrary,
} from "../src/lib/reddit";
import type {
  RedditThread,
  RedditListing,
  RedditAuthStatus,
} from "../src/lib/reddit";

/* ------------------------------------------------------------------ */
/*  Mock data                                                          */
/* ------------------------------------------------------------------ */

const MOCK_POST = {
  id: "abc123",
  subreddit: "LocalLLaMA",
  title: "Test post about LLMs",
  author: "testuser",
  selftext: "This is the body of the post.",
  score: 420,
  upvote_ratio: 0.95,
  num_comments: 42,
  created_utc: 1700000000,
  url: "https://www.reddit.com/r/LocalLLaMA/comments/abc123/test_post_about_llms/",
  permalink: "/r/LocalLLaMA/comments/abc123/test_post_about_llms/",
  flair: "Discussion",
  is_self: true,
};

const MOCK_COMMENT = {
  id: "cmt1",
  author: "commenter1",
  body: "Great post!",
  score: 10,
  created_utc: 1700001000,
  depth: 0,
  parent_id: "t3_abc123",
  replies: [],
  edited: false,
  distinguished: null,
};

const MOCK_THREAD: RedditThread = {
  post: MOCK_POST,
  comments: [MOCK_COMMENT],
};

const MOCK_LISTING: RedditListing = {
  posts: [MOCK_POST],
  after: "t3_xyz789",
};

const MOCK_AUTH_STATUS_AUTHED: RedditAuthStatus = {
  authenticated: true,
  username: "testuser",
};

const MOCK_AUTH_STATUS_UNAUTHED: RedditAuthStatus = {
  authenticated: false,
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
/*  fetchThread                                                        */
/* ------------------------------------------------------------------ */

describe("fetchThread", () => {
  it("returns thread data for a valid URL", async () => {
    globalThis.fetch = mockFetchJson(MOCK_THREAD);
    const result = await fetchThread(
      "https://www.reddit.com/r/LocalLLaMA/comments/abc123/test_post_about_llms/",
    );
    expect(result).not.toBeNull();
    expect(result!.post.id).toBe("abc123");
    expect(result!.comments).toHaveLength(1);
  });

  it("encodes the URL in the query string", async () => {
    globalThis.fetch = mockFetchJson(MOCK_THREAD);
    const url =
      "https://www.reddit.com/r/LocalLLaMA/comments/abc123/test_post/";
    await fetchThread(url);
    const called = (globalThis.fetch as ReturnType<typeof vi.fn>).mock
      .calls[0][0] as string;
    expect(called).toContain("/api/reddit/thread?url=");
    expect(called).toContain(encodeURIComponent(url));
  });

  it("returns null on network error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await fetchThread("https://www.reddit.com/r/test/comments/x/");
    expect(result).toBeNull();
  });

  it("returns null on non-ok response", async () => {
    globalThis.fetch = mockFetchJson({ error: "not found" }, 404);
    const result = await fetchThread("https://www.reddit.com/r/test/comments/x/");
    expect(result).toBeNull();
  });
});

/* ------------------------------------------------------------------ */
/*  fetchSubreddit                                                     */
/* ------------------------------------------------------------------ */

describe("fetchSubreddit", () => {
  it("returns a listing of posts", async () => {
    globalThis.fetch = mockFetchJson(MOCK_LISTING);
    const result = await fetchSubreddit("LocalLLaMA");
    expect(result.posts).toHaveLength(1);
    expect(result.posts[0].subreddit).toBe("LocalLLaMA");
    expect(result.after).toBe("t3_xyz789");
  });

  it("passes sort and after params", async () => {
    globalThis.fetch = mockFetchJson(MOCK_LISTING);
    await fetchSubreddit("homelab", "new", "t3_cursor");
    const called = (globalThis.fetch as ReturnType<typeof vi.fn>).mock
      .calls[0][0] as string;
    expect(called).toContain("name=homelab");
    expect(called).toContain("sort=new");
    expect(called).toContain("after=t3_cursor");
  });

  it("defaults to hot sort when no sort given", async () => {
    globalThis.fetch = mockFetchJson(MOCK_LISTING);
    await fetchSubreddit("selfhosted");
    const called = (globalThis.fetch as ReturnType<typeof vi.fn>).mock
      .calls[0][0] as string;
    expect(called).toContain("sort=hot");
  });

  it("returns empty listing on network error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await fetchSubreddit("LocalLLaMA");
    expect(result.posts).toEqual([]);
    expect(result.after).toBeNull();
  });
});

/* ------------------------------------------------------------------ */
/*  searchReddit                                                       */
/* ------------------------------------------------------------------ */

describe("searchReddit", () => {
  it("returns search results", async () => {
    globalThis.fetch = mockFetchJson(MOCK_LISTING);
    const result = await searchReddit("llama models");
    expect(result.posts).toHaveLength(1);
  });

  it("passes query param", async () => {
    globalThis.fetch = mockFetchJson(MOCK_LISTING);
    await searchReddit("llama models");
    const called = (globalThis.fetch as ReturnType<typeof vi.fn>).mock
      .calls[0][0] as string;
    expect(called).toContain("q=llama+models");
  });

  it("passes optional subreddit param", async () => {
    globalThis.fetch = mockFetchJson(MOCK_LISTING);
    await searchReddit("llama models", "LocalLLaMA");
    const called = (globalThis.fetch as ReturnType<typeof vi.fn>).mock
      .calls[0][0] as string;
    expect(called).toContain("subreddit=LocalLLaMA");
  });

  it("omits subreddit param when not given", async () => {
    globalThis.fetch = mockFetchJson(MOCK_LISTING);
    await searchReddit("query");
    const called = (globalThis.fetch as ReturnType<typeof vi.fn>).mock
      .calls[0][0] as string;
    expect(called).not.toContain("subreddit=");
  });

  it("returns empty listing on error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await searchReddit("test");
    expect(result.posts).toEqual([]);
    expect(result.after).toBeNull();
  });
});

/* ------------------------------------------------------------------ */
/*  fetchSaved                                                         */
/* ------------------------------------------------------------------ */

describe("fetchSaved", () => {
  it("returns saved posts listing", async () => {
    globalThis.fetch = mockFetchJson(MOCK_LISTING);
    const result = await fetchSaved();
    expect(result.posts).toHaveLength(1);
  });

  it("passes after param when given", async () => {
    globalThis.fetch = mockFetchJson(MOCK_LISTING);
    await fetchSaved("t3_cursor");
    const called = (globalThis.fetch as ReturnType<typeof vi.fn>).mock
      .calls[0][0] as string;
    expect(called).toContain("after=t3_cursor");
  });

  it("returns empty listing on error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await fetchSaved();
    expect(result.posts).toEqual([]);
    expect(result.after).toBeNull();
  });
});

/* ------------------------------------------------------------------ */
/*  getAuthStatus                                                      */
/* ------------------------------------------------------------------ */

describe("getAuthStatus", () => {
  it("returns authenticated status with username", async () => {
    globalThis.fetch = mockFetchJson(MOCK_AUTH_STATUS_AUTHED);
    const result = await getAuthStatus();
    expect(result.authenticated).toBe(true);
    expect(result.username).toBe("testuser");
  });

  it("returns unauthenticated status", async () => {
    globalThis.fetch = mockFetchJson(MOCK_AUTH_STATUS_UNAUTHED);
    const result = await getAuthStatus();
    expect(result.authenticated).toBe(false);
    expect(result.username).toBeUndefined();
  });

  it("returns unauthenticated on network error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await getAuthStatus();
    expect(result.authenticated).toBe(false);
  });
});

/* ------------------------------------------------------------------ */
/*  saveToLibrary                                                      */
/* ------------------------------------------------------------------ */

describe("saveToLibrary", () => {
  it("posts to knowledge ingest with source=reddit-client", async () => {
    globalThis.fetch = mockFetchJson({ id: "new-123", status: "pending" });
    const result = await saveToLibrary(
      "https://www.reddit.com/r/LocalLLaMA/comments/abc123/",
      "Test post about LLMs",
    );
    expect(result).toEqual({ id: "new-123", status: "pending" });
    const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const url = call[0] as string;
    const init = call[1] as RequestInit;
    expect(url).toBe("/api/knowledge/ingest");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string);
    expect(body.source).toBe("reddit-client");
    expect(body.url).toContain("LocalLLaMA");
    expect(body.title).toBe("Test post about LLMs");
  });

  it("works without a title", async () => {
    globalThis.fetch = mockFetchJson({ id: "new-456", status: "pending" });
    const result = await saveToLibrary(
      "https://www.reddit.com/r/homelab/comments/xyz/",
    );
    expect(result).not.toBeNull();
    const body = JSON.parse(
      (
        (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][1] as RequestInit
      ).body as string,
    );
    expect(body.source).toBe("reddit-client");
    expect(body.title).toBe("");
  });

  it("returns null on error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await saveToLibrary("https://www.reddit.com/r/test/");
    expect(result).toBeNull();
  });
});
