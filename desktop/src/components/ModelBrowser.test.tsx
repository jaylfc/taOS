import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ModelBrowser } from "./ModelBrowser";

// Pins the #1548 fix: a download that fails must stay visible with its
// backend error and a Retry button, not silently vanish (which made an
// instant failure look like a completed install).

const CATALOG = {
  models: [
    {
      id: "qwen-test",
      name: "Qwen Test",
      description: "test model",
      capabilities: ["chat"],
      variants: [
        {
          id: "q4",
          name: "Q4",
          size_mb: 500,
          compatibility: "green",
          downloaded: false,
        },
      ],
    },
  ],
};

function mockFetch(routes: Record<string, () => Response>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      for (const [prefix, make] of Object.entries(routes)) {
        if (url.startsWith(prefix)) return Promise.resolve(make());
      }
      return Promise.resolve(
        new Response("[]", {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }) as unknown as typeof fetch,
  );
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ModelBrowser download errors", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("shows the backend error and a Retry button when a download fails", async () => {
    mockFetch({
      "/api/models/downloads/": () =>
        json({ status: "error", percent: 0, error: "SHA256 mismatch" }),
      "/api/models/download": () => json({ download_id: "dl-1" }),
      "/api/models/loaded": () => json({ models: [] }),
      "/api/models": () => json(CATALOG),
      "/api/providers": () => json([]),
    });

    render(
      <ModelBrowser open={true} onClose={() => {}} capability="chat" />,
    );

    const btn = await screen.findByRole("button", { name: "Download" });
    fireEvent.click(btn);

    // Poll interval is 2s; the error must surface, not be cleared.
    const alert = await screen.findByRole(
      "alert",
      {},
      { timeout: 5000 },
    );
    expect(alert.textContent).toContain("SHA256 mismatch");
    expect(
      screen.getByRole("button", { name: "Retry" }),
    ).toBeInTheDocument();
  }, 10000);

  it("shows an error when the download cannot even be started", async () => {
    mockFetch({
      "/api/models/download": () => json({ error: "no download_url for variant" }),
      "/api/models/loaded": () => json({ models: [] }),
      "/api/models": () => json(CATALOG),
      "/api/providers": () => json([]),
    });

    render(
      <ModelBrowser open={true} onClose={() => {}} capability="chat" />,
    );

    const btn = await screen.findByRole("button", { name: "Download" });
    fireEvent.click(btn);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("no download_url for variant");
  });
});
