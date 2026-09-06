import { describe, it, expect, vi, afterEach } from "vitest";
import { generateSite } from "./generate-site";

/** Encode a mock /api/taos-agent/chat NDJSON stream response from a list of
 *  text deltas -- mirrors the {delta} shape streamTaosAgentChat parses. */
function ndjsonResponse(deltas: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const d of deltas) {
        controller.enqueue(encoder.encode(JSON.stringify({ delta: d }) + "\n"));
      }
      controller.close();
    },
  });
  return new Response(body, { status: 200 });
}

function makeFetchMock(chatResponse: () => Response) {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/taos-agent/chat") {
      return Promise.resolve(chatResponse());
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response);
  });
}

const VALID_SITE_JSON = JSON.stringify({
  title: "Fresh Cafe",
  theme: { palette: "sand", font: "serif" },
  sections: [
    {
      id: "hero-1",
      type: "hero",
      content: {
        eyebrow: "Now open",
        heading: "Coffee worth the walk",
        subheading: "A neighborhood cafe.",
        ctaLabel: "See the menu",
        image: "",
      },
    },
    {
      id: "footer-1",
      type: "footer",
      content: { businessName: "Fresh Cafe", tagline: "Made with taOS Web Studio" },
    },
  ],
});

describe("generateSite", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("parses a mocked AI JSON response into a valid Site", async () => {
    vi.stubGlobal(
      "fetch",
      makeFetchMock(() => ndjsonResponse([`Here you go:\n\n\`\`\`json\n${VALID_SITE_JSON}\n\`\`\``])) as unknown as typeof fetch,
    );

    const progress: string[] = [];
    const result = await generateSite("a cafe landing page", (p) => progress.push(p.stage));

    expect(result.usedFallback).toBe(false);
    expect(result.parseNotice).toBeNull();
    expect(result.site.title).toBe("Fresh Cafe");
    expect(result.site.theme.palette).toBe("sand");
    expect(result.site.sections).toHaveLength(2);
    expect(progress).toEqual(["streaming", "parsing", "done"]);
  });

  it("parses a bare JSON response with no code fence", async () => {
    vi.stubGlobal("fetch", makeFetchMock(() => ndjsonResponse([VALID_SITE_JSON])) as unknown as typeof fetch);

    const result = await generateSite("a cafe landing page", () => {});
    expect(result.usedFallback).toBe(false);
    expect(result.site.title).toBe("Fresh Cafe");
  });

  it("falls back to matchTemplate when the response has no JSON object", async () => {
    vi.stubGlobal(
      "fetch",
      makeFetchMock(() => ndjsonResponse(["Sorry, I can't help with that request."])) as unknown as typeof fetch,
    );

    const result = await generateSite("a landing page for a saas product", () => {});
    expect(result.usedFallback).toBe(true);
    expect(result.parseNotice).toMatch(/starter template/);
    expect(result.site.sections.length).toBeGreaterThan(0);
  });

  it("falls back when the JSON parses but doesn't match the Site shape", async () => {
    vi.stubGlobal(
      "fetch",
      makeFetchMock(() => ndjsonResponse(['{"foo": "bar"}'])) as unknown as typeof fetch,
    );

    const result = await generateSite("a portfolio site", () => {});
    expect(result.usedFallback).toBe(true);
    expect(result.parseNotice).toMatch(/starter template/);
  });

  it("falls back when the model invents an unknown palette/font id", async () => {
    const badSite = JSON.stringify({
      title: "X",
      theme: { palette: "neon", font: "sans" },
      sections: [{ id: "a", type: "footer", content: { businessName: "X", tagline: "" } }],
    });
    vi.stubGlobal("fetch", makeFetchMock(() => ndjsonResponse([badSite])) as unknown as typeof fetch);

    const result = await generateSite("a landing page", () => {});
    expect(result.usedFallback).toBe(true);
  });

  it("propagates a stream error instead of silently falling back", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve({ ok: false, status: 500, text: () => Promise.resolve("boom") } as Response)) as unknown as typeof fetch,
    );

    await expect(generateSite("anything", () => {})).rejects.toThrow();
  });
});
