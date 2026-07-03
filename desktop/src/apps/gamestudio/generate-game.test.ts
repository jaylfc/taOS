import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { generateGame } from "./generate-game";
import { findTemplate } from "./templates";

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

function seedContentFor(path: string): string {
  return `// original seed content for ${path}\n`;
}

function makeFetchMock(chatResponse: () => Response) {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.startsWith("/desktop/gamestudio-seeds/")) {
      const path = url.split("/").pop()!;
      return Promise.resolve({ ok: true, text: () => Promise.resolve(seedContentFor(path)) } as Response);
    }
    if (url === "/api/taos-agent/chat") {
      return Promise.resolve(chatResponse());
    }
    if (/^\/api\/games\/[^/]+$/.test(url) && (init?.method ?? "GET").toUpperCase() === "PUT") {
      const body = JSON.parse(String(init?.body ?? "{}"));
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ id: url.split("/").pop(), created: 1, updated: 2, ...body }),
      } as Response);
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response);
  });
}

describe("generateGame", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("parses a mocked AI file-set response and saves the changed files", async () => {
    const template = findTemplate("breakout")!;
    const responseText = [
      "Here's your customized game:",
      "",
      "### FILE: game.js",
      "```js",
      "// a faster paddle\nconst PADDLE_SPEED = 12;",
      "```",
    ].join("\n");
    vi.stubGlobal(
      "fetch",
      makeFetchMock(() => ndjsonResponse([responseText])) as unknown as typeof fetch,
    );

    const progress: string[] = [];
    const result = await generateGame("a faster paddle please", template, (p) => progress.push(p.stage));

    expect(result.usedFallback).toBe(false);
    expect(result.parseNotice).toBeNull();
    expect(result.game.files["game.js"]).toContain("PADDLE_SPEED = 12");
    // index.html was untouched by the mocked response, so the seed content survives.
    expect(result.game.files["index.html"]).toBe(seedContentFor("index.html"));
    expect(progress).toEqual(["seed", "streaming", "saving", "done"]);
  });

  it("falls back to the unmodified seed when the response has no file blocks", async () => {
    const template = findTemplate("breakout")!;
    vi.stubGlobal(
      "fetch",
      makeFetchMock(() => ndjsonResponse(["Sorry, I can't help with that request."])) as unknown as typeof fetch,
    );

    const result = await generateGame("do something impossible", template, () => {});

    expect(result.usedFallback).toBe(true);
    expect(result.parseNotice).toMatch(/unmodified starter template/);
    expect(result.game.files["game.js"]).toBe(seedContentFor("game.js"));
    expect(result.game.files["index.html"]).toBe(seedContentFor("index.html"));
  });

  it("never lets the agent overwrite a vendor file like three.module.js", async () => {
    const template = findTemplate("platformer-lite")!;
    const responseText = [
      "### FILE: three.module.js",
      "```js",
      "// a malicious or accidental rewrite of the vendored three.js build",
      "```",
      "### FILE: game.js",
      "```js",
      "// double jump added\nconst DOUBLE_JUMP = true;",
      "```",
    ].join("\n");
    vi.stubGlobal(
      "fetch",
      makeFetchMock(() => ndjsonResponse([responseText])) as unknown as typeof fetch,
    );

    const result = await generateGame("add a double jump", template, () => {});

    expect(result.game.files["three.module.js"]).toBe(seedContentFor("three.module.js"));
    expect(result.game.files["game.js"]).toContain("DOUBLE_JUMP = true");
  });
});
