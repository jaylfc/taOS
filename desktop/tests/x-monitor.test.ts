import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  fetchTweet,
  fetchThread,
  getAuthStatus,
  listWatches,
  createWatch,
  updateWatch,
  deleteWatch,
  saveToLibrary,
} from "../src/lib/x-monitor";
import type { Tweet, XThread, XAuthStatus, AuthorWatch } from "../src/lib/x-monitor";

/* ------------------------------------------------------------------ */
/*  Mock data                                                          */
/* ------------------------------------------------------------------ */

const MOCK_TWEET: Tweet = {
  id: "1234567890",
  author: "Test User",
  handle: "testhandle",
  text: "Hello from tests!",
  likes: 42,
  reposts: 7,
  views: 1500,
  created_at: 1700000000,
  media: [{ type: "image", url: "https://example.com/img.jpg" }],
};

const MOCK_THREAD: XThread = {
  tweets: [MOCK_TWEET],
  text: "@testhandle\nHello from tests!",
};

const MOCK_WATCH: AuthorWatch = {
  handle: "elonmusk",
  filters: { all_posts: true, min_likes: 0, threads_only: false, media_only: false },
  frequency: 1800,
  enabled: true,
  last_check: 0,
  created_at: 1700000000,
};

const MOCK_AUTH_UNAUTHED: XAuthStatus = { authenticated: false };
const MOCK_AUTH_AUTHED: XAuthStatus = { authenticated: true, handle: "janelabs" };

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

function mockFetchBadStatus(status: number) {
  return vi.fn().mockResolvedValue({
    ok: false,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    json: () => Promise.resolve({ error: "not found" }),
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
});

/* ------------------------------------------------------------------ */
/*  fetchTweet                                                         */
/* ------------------------------------------------------------------ */

describe("fetchTweet", () => {
  it("returns tweet data on success", async () => {
    globalThis.fetch = mockFetchJson(MOCK_TWEET);
    const result = await fetchTweet("1234567890");
    expect(result).not.toBeNull();
    expect(result!.id).toBe("1234567890");
    expect(result!.handle).toBe("testhandle");
    expect(result!.likes).toBe(42);
  });

  it("includes tweet_id in the URL path", async () => {
    globalThis.fetch = mockFetchJson(MOCK_TWEET);
    await fetchTweet("9876543210");
    const called = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(called).toContain("/api/x/tweet/9876543210");
  });

  it("returns null on network error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await fetchTweet("1234567890");
    expect(result).toBeNull();
  });

  it("returns null on 404", async () => {
    globalThis.fetch = mockFetchBadStatus(404);
    const result = await fetchTweet("badid");
    expect(result).toBeNull();
  });
});

/* ------------------------------------------------------------------ */
/*  fetchThread                                                        */
/* ------------------------------------------------------------------ */

describe("fetchThread", () => {
  it("returns thread data on success", async () => {
    globalThis.fetch = mockFetchJson(MOCK_THREAD);
    const result = await fetchThread("1234567890");
    expect(result).not.toBeNull();
    expect(result!.tweets).toHaveLength(1);
    expect(result!.tweets[0].handle).toBe("testhandle");
    expect(result!.text).toContain("@testhandle");
  });

  it("includes tweet_id in the URL path", async () => {
    globalThis.fetch = mockFetchJson(MOCK_THREAD);
    await fetchThread("99999");
    const called = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(called).toContain("/api/x/thread/99999");
  });

  it("returns null on network error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await fetchThread("123");
    expect(result).toBeNull();
  });
});

/* ------------------------------------------------------------------ */
/*  getAuthStatus                                                      */
/* ------------------------------------------------------------------ */

describe("getAuthStatus", () => {
  it("returns unauthenticated status by default", async () => {
    globalThis.fetch = mockFetchJson(MOCK_AUTH_UNAUTHED);
    const result = await getAuthStatus();
    expect(result.authenticated).toBe(false);
  });

  it("returns authenticated status with handle when provided", async () => {
    globalThis.fetch = mockFetchJson(MOCK_AUTH_AUTHED);
    const result = await getAuthStatus();
    expect(result.authenticated).toBe(true);
    expect(result.handle).toBe("janelabs");
  });

  it("returns fallback on network error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await getAuthStatus();
    expect(result.authenticated).toBe(false);
  });

  it("calls the correct endpoint", async () => {
    globalThis.fetch = mockFetchJson(MOCK_AUTH_UNAUTHED);
    await getAuthStatus();
    const called = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(called).toBe("/api/x/auth/status");
  });
});

/* ------------------------------------------------------------------ */
/*  listWatches                                                        */
/* ------------------------------------------------------------------ */

describe("listWatches", () => {
  it("returns list of watches on success", async () => {
    globalThis.fetch = mockFetchJson({ watches: [MOCK_WATCH] });
    const result = await listWatches();
    expect(result).toHaveLength(1);
    expect(result[0].handle).toBe("elonmusk");
  });

  it("returns empty array on network error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await listWatches();
    expect(result).toEqual([]);
  });

  it("returns empty array when watches key is missing", async () => {
    globalThis.fetch = mockFetchJson({});
    const result = await listWatches();
    expect(result).toEqual([]);
  });

  it("calls the correct endpoint", async () => {
    globalThis.fetch = mockFetchJson({ watches: [] });
    await listWatches();
    const called = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(called).toBe("/api/x/watches");
  });
});

/* ------------------------------------------------------------------ */
/*  createWatch                                                        */
/* ------------------------------------------------------------------ */

describe("createWatch", () => {
  it("returns created watch on success", async () => {
    globalThis.fetch = mockFetchJson(MOCK_WATCH);
    const result = await createWatch("elonmusk");
    expect(result).not.toBeNull();
    expect(result!.handle).toBe("elonmusk");
  });

  it("posts to the correct endpoint", async () => {
    globalThis.fetch = mockFetchJson(MOCK_WATCH);
    await createWatch("elonmusk", { min_likes: 100 }, 3600);
    const [url, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/x/watch");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string);
    expect(body.handle).toBe("elonmusk");
    expect(body.frequency).toBe(3600);
    expect(body.filters.min_likes).toBe(100);
  });

  it("uses default frequency of 1800 when not provided", async () => {
    globalThis.fetch = mockFetchJson(MOCK_WATCH);
    await createWatch("testuser");
    const [, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const body = JSON.parse(init.body as string);
    expect(body.frequency).toBe(1800);
  });

  it("returns null on network error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await createWatch("baduser");
    expect(result).toBeNull();
  });

  it("forwards CSRF token from a Headers instance through fetchJson", async () => {
    // Regression: spreading a Headers object into an object literal
    // drops all entries because Headers entries are not own-enumerable.
    // withCsrf() returns a Headers instance; fetchJson must normalise it.
    vi.stubGlobal("document", {
      cookie: "csrf_token=hdr-token",
    } as unknown as Document);
    globalThis.fetch = mockFetchJson(MOCK_WATCH);
    await createWatch("elonmusk");
    const [, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    // init.headers may be a Headers instance or a plain object depending
    // on the code path — in either case the CSRF token must survive.
    const hdr = init.headers instanceof Headers
      ? init.headers.get("X-CSRF-Token")
      : (init.headers as Record<string, string>)["X-CSRF-Token"];
    expect(hdr).toBe("hdr-token");
  });
});

/* ------------------------------------------------------------------ */
/*  updateWatch                                                        */
/* ------------------------------------------------------------------ */

describe("updateWatch", () => {
  it("sends PUT request to the correct endpoint", async () => {
    const updatedWatch = { ...MOCK_WATCH, frequency: 600 };
    globalThis.fetch = mockFetchJson(updatedWatch);
    const result = await updateWatch("elonmusk", { frequency: 600 });
    expect(result).not.toBeNull();
    expect(result!.frequency).toBe(600);

    const [url, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/x/watch/elonmusk");
    expect(init.method).toBe("PUT");
  });

  it("can update enabled status", async () => {
    globalThis.fetch = mockFetchJson({ ...MOCK_WATCH, enabled: false });
    await updateWatch("elonmusk", { enabled: false });
    const [, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const body = JSON.parse(init.body as string);
    expect(body.enabled).toBe(false);
  });

  it("returns null on network error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await updateWatch("ghost", { frequency: 100 });
    expect(result).toBeNull();
  });

  it("encodes handle in URL", async () => {
    globalThis.fetch = mockFetchJson(MOCK_WATCH);
    await updateWatch("some user", { frequency: 300 });
    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/x/watch/some%20user");
  });
});

/* ------------------------------------------------------------------ */
/*  deleteWatch                                                        */
/* ------------------------------------------------------------------ */

describe("deleteWatch", () => {
  it("returns true on successful deletion", async () => {
    globalThis.fetch = mockFetchJson({ deleted: true, handle: "elonmusk" });
    const result = await deleteWatch("elonmusk");
    expect(result).toBe(true);
  });

  it("sends DELETE request to the correct endpoint", async () => {
    globalThis.fetch = mockFetchJson({ deleted: true, handle: "elonmusk" });
    await deleteWatch("elonmusk");
    const [url, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/x/watch/elonmusk");
    expect(init.method).toBe("DELETE");
  });

  it("returns false on network error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await deleteWatch("ghost");
    expect(result).toBe(false);
  });

  it("returns false when deleted is not true", async () => {
    globalThis.fetch = mockFetchJson({ error: "not found" });
    const result = await deleteWatch("nobody");
    expect(result).toBe(false);
  });
});

/* ------------------------------------------------------------------ */
/*  saveToLibrary                                                      */
/* ------------------------------------------------------------------ */

describe("saveToLibrary", () => {
  it("posts to knowledge ingest with source=x-monitor", async () => {
    globalThis.fetch = mockFetchJson({ id: "abc123", status: "pending" });
    const result = await saveToLibrary("https://twitter.com/user/status/123");
    expect(result).not.toBeNull();
    expect(result!.id).toBe("abc123");

    const [url, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/knowledge/ingest");
    const body = JSON.parse(init.body as string);
    expect(body.source).toBe("x-monitor");
    expect(body.url).toBe("https://twitter.com/user/status/123");
  });

  it("returns null on network error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await saveToLibrary("https://twitter.com/user/status/bad");
    expect(result).toBeNull();
  });
});
