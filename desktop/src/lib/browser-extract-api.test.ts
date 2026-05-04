import { afterEach, describe, expect, it, vi } from "vitest";
import { extractReadable } from "./browser-extract-api";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("extractReadable", () => {
  it("calls the correct URL with profile_id and url params", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        title: "Test Article",
        text: "Some text",
        html: "<p>Some text</p>",
        word_count: 250,
      }),
    });
    global.fetch = fetchMock;

    await extractReadable("personal", "https://example.com/article");

    const [calledUrl] = fetchMock.mock.calls[0];
    expect(calledUrl).toContain("/api/desktop/browser/extract");
    expect(calledUrl).toContain("profile_id=personal");
    expect(calledUrl).toContain("url=");
    expect(calledUrl).toContain("example.com");
  });

  it("returns parsed JSON on 200", async () => {
    const expected = {
      title: "Article Title",
      text: "The text",
      html: "<p>The text</p>",
      word_count: 350,
    };
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => expected,
    });

    const result = await extractReadable("personal", "https://example.com/");
    expect(result).toEqual(expected);
  });

  it("returns null on non-ok response (e.g. 401)", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401 });
    const result = await extractReadable("personal", "https://example.com/");
    expect(result).toBeNull();
  });

  it("returns null on non-ok 403 response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 403 });
    const result = await extractReadable("personal", "https://blocked.com/");
    expect(result).toBeNull();
  });

  it("returns null on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("Network error"));
    const result = await extractReadable("personal", "https://example.com/");
    expect(result).toBeNull();
  });

  it("passes credentials: include", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ title: "", text: "", html: "", word_count: 0 }),
    });
    global.fetch = fetchMock;

    await extractReadable("work", "https://example.com/");

    const [, options] = fetchMock.mock.calls[0];
    expect(options?.credentials).toBe("include");
  });
});
