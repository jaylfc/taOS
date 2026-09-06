import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { EditorView } from "./EditorView";

function ndjsonResponse(deltas: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const d of deltas) controller.enqueue(encoder.encode(JSON.stringify({ delta: d }) + "\n"));
      controller.close();
    },
  });
  return new Response(body, { status: 200 });
}

const INITIAL_FILES = {
  "index.html": "<!doctype html><html></html>",
  "game.js": "const SPEED = 1;",
};

function makeFetchMock() {
  const putCalls: Record<string, string>[] = [];
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();

    if (url === "/api/games/test-game" && method === "GET") {
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            id: "test-game",
            name: "Test Game",
            prompt: "",
            template: "breakout",
            created: 1,
            updated: 1,
            files: INITIAL_FILES,
          }),
      } as Response);
    }
    if (url === "/api/games/test-game" && method === "PUT") {
      const body = JSON.parse(String(init?.body ?? "{}"));
      putCalls.push(body.files);
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ id: "test-game", name: "Test Game", prompt: "", template: "breakout", created: 1, updated: 2, files: body.files }),
      } as Response);
    }
    if (url === "/api/taos-agent/chat") {
      const responseText = [
        "### FILE: game.js",
        "```js",
        "const SPEED = 5; // faster",
        "```",
      ].join("\n");
      return Promise.resolve(ndjsonResponse([responseText]));
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response);
  });
  return { fetchMock, putCalls };
}

describe("EditorView", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads the game and shows its name", async () => {
    const { fetchMock } = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    render(<EditorView gameId="test-game" />);
    await waitFor(() => expect(screen.getByText("Test Game")).toBeDefined());
    expect(screen.getByLabelText("Editing game.js")).toBeDefined();
  });

  it("applying a proposed AI change updates the file set, saves, and refreshes the preview", async () => {
    const { fetchMock, putCalls } = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    render(<EditorView gameId="test-game" />);

    await waitFor(() => expect(screen.getByText("Test Game")).toBeDefined());

    const iframeBefore = screen.getByTitle("Game preview") as HTMLIFrameElement;
    const srcBefore = iframeBefore.src;

    fireEvent.change(screen.getByLabelText("Ask the agent for a change"), {
      target: { value: "make it faster" },
    });
    fireEvent.click(screen.getByLabelText("Send"));

    // The proposed change surfaces an Apply button before anything is saved.
    const applyButton = await screen.findByRole("button", { name: "Apply" });
    expect(putCalls.length).toBe(0);

    fireEvent.click(applyButton);

    await waitFor(() => expect(putCalls.length).toBeGreaterThan(0));
    expect(putCalls[putCalls.length - 1]!["game.js"]).toContain("SPEED = 5");

    await waitFor(() => {
      const iframeAfter = screen.getByTitle("Game preview") as HTMLIFrameElement;
      expect(iframeAfter.src).not.toBe(srcBefore);
    });
  });
});
