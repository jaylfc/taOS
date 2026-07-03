import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ProvidersApp } from "./ProvidersApp";

vi.mock("@/stores/process-store", () => ({
  useProcessStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({ openWindow: vi.fn() }),
}));

vi.mock("@/registry/app-registry", () => ({
  getApp: () => undefined,
}));

const HEALTHY_PROVIDER = {
  name: "local-npu",
  type: "rkllama",
  url: "http://localhost:7833",
  priority: 3,
  status: "ok",
  response_ms: 14,
  models: [{ name: "qwen2.5-3b" }],
  source: "local",
  category: "local",
  // Root-cause regression: an unmanaged (auto_manage=false) backend must
  // not carry a fabricated lifecycle_state -- the backend now omits the
  // field entirely rather than defaulting to "running" (#1578).
  auto_manage: false,
  keep_alive_minutes: 10,
  enabled: true,
};

function mockFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url === "/api/providers") {
        return Promise.resolve(
          new Response(JSON.stringify([HEALTHY_PROVIDER]), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      if (url === "/api/providers/types") {
        return Promise.resolve(
          new Response(
            JSON.stringify({ all: ["rkllama"], cloud: [], local: ["rkllama"] }),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        );
      }
      return Promise.reject(new Error("unexpected fetch: " + url));
    }) as unknown as typeof fetch,
  );
}

describe("ProvidersApp", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does not show an Error badge or a contradictory Running pill for a healthy, unmanaged provider", async () => {
    mockFetch();
    render(<ProvidersApp windowId="providers" />);

    // The single provider is auto-selected on desktop, so the detail pane
    // (with its own "local-npu" heading) renders without an extra click.
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "local-npu" })).toBeInTheDocument(),
    );

    // Given a healthy status payload, no Error badge anywhere in the dialog.
    expect(screen.queryByText("Error")).not.toBeInTheDocument();
    // No lifecycle pill either -- lifecycle_state is absent (not "running"),
    // so there is nothing to contradict the healthy status.
    expect(screen.queryByText("Running")).not.toBeInTheDocument();
    expect(screen.getAllByText("Online").length).toBeGreaterThan(0);
  });
});
