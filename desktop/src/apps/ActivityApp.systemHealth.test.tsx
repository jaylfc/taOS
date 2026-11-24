import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { SystemHealthCard } from "./ActivityApp.systemHealth";
import { useProcessStore } from "@/stores/process-store";

// Fixtures shaped like the real endpoints (see lib/system-health.ts).
const HEALTH = {
  status: "ok",
  agents: 2,
  backends: 1,
  subsystems: {
    cluster: { status: "ok", workers_total: 2, workers_online: 2 },
    scheduler: { status: "ok", backends: 1 },
    qmd: { status: "ok", url: "http://127.0.0.1:7835" },
    controller_identity: { status: "ok", reason: "" },
  },
};

const CHECKS = [
  { name: "TinyAgentOS", status: "ok", detail: "Running", response_ms: 0 },
  { name: "incusd", status: "ok", detail: "Running", response_ms: 3 },
  { name: "Docker", status: "ok", detail: "Docker version 27", response_ms: 5 },
  { name: "Backend: rkllama", status: "ok", detail: "12ms, 3 models", response_ms: 12 },
  { name: "QMD Server", status: "ok", detail: "8ms", response_ms: 8 },
];

const AGENTS = [
  { name: "alpha", status: "running" },
  { name: "beta", status: "running" },
];

const MEMORY = {
  pipeline: { archive: "idle", catalog: "idle", vector: "idle", kg: "idle", crystal: "idle" },
  recent_errors: [] as string[],
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockEndpoints(overrides: Record<string, () => Promise<Response>> = {}) {
  const handlers: Record<string, () => Promise<Response>> = {
    "/api/health?detailed=true": () => Promise.resolve(jsonResponse(HEALTH)),
    "/api/health-check": () => Promise.resolve(jsonResponse({ checks: CHECKS })),
    "/api/agents": () => Promise.resolve(jsonResponse(AGENTS)),
    "/api/memory/stats": () => Promise.resolve(jsonResponse(MEMORY)),
    ...overrides,
  };
  return vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const handler = handlers[String(input)];
    if (!handler) return Promise.reject(new Error(`Unmocked fetch: ${String(input)}`));
    return handler();
  });
}

describe("SystemHealthCard", () => {
  let openWindow: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.restoreAllMocks();
    openWindow = vi.fn(() => "window-1");
    useProcessStore.setState({ openWindow });
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders every glance row healthy with an all-healthy verdict", async () => {
    mockEndpoints();
    render(<SystemHealthCard />);
    await waitFor(() =>
      expect(screen.getByText("All systems healthy")).toBeInTheDocument(),
    );
    for (const label of [
      "LLM gateway",
      "Agents",
      "Cluster workers",
      "Containers",
      "Model backends",
      "Memory pipeline",
      "Recent errors",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText(/2 running · 0 stopped/)).toBeInTheDocument();
    expect(screen.getByText(/2\/2 online/)).toBeInTheDocument();
    // Healthy rows are not buttons: only unhealthy items link out.
    expect(screen.queryByRole("button", { name: /agents:/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /cluster workers:/i })).toBeNull();
  });

  it("links an errored agents row into the Agents app", async () => {
    mockEndpoints({
      "/api/agents": () =>
        Promise.resolve(
          jsonResponse([
            { name: "alpha", status: "running" },
            { name: "beta", status: "error" },
          ]),
        ),
    });
    render(<SystemHealthCard />);
    const button = await screen.findByRole("button", { name: /agents: error/i });
    expect(screen.getByText(/1 in error/)).toBeInTheDocument();
    fireEvent.click(button);
    expect(openWindow).toHaveBeenCalledWith("agents", expect.anything());
  });

  it("links a degraded cluster row into the Cluster app", async () => {
    mockEndpoints({
      "/api/health?detailed=true": () =>
        Promise.resolve(
          jsonResponse({
            ...HEALTH,
            subsystems: {
              ...HEALTH.subsystems,
              cluster: { status: "degraded", workers_total: 2, workers_online: 0 },
            },
          }),
        ),
    });
    render(<SystemHealthCard />);
    const button = await screen.findByRole("button", { name: /cluster workers: degraded/i });
    expect(screen.getByText(/0\/2 online/)).toBeInTheDocument();
    fireEvent.click(button);
    expect(openWindow).toHaveBeenCalledWith("cluster", expect.anything());
  });

  it("shows recent errors and links the row into the Memory app", async () => {
    mockEndpoints({
      "/api/memory/stats": () =>
        Promise.resolve(
          jsonResponse({
            ...MEMORY,
            recent_errors: ["embed failed: connection refused", "catalog write timeout"],
          }),
        ),
    });
    render(<SystemHealthCard />);
    const button = await screen.findByRole("button", { name: /recent errors: degraded/i });
    expect(screen.getByText(/2 recent errors/)).toBeInTheDocument();
    fireEvent.click(button);
    expect(openWindow).toHaveBeenCalledWith("memory", expect.anything());
  });

  it("reports the gateway down when the controller cannot be reached", async () => {
    mockEndpoints({
      "/api/health?detailed=true": () => Promise.reject(new Error("network down")),
    });
    render(<SystemHealthCard />);
    await waitFor(() =>
      expect(screen.getByText("Some systems are down")).toBeInTheDocument(),
    );
    expect(screen.getByText(/controller unreachable/i)).toBeInTheDocument();
    const button = screen.getByRole("button", { name: /llm gateway: error/i });
    fireEvent.click(button);
    expect(openWindow).toHaveBeenCalledWith("providers", expect.anything());
  });

  it("ignores a superseded refresh that resolves after a newer one", async () => {
    vi.useFakeTimers();
    try {
      let resolveStale: (r: Response) => void = () => {};
      let healthCheckCalls = 0;
      mockEndpoints({
        "/api/health-check": () => {
          healthCheckCalls += 1;
          if (healthCheckCalls === 1) {
            return new Promise<Response>((resolve) => {
              resolveStale = resolve;
            });
          }
          return Promise.resolve(jsonResponse({ checks: CHECKS }));
        },
      });
      render(<SystemHealthCard />);
      // The first load is parked on its health-check. A focus refresh
      // starts a second load (useRefreshOnFocus coalesces at 1000 ms),
      // which resolves healthy and commits.
      fireEvent.focus(window);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      await act(async () => {
        await Promise.resolve();
      });
      expect(screen.getByText("All systems healthy")).toBeInTheDocument();
      // The stale first load now resolves with a dead container runtime;
      // it must not overwrite the newer rows.
      resolveStale(
        jsonResponse({
          checks: [
            { name: "incusd", status: "error", detail: "Not running", response_ms: 1 },
            { name: "Docker", status: "error", detail: "Not running", response_ms: 1 },
          ],
        }),
      );
      await act(async () => {
        await Promise.resolve();
      });
      expect(screen.getByText("All systems healthy")).toBeInTheDocument();
      expect(screen.queryByText("Some systems are down")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps the rest of the card alive when one endpoint fails", async () => {
    mockEndpoints({
      "/api/memory/stats": () => Promise.resolve(jsonResponse({ error: "boom" }, 500)),
    });
    render(<SystemHealthCard />);
    // Memory rows degrade independently; the agents row still reads fine.
    await waitFor(() =>
      expect(screen.getByText(/memory service unreachable/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/2 running · 0 stopped/)).toBeInTheDocument();
    expect(screen.getByText(/error history unavailable/i)).toBeInTheDocument();
  });
});
