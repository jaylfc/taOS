import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ModelsApp } from "./ModelsApp";

// Pins #1548: the Models app download flow used to be a pure frontend mock —
// a setInterval + Math.random progress bar that never called the backend,
// then fabricated a "downloaded" entry that vanished on reopen. These tests
// fail against that mock and only pass once Download really calls
// POST /api/models/download, polls GET /api/models/downloads/{id}, and
// refetches /api/models on completion instead of fabricating state.

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const CATALOG_NOT_DOWNLOADED = {
  models: [
    {
      id: "test-model",
      name: "Test Model",
      description: "a model for testing",
      compatibility: "green",
      capabilities: ["chat"],
      has_downloaded_variant: false,
      variants: [{ id: "q4", size_mb: 500 }],
    },
  ],
  downloaded_files: [],
  hardware_profile_id: "profile-1",
};

const CATALOG_DOWNLOADED = {
  ...CATALOG_NOT_DOWNLOADED,
  models: [
    {
      ...CATALOG_NOT_DOWNLOADED.models[0],
      has_downloaded_variant: true,
    },
  ],
};

function mockFetch(routes: Record<string, () => Response>) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      calls.push({ url, init });
      for (const [prefix, make] of Object.entries(routes)) {
        if (url.startsWith(prefix)) return Promise.resolve(make());
      }
      return Promise.resolve(json([]));
    }) as unknown as typeof fetch,
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ModelsApp download wiring (#1548)", () => {
  it("issues POST /api/models/download with {app_id, variant_id} on click", async () => {
    const calls = mockFetch({
      "/api/models/downloads/": () => json({ status: "downloading", percent: 0 }),
      "/api/models/download": () =>
        json({ status: "started", download_id: "test-model-q4" }),
      "/api/models": () => json(CATALOG_NOT_DOWNLOADED),
    });

    render(<ModelsApp windowId="w1" />);

    const btn = await screen.findByRole("button", { name: "Download Test Model" });
    fireEvent.click(btn);

    await waitFor(() => {
      const call = calls.find((c) => c.url === "/api/models/download");
      expect(call).toBeTruthy();
      const body = JSON.parse(call!.init!.body as string);
      expect(body).toEqual({ app_id: "test-model", variant_id: "q4" });
    });
  });

  it("drives the progress bar from the polled percent, not a fake timer", async () => {
    let pollCount = 0;
    mockFetch({
      "/api/models/downloads/": () => {
        pollCount += 1;
        return json({ status: "downloading", percent: 42 });
      },
      "/api/models/download": () =>
        json({ status: "started", download_id: "test-model-q4" }),
      "/api/models": () => json(CATALOG_NOT_DOWNLOADED),
    });

    render(<ModelsApp windowId="w1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Download Test Model" }));

    const bar = await screen.findByRole("progressbar", {}, { timeout: 5000 });
    await waitFor(
      () => {
        expect(pollCount).toBeGreaterThan(0);
        expect(bar.getAttribute("aria-valuenow")).toBe("42");
      },
      { timeout: 5000 },
    );
  }, 10000);

  it("surfaces a polled status:error with a Retry button instead of fake success", async () => {
    mockFetch({
      "/api/models/downloads/": () =>
        json({ status: "error", percent: 0, error: "checksum mismatch" }),
      "/api/models/download": () =>
        json({ status: "started", download_id: "test-model-q4" }),
      "/api/models": () => json(CATALOG_NOT_DOWNLOADED),
    });

    render(<ModelsApp windowId="w1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Download Test Model" }));

    const alert = await screen.findByRole("alert", {}, { timeout: 5000 });
    expect(alert.textContent).toContain("checksum mismatch");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  }, 10000);

  it("refetches the model list on complete instead of fabricating a downloaded entry", async () => {
    let modelsCallCount = 0;
    mockFetch({
      "/api/models/downloads/": () => json({ status: "complete", percent: 100 }),
      "/api/models/download": () =>
        json({ status: "started", download_id: "test-model-q4" }),
      "/api/models": () => {
        modelsCallCount += 1;
        return json(modelsCallCount === 1 ? CATALOG_NOT_DOWNLOADED : CATALOG_DOWNLOADED);
      },
    });

    render(<ModelsApp windowId="w1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Download Test Model" }));

    // The real signal of completion is a second /api/models fetch driving
    // the "Downloaded" label — never a client-fabricated entry.
    await waitFor(
      () => {
        expect(modelsCallCount).toBeGreaterThanOrEqual(2);
        expect(screen.getByText("Downloaded")).toBeInTheDocument();
      },
      { timeout: 5000 },
    );
    expect(
      screen.queryByRole("button", { name: "Download Test Model" }),
    ).not.toBeInTheDocument();
  }, 10000);

  it("shows a clear error instead of fake success when a model has no downloadable variant", async () => {
    // Force the offline MOCK_AVAILABLE fallback (no variantId on any model)
    // by making /api/models fail while a worker is reachable — this is the
    // path that surfaces isFallback with real cards to click.
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url.startsWith("/api/models")) {
          return Promise.resolve(new Response("not json", { status: 500 }));
        }
        if (url.startsWith("/api/cluster/workers")) {
          return Promise.resolve(
            json([{ name: "worker1", url: "http://worker1", models: ["some-model"] }]),
          );
        }
        return Promise.resolve(json([]));
      }) as unknown as typeof fetch,
    );

    render(<ModelsApp windowId="w1" />);

    const btn = await screen.findByRole("button", { name: /Download Llama 3 8B/i });
    fireEvent.click(btn);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/no downloadable variant/i);
  });
});
