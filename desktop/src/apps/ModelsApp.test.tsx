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

  it("shows the error state with a Retry button when no real data exists and refresh fails", async () => {
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

    // A rejected fetch must end the loading state and surface the error
    // state with a Retry button — it must not sit on "Loading models..."
    // forever, nor silently fall back to the empty state.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    });
    expect(screen.getByText("Could not load models")).toBeInTheDocument();
    expect(screen.getByText("network error")).toBeInTheDocument();
    expect(screen.queryByText("Loading models...")).not.toBeInTheDocument();

    window.dispatchEvent(new Event("focus"));

    await act(async () => {
      vi.advanceTimersByTime(1100);
      await Promise.resolve();
    });

    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("shows the empty state when the backend answers ok with no models", async () => {
    mockFetch({
      "/api/models": () =>
        json({ models: [], downloaded_files: [], hardware_profile_id: "p1" }),
    });

    render(<ModelsApp windowId="w1" />);

    // An ok, empty response must end loading and show the empty state —
    // the spinner must not stay up, and there must be no Retry button.
    await waitFor(() => {
      expect(screen.getByText("No downloaded models")).toBeInTheDocument();
    });
    expect(screen.queryByText("Loading models...")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });
});

describe("ModelsApp request sequencing", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("only the latest fetch clears loading; a stale mount request cannot clear it before a newer refresh commits", async () => {
    // Mount fetch is SLOW; refresh fetch is FAST and resolves first.
    let resolveMount: (r: Response) => void = () => {};
    const mountPromise = new Promise<Response>((resolve) => {
      resolveMount = resolve;
    });

    const REFRESH_RESPONSE = {
      models: [
        {
          id: "m1",
          name: "Refreshed Model",
          description: "",
          compatibility: "green",
          capabilities: [],
          has_downloaded_variant: true,
          variants: [{ id: "q4", size_mb: 500 }],
        },
      ],
      downloaded_files: [
        { filename: "refreshed-model.gguf", size_mb: 500, format: "gguf", model_id: "m1" },
      ],
      hardware_profile_id: "p1",
    };

    let modelsCallCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url === "/api/models") {
          modelsCallCount += 1;
          if (modelsCallCount === 1) return mountPromise; // mount: resolves LAST (stale)
          return Promise.resolve(json(REFRESH_RESPONSE)); // refresh: resolves FIRST
        }
        if (url.startsWith("/api/cluster/workers")) return Promise.resolve(json({ nodes: [], capabilities: [] }));
        if (url.startsWith("/api/cloud/providers")) return Promise.resolve(json({ providers: [] }));
        return Promise.resolve(json([]));
      }) as unknown as typeof fetch,
    );

    render(<ModelsApp windowId="w1" />);

    // Trigger a refresh (simulating focus) while mount is still pending.
    window.dispatchEvent(new Event("focus"));
    await act(async () => {
      vi.advanceTimersByTime(1100);
      await Promise.resolve();
    });

    // Refresh should have resolved and shown its data.
    await waitFor(() => expect(screen.getByText("Refreshed Model")).toBeInTheDocument());
    expect(screen.queryByText("Loading models...")).not.toBeInTheDocument();

    // Now let the stale mount resolve — it must NOT clobber the UI (loading must
    // stay cleared, data must stay the refreshed data).
    resolveMount(json({ models: [], downloaded_files: [], hardware_profile_id: "p1" }));
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByText("Refreshed Model")).toBeInTheDocument();
    expect(screen.queryByText("Loading models...")).not.toBeInTheDocument();
  });

  it("shows error state with Retry when primary fetch fails but worker/cloud models are available", async () => {
    // /api/models fails, but cluster workers + cloud providers return models.
    const WORKER_RESPONSE = [{ name: "worker-1", kind: "worker", url: "http://w1", hardware: { ram_mb: 8192 }, backends: [{ name: "llama.cpp", models: ["worker-model"] }] }];
    const CLOUD_RESPONSE = [{ type: "openai", name: "OpenAI", models: [{ id: "gpt-4", name: "gpt-4" }] }];

    let modelsCallCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url === "/api/models") {
          modelsCallCount += 1;
          return Promise.reject(new Error("network error"));
        }
        if (url.startsWith("/api/cluster/workers")) return Promise.resolve(json(WORKER_RESPONSE));
        if (url.startsWith("/api/providers")) return Promise.resolve(json(CLOUD_RESPONSE));
        return Promise.resolve(json([]));
      }) as unknown as typeof fetch,
    );

    render(<ModelsApp windowId="w1" />);

    // Must show the fallback data (worker + cloud models) AND the error state
    // with a Retry button — not silently succeed or hide the error.
    await waitFor(() => expect(screen.getByText("worker-model")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("gpt-4")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument());
    expect(screen.getByText("Could not load models from the main catalog")).toBeInTheDocument();
    expect(screen.getByText("network error")).toBeInTheDocument();
    expect(screen.queryByText("Loading models...")).not.toBeInTheDocument();
    expect(screen.getByText("Sample data")).toBeInTheDocument(); // isFallback badge
  });
});
