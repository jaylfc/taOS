import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { ModelsApp } from "./ModelsApp";

// Pins #1581 (delete was UI-only, never hit the backend) and the #1548
// remainder (the Models dialog didn't reliably show a model as installed
// right after its download completed).

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const CATALOG_WITH_DOWNLOADED_FILE = {
  models: [
    {
      id: "test-model",
      name: "Test Model",
      description: "a model for testing",
      compatibility: "green",
      capabilities: ["chat"],
      has_downloaded_variant: true,
      variants: [{ id: "q4", size_mb: 500 }],
    },
  ],
  downloaded_files: [
    {
      filename: "test-model-q4.gguf",
      size_mb: 500,
      format: "gguf",
      model_id: "test-model",
    },
  ],
  hardware_profile_id: "profile-1",
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

describe("ModelsApp delete wiring (#1581)", () => {
  it("issues DELETE /api/models/{model_id} and removes the row only on success", async () => {
    let modelsCallCount = 0;
    const calls = mockFetch({
      "/api/models/test-model": () => json({ status: "deleted", deleted_files: ["test-model-q4.gguf"] }),
      "/api/models": () => {
        modelsCallCount += 1;
        return json(
          modelsCallCount === 1
            ? CATALOG_WITH_DOWNLOADED_FILE
            : { ...CATALOG_WITH_DOWNLOADED_FILE, models: [{ ...CATALOG_WITH_DOWNLOADED_FILE.models[0], has_downloaded_variant: false }], downloaded_files: [] },
        );
      },
    });

    render(<ModelsApp windowId="w1" />);

    const deleteBtn = await screen.findByRole("button", { name: "Delete test-model-q4.gguf" });
    fireEvent.click(deleteBtn);

    await waitFor(() => {
      const deleteCall = calls.find((c) => c.url === "/api/models/test-model" && c.init?.method === "DELETE");
      expect(deleteCall).toBeTruthy();
    });

    // Row disappears only after the backend confirms deletion via a refetch,
    // never optimistically before the DELETE call resolves.
    await waitFor(() => {
      expect(screen.queryByText("test-model-q4.gguf")).not.toBeInTheDocument();
    });
  });

  it("keeps the row and surfaces an error when the backend delete fails", async () => {
    mockFetch({
      "/api/models/test-model": () =>
        new Response(JSON.stringify({ error: "permission denied" }), { status: 500 }),
      "/api/models": () => json(CATALOG_WITH_DOWNLOADED_FILE),
    });

    render(<ModelsApp windowId="w1" />);

    const deleteBtn = await screen.findByRole("button", { name: "Delete test-model-q4.gguf" });
    fireEvent.click(deleteBtn);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("permission denied");
    // The row must still be there — a failed delete is not silently treated
    // as a success.
    expect(screen.getByText("test-model-q4.gguf")).toBeInTheDocument();
  });
});

describe("ModelsApp post-download refresh (#1548 remainder)", () => {
  it("a slow download-complete refetch must not clobber a later, faster refetch", async () => {
    // model-a: not yet downloaded, will be downloaded during the test.
    // model-b: already downloaded, will be deleted during the test — its
    // Delete action triggers its own fetchModels() call, giving us a SECOND
    // real (not synthetic) refetch to race against the download's.
    const INITIAL = {
      models: [
        {
          id: "model-a",
          name: "Model A",
          description: "",
          compatibility: "green",
          capabilities: ["chat"],
          has_downloaded_variant: false,
          variants: [{ id: "q4", size_mb: 500 }],
        },
      ],
      downloaded_files: [
        { filename: "model-b-default.gguf", size_mb: 100, format: "gguf", model_id: "model-b" },
      ],
      hardware_profile_id: "profile-1",
    };

    // What the download-complete refetch would see — STALE, because it
    // still lists model-b as downloaded even though it's about to be
    // deleted by the time this call actually resolves.
    let resolveDownloadRefetch: (r: Response) => void = () => {};
    const downloadRefetchPromise = new Promise<Response>((resolve) => {
      resolveDownloadRefetch = resolve;
    });

    // What the delete refetch sees — the TRUE latest state: model-a
    // installed, model-b gone. This call is issued AFTER the download's
    // refetch but resolves FIRST.
    const AFTER_DELETE = {
      models: [{ ...INITIAL.models[0], has_downloaded_variant: true }],
      downloaded_files: [],
      hardware_profile_id: "profile-1",
    };

    let modelsCallCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        if (url.startsWith("/api/models/downloads/")) {
          return Promise.resolve(json({ status: "complete", percent: 100 }));
        }
        if (url === "/api/models/download") {
          return Promise.resolve(json({ status: "started", download_id: "model-a-q4" }));
        }
        if (url === "/api/models/model-b" && init?.method === "DELETE") {
          return Promise.resolve(json({ status: "deleted", deleted_files: ["model-b-default.gguf"] }));
        }
        if (url === "/api/models") {
          modelsCallCount += 1;
          if (modelsCallCount === 1) return Promise.resolve(json(INITIAL));
          if (modelsCallCount === 2) return downloadRefetchPromise; // stale, resolves last
          return Promise.resolve(json(AFTER_DELETE)); // 3rd+ call: the true latest state
        }
        return Promise.resolve(json([]));
      }) as unknown as typeof fetch,
    );

    render(<ModelsApp windowId="w1" />);

    // Kick off the download — its completion will fire the (stale, slow)
    // 2nd /api/models call.
    const downloadBtn = await screen.findByRole("button", { name: "Download Model A" });
    fireEvent.click(downloadBtn);
    await waitFor(() => expect(modelsCallCount).toBeGreaterThanOrEqual(2));

    // While that's still pending, delete model-b — its completion fires the
    // (fresh, fast) 3rd /api/models call, which resolves immediately.
    const deleteBtn = await screen.findByRole("button", { name: "Delete model-b-default.gguf" });
    fireEvent.click(deleteBtn);

    await waitFor(() => {
      expect(modelsCallCount).toBeGreaterThanOrEqual(3);
      expect(screen.getByText("Downloaded")).toBeInTheDocument();
      expect(screen.queryByText("model-b-default.gguf")).not.toBeInTheDocument();
    });

    // Now the stale download-refetch (started before the delete-refetch)
    // finally resolves. Its data must be discarded — model-b must not
    // reappear, and model-a must stay "Downloaded".
    resolveDownloadRefetch(json(INITIAL));
    await new Promise((r) => setTimeout(r, 50));

    expect(screen.getByText("Downloaded")).toBeInTheDocument();
    expect(screen.queryByText("model-b-default.gguf")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download Model A" })).not.toBeInTheDocument();
  });
});

describe("ModelsApp refresh-failure", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("preserves real models when a background refresh fails", async () => {
    let modelsCallCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url === "/api/models") {
          modelsCallCount += 1;
          if (modelsCallCount === 1) {
            return Promise.resolve(
              json({
                models: [
                  {
                    id: "m1",
                    name: "Real Model",
                    description: "",
                    compatibility: "green",
                    capabilities: [],
                    variants: [],
                  },
                ],
                downloaded_files: [
                  { filename: "real-model.gguf", size_mb: 100, format: "gguf", model_id: "m1" },
                ],
                hardware_profile_id: "p1",
              }),
            );
          }
          return Promise.reject(new Error("network error"));
        }
        return Promise.resolve(json([]));
      }) as unknown as typeof fetch,
    );

    render(<ModelsApp windowId="w1" />);

    await waitFor(() => expect(screen.getByText("Real Model")).toBeInTheDocument());

    window.dispatchEvent(new Event("focus"));

    await act(async () => {
      vi.advanceTimersByTime(1100);
      await Promise.resolve();
    });

    expect(screen.getByText("Real Model")).toBeInTheDocument();
    expect(screen.queryByText("No models yet")).not.toBeInTheDocument();
  });

  it("still shows 'No models yet' when no real data exists and refresh fails", async () => {
    let callCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        callCount += 1;
        if (url === "/api/models") {
          return Promise.reject(new Error("network error"));
        }
        return Promise.resolve(json([]));
      }) as unknown as typeof fetch,
    );

    render(<ModelsApp windowId="w1" />);

    await waitFor(() => expect(screen.getByText("No models yet")).toBeInTheDocument());

    window.dispatchEvent(new Event("focus"));

    await act(async () => {
      vi.advanceTimersByTime(1100);
      await Promise.resolve();
    });

    expect(screen.getByText("No models yet")).toBeInTheDocument();
  });
});
