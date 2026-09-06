import { describe, it, expect, vi, afterEach } from "vitest";
import { generateApp } from "./generate-app";

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

describe("generateApp", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("parses a mocked AI file-set response into a file map", async () => {
    const responseText = [
      "Here's your app:",
      "",
      "### FILE: index.html",
      "```html",
      "<!doctype html><html><body>Hello</body></html>",
      "```",
      "",
      "### FILE: app.js",
      "```js",
      "console.log('hi');",
      "```",
    ].join("\n");
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(ndjsonResponse([responseText]))) as unknown as typeof fetch,
    );

    const progress: string[] = [];
    const result = await generateApp("a simple greeter", (p) => progress.push(p.stage));

    expect(result.usedFallback).toBe(false);
    expect(result.parseNotice).toBeNull();
    expect(result.files["index.html"]).toContain("Hello");
    expect(result.files["app.js"]).toContain("console.log");
    expect(progress).toEqual(["streaming", "parsing", "done"]);
  });

  it("falls back to a starter page when the response has no file blocks", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(ndjsonResponse(["Sorry, I can't help with that request."])),
      ) as unknown as typeof fetch,
    );

    const result = await generateApp("do something impossible", () => {});

    expect(result.usedFallback).toBe(true);
    expect(result.parseNotice).toMatch(/could not be parsed/);
    expect(result.files["index.html"]).toContain("<!doctype html>");
  });

  it("falls back to a starter page when the response never emits an index.html", async () => {
    const responseText = ["### FILE: app.js", "```js", "console.log('no entry point');", "```"].join("\n");
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(ndjsonResponse([responseText]))) as unknown as typeof fetch,
    );

    const result = await generateApp("build something", () => {});

    expect(result.usedFallback).toBe(true);
    expect(result.parseNotice).toMatch(/index\.html entry point/);
    expect(result.files["index.html"]).toContain("<!doctype html>");
  });

  it("propagates a stream error instead of silently falling back", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(null, { status: 500, statusText: "Internal Server Error" }),
        ),
      ) as unknown as typeof fetch,
    );

    await expect(generateApp("anything", () => {})).rejects.toThrow();
  });
});
