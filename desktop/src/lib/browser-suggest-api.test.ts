import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchSuggestions } from "./browser-suggest-api";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("fetchSuggestions", () => {
  it("returns suggestions array on 200", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        suggestions: [
          { url: "https://a.test/", title: "A", source: "history", score: 1 },
        ],
      }),
    });
    const out = await fetchSuggestions("personal", "a");
    expect(out.length).toBe(1);
    expect(out[0].url).toBe("https://a.test/");
  });

  it("returns [] on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false });
    const out = await fetchSuggestions("personal", "a");
    expect(out).toEqual([]);
  });

  it("returns [] for empty/whitespace query without hitting fetch", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock;
    const a = await fetchSuggestions("personal", "");
    const b = await fetchSuggestions("personal", "   ");
    expect(a).toEqual([]);
    expect(b).toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("encodes query parameters correctly", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ suggestions: [] }),
    });
    global.fetch = fetchMock;
    await fetchSuggestions("personal", "hello world", 5);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("profile_id=personal");
    expect(url).toContain("q=hello+world");
    expect(url).toContain("limit=5");
  });
});
