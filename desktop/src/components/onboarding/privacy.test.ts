import { describe, it, expect, vi } from "vitest";
import { formatRate, loadPrivacy } from "./privacy";

describe("formatRate", () => {
  it("reads KB/s caps the way people say them", () => {
    expect(formatRate(5000)).toBe("5 MB/s");
    expect(formatRate(2500)).toBe("2.5 MB/s");
    expect(formatRate(512)).toBe("512 KB/s");
  });

  it("has no text for no cap", () => {
    expect(formatRate(0)).toBeNull();
  });
});

describe("loadPrivacy", () => {
  it("returns the correct providers with metadata", async () => {
    vi.stubGlobal("fetch", (url: string) => {
      if (url === "/api/preferences/auto-update") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ update_ping_enabled: true })
        } as Response);
      }
      if (url === "/api/torrent/settings") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ libtorrent_available: true, seed_enabled: true })
        } as Response);
      }
      if (url === "/api/providers") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([
            { type: "openai-compatible" },
            { type: "openai" },
            { type: "ollama" }
          ])
        } as Response);
      }
      return Promise.resolve({ ok: false } as Response);
    });

    const result = await loadPrivacy();

    expect(result.providers).toContain("OpenAI-Compatible");
    expect(result.providers).toContain("OpenAI");
    expect(result.providers).toHaveLength(2);
    expect(result.providers).not.toContain("Ollama");
  });
});
