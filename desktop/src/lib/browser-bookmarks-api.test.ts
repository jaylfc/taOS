import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { listBookmarks, addBookmark, removeBookmark } from "./browser-bookmarks-api";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("listBookmarks", () => {
  it("returns bookmarks array on 200", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        bookmarks: [
          { bookmark_id: "bm-1", url: "https://a.test/", title: "A", created_at: 1000 },
          { bookmark_id: "bm-2", url: "https://b.test/", title: "B", created_at: 2000 },
        ],
      }),
    });

    const result = await listBookmarks("profile-1");
    expect(result).toHaveLength(2);
    expect(result[0].bookmark_id).toBe("bm-1");
    expect(result[0].url).toBe("https://a.test/");
    expect(result[1].title).toBe("B");
  });

  it("returns [] on 401", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401 });
    expect(await listBookmarks("profile-1")).toEqual([]);
  });

  it("returns [] on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("network failure"));
    expect(await listBookmarks("profile-1")).toEqual([]);
  });

  it("returns [] when body.bookmarks is not an array", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ bookmarks: null }),
    });
    expect(await listBookmarks("profile-1")).toEqual([]);
  });

  it("includes credentials and profile_id param", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ bookmarks: [] }),
    });
    global.fetch = fetchMock;
    await listBookmarks("profile-1");
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("profile_id=profile-1");
    expect(opts.credentials).toBe("include");
  });

  it("encodes profile_id with spaces", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ bookmarks: [] }),
    });
    global.fetch = fetchMock;
    await listBookmarks("my profile");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("profile_id=my+profile");
  });
});

describe("addBookmark", () => {
  it("returns bookmark_id string on 200", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ bookmark_id: "bm-abc" }),
    });

    const result = await addBookmark("profile-1", "https://a.test/", "A");
    expect(result).toBe("bm-abc");
  });

  it("returns null on 401", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401 });
    expect(await addBookmark("profile-1", "https://a.test/", "A")).toBeNull();
  });

  it("returns null on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await addBookmark("profile-1", "https://a.test/", "A")).toBeNull();
  });

  it("returns null when body.bookmark_id is not a string", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ bookmark_id: 123 }),
    });
    expect(await addBookmark("profile-1", "https://a.test/", "A")).toBeNull();
  });

  it("posts JSON body with profile_id, url, and title", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ bookmark_id: "bm-1" }),
    });
    global.fetch = fetchMock;

    await addBookmark("profile-1", "https://a.test/", "A Test");

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/desktop/browser/bookmarks");
    expect(opts.method).toBe("POST");
    expect(opts.headers["content-type"]).toBe("application/json");
    const body = JSON.parse(opts.body);
    expect(body.profile_id).toBe("profile-1");
    expect(body.url).toBe("https://a.test/");
    expect(body.title).toBe("A Test");
  });

  it("includes credentials", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ bookmark_id: "bm-1" }),
    });
    global.fetch = fetchMock;
    await addBookmark("profile-1", "https://a.test/", "A");
    const [, opts] = fetchMock.mock.calls[0];
    expect(opts.credentials).toBe("include");
  });
});

describe("removeBookmark", () => {
  it("returns true on 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    expect(await removeBookmark("profile-1", "bm-1")).toBe(true);

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/desktop/browser/bookmarks/bm-1");
    expect(opts.method).toBe("DELETE");
  });

  it("returns false on 401", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401 });
    expect(await removeBookmark("profile-1", "bm-1")).toBe(false);
  });

  it("returns false on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await removeBookmark("profile-1", "bm-1")).toBe(false);
  });

  it("encodes bookmark_id in the path", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;
    await removeBookmark("profile-1", "bm/with/slashes");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("bm%2Fwith%2Fslashes");
  });

  it("includes profile_id as query param", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;
    await removeBookmark("profile-1", "bm-1");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("profile_id=profile-1");
  });

  it("includes credentials", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;
    await removeBookmark("profile-1", "bm-1");
    const [, opts] = fetchMock.mock.calls[0];
    expect(opts.credentials).toBe("include");
  });
});
