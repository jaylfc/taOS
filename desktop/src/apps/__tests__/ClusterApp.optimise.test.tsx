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

describe("ClusterApp — Optimise cluster", () => {
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