import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ClusterApp } from "../ClusterApp";

const WORKERS_RESPONSE = [
  {
    name: "worker-1",
    url: "http://10.0.0.5:8080",
    status: "online",
    last_heartbeat: Date.now() / 1000,
    hardware: {},
    backends: [],
    capabilities: [],
  },
];

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    json: () => Promise.resolve(body),
  } as unknown as Response);
}

async function selectWorker() {
  render(<ClusterApp windowId="test" />);
  await act(async () => {
    await new Promise((r) => setTimeout(r, 100));
  });
  // Click on the worker in the list to select it - target the list card button
  const workerCard = await screen.findByRole("button", { name: /Select worker worker-1/i });
  fireEvent.click(workerCard);
  // Wait for the detail view to appear (Optimise cluster button)
  await waitFor(() => {
    screen.getByRole("button", { name: /run cluster optimiser/i });
  });
}

describe("ClusterApp clipboard in non-secure context", () => {
  beforeEach(() => {
    Object.defineProperty(window, "isSecureContext", {
      value: false,
      configurable: true,
    });
    Object.defineProperty(navigator, "clipboard", {
      value: undefined,
      configurable: true,
    });
    const execCommand = vi.fn().mockReturnValue(false);
    Object.defineProperty(document, "execCommand", {
      value: execCommand,
      configurable: true,
      writable: true,
    });
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [
        {
          name: "test-worker",
          status: "online",
          last_heartbeat: Date.now(),
          hardware: {},
          tier_id: null,
          potential_capabilities: [],
          backends: [],
          capabilities: [],
        },
      ],
    }) as typeof fetch;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("copy button does not report success when nothing was copied", async () => {
    render(<ClusterApp windowId="test" />);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 100));
    });
    const copyButton = screen.getByText("Copy name");
    await act(async () => {
      fireEvent.click(copyButton);
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(screen.queryByText("Copied")).not.toBeInTheDocument();
  });
});

describe("ClusterApp Resources tab", () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ resources: [], errors: [], generated_at: 0 }),
    }) as typeof fetch;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("has a Resources tab that requests /api/cluster/resources when selected", async () => {
    render(<ClusterApp windowId="test" />);
    const tab = screen.getByRole("tab", { name: "Show cluster resources" });
    expect(tab).toHaveAttribute("aria-selected", "false");
    expect(globalThis.fetch).not.toHaveBeenCalledWith("/api/cluster/resources", expect.anything());
    await act(async () => {
      fireEvent.click(tab);
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(tab).toHaveAttribute("aria-selected", "true");
    expect(globalThis.fetch).toHaveBeenCalledWith("/api/cluster/resources", expect.anything());
    expect(screen.getByRole("tabpanel", { name: "Cluster resources" })).toBeInTheDocument();
  });
});

describe("ClusterApp — Optimise cluster", () => {
  beforeEach(() => {
    // Force desktop layout: mock matchMedia to return non-mobile before render
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation((query) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
    Object.defineProperty(window, "innerWidth", { value: 1024, configurable: true });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows optimiser suggestions panel with suggestions when optimise returns suggestions", async () => {
    globalThis.fetch = vi.fn().mockImplementation((input: string, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/cluster/workers")) return jsonResponse(WORKERS_RESPONSE);
      if (url === "/api/cluster/optimise" && (!init?.method || init.method === "GET")) {
        return jsonResponse({
          suggestions: [
            { model_or_service: "embedding-model", suggested_worker: "w2", reason: "r", improvement: "frees GPU" },
          ],
          summary: "2 workers online, 1 optimisation suggestions",
          workers: [],
        });
      }
      return jsonResponse({}, 404);
    }) as typeof fetch;

    await selectWorker();

    // Click the Optimise cluster button
    const optimiseBtn = screen.getByRole("button", { name: /run cluster optimiser/i });
    fireEvent.click(optimiseBtn);

    // Wait for the toast to appear and disappear, then check for the suggestions panel
    await waitFor(() => {
      expect(screen.getByText("embedding-model")).toBeInTheDocument();
      expect(screen.getByText("→ w2")).toBeInTheDocument();
      expect(screen.getByText("r")).toBeInTheDocument();
    });
  });

  it("shows optimiser summary when optimise returns empty suggestions", async () => {
    globalThis.fetch = vi.fn().mockImplementation((input: string, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/cluster/workers")) return jsonResponse(WORKERS_RESPONSE);
      if (url === "/api/cluster/optimise" && (!init?.method || init.method === "GET")) {
        return jsonResponse({
          suggestions: [],
          summary: "Need at least 2 online workers for optimisation",
          workers: [],
        });
      }
      return jsonResponse({}, 404);
    }) as typeof fetch;

    await selectWorker();

    const optimiseBtn = screen.getByRole("button", { name: /run cluster optimiser/i });
    fireEvent.click(optimiseBtn);

    await waitFor(() => {
      expect(screen.getByText("Need at least 2 online workers for optimisation")).toBeInTheDocument();
    });
  });

  it("keeps the failure toast when optimise fails", async () => {
    globalThis.fetch = vi.fn().mockImplementation((input: string, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/cluster/workers")) return jsonResponse(WORKERS_RESPONSE);
      if (url === "/api/cluster/optimise" && (!init?.method || init.method === "GET")) {
        return jsonResponse({ error: "Internal error" }, 500);
      }
      return jsonResponse({}, 404);
    }) as typeof fetch;

    await selectWorker();

    const optimiseBtn = screen.getByRole("button", { name: /run cluster optimiser/i });
    fireEvent.click(optimiseBtn);

    await waitFor(() => {
      expect(screen.getByText(/Optimiser failed \(500\)/i)).toBeInTheDocument();
    });
  });
});
