import { describe, it, expect } from "vitest";
import {
  buildHealthRows,
  overallHealthStatus,
  type SystemHealthInput,
} from "./system-health";

// Fixtures shaped like the real endpoints:
// - GET /api/health?detailed=true  (routes/dashboard.py api_health)
// - GET /api/health-check          (routes/dashboard.py api_health_check)
// - GET /api/agents                (routes/agents.py list_agents)
// - GET /api/memory/stats          (routes/memory_management.py memory_stats)

function healthyInput(): SystemHealthInput {
  return {
    health: {
      status: "ok",
      agents: 2,
      backends: 1,
      subsystems: {
        cluster: { status: "ok", workers_total: 2, workers_online: 2 },
        scheduler: { status: "ok", backends: 1 },
        qmd: { status: "ok", url: "http://127.0.0.1:7835" },
        controller_identity: { status: "ok", reason: "" },
      },
    },
    checks: [
      { name: "TinyAgentOS", status: "ok", detail: "Running", response_ms: 0 },
      { name: "incusd", status: "ok", detail: "Running", response_ms: 3 },
      { name: "Docker", status: "ok", detail: "Docker version 27", response_ms: 5 },
      { name: "Docker Compose v2", status: "ok", detail: "Docker Compose v2.29", response_ms: 4 },
      { name: "Backend: rkllama", status: "ok", detail: "12ms, 3 models", response_ms: 12 },
      { name: "QMD Server", status: "ok", detail: "8ms", response_ms: 8 },
      { name: "Disk Space", status: "ok", detail: "40.0% used, 60.0 GB free", response_ms: 0 },
      { name: "RAM Usage", status: "ok", detail: "30.0% used, 5.6 GB available", response_ms: 0 },
      { name: "Worker: pi5", status: "ok", detail: "online, last heartbeat 2s ago, linux/arm64", response_ms: 0 },
    ],
    agents: [
      { name: "alpha", status: "running" },
      { name: "beta", status: "running" },
    ],
    memoryStats: {
      pipeline: { archive: "idle", catalog: "idle", vector: "idle", kg: "idle", crystal: "idle" },
      recent_errors: [],
    },
  };
}

function rowById(rows: ReturnType<typeof buildHealthRows>, id: string) {
  const row = rows.find((r) => r.id === id);
  if (!row) throw new Error(`row ${id} missing`);
  return row;
}

describe("buildHealthRows", () => {
  it("reports every glance item healthy on a healthy system", () => {
    const rows = buildHealthRows(healthyInput());
    expect(rows.map((r) => r.id)).toEqual([
      "gateway",
      "agents",
      "cluster",
      "containers",
      "backends",
      "memory",
      "errors",
    ]);
    for (const row of rows) {
      expect(row.status, row.id).toBe("ok");
      // Healthy rows carry no app link: only unhealthy items link out.
      expect(row.appId, row.id).toBeUndefined();
    }
    expect(overallHealthStatus(rows)).toBe("ok");
  });

  it("derives the agents row counts from /api/agents statuses", () => {
    const input = healthyInput();
    input.agents = [
      { name: "a", status: "running" },
      { name: "b", status: "stopped" },
      { name: "c", status: "stopped" },
    ];
    const row = rowById(buildHealthRows(input), "agents");
    expect(row.detail).toContain("1 running");
    expect(row.detail).toContain("2 stopped");
    expect(row.status).toBe("ok");
  });

  it("flags an errored agent as an error row linking to the Agents app", () => {
    const input = healthyInput();
    input.agents = [
      { name: "a", status: "running" },
      { name: "b", status: "error" },
    ];
    const row = rowById(buildHealthRows(input), "agents");
    expect(row.status).toBe("error");
    expect(row.detail).toContain("1 in error");
    expect(row.appId).toBe("agents");
  });

  it("marks all-stopped agents degraded and an empty list unavailable", () => {
    const input = healthyInput();
    input.agents = [{ name: "a", status: "stopped" }];
    expect(rowById(buildHealthRows(input), "agents").status).toBe("degraded");
    input.agents = [];
    const empty = rowById(buildHealthRows(input), "agents");
    expect(empty.status).toBe("unavailable");
    expect(empty.detail).toMatch(/no agents/i);
    input.agents = null;
    expect(rowById(buildHealthRows(input), "agents").status).toBe("unavailable");
  });

  it("mirrors the cluster subsystem status with online counts", () => {
    const input = healthyInput();
    input.health!.subsystems!.cluster = { status: "degraded", workers_total: 3, workers_online: 1 };
    const row = rowById(buildHealthRows(input), "cluster");
    expect(row.status).toBe("degraded");
    expect(row.detail).toContain("1/3 online");
    expect(row.appId).toBe("cluster");
  });

  it("treats an unreachable controller as a gateway error", () => {
    const input = healthyInput();
    input.health = null;
    const rows = buildHealthRows(input);
    expect(rowById(rows, "gateway").status).toBe("error");
    expect(rowById(rows, "gateway").appId).toBe("providers");
    expect(rowById(rows, "cluster").status).toBe("unavailable");
    expect(overallHealthStatus(rows)).toBe("error");
  });

  it("surfaces a controller identity error as its own row", () => {
    const input = healthyInput();
    input.health!.subsystems!.controller_identity = {
      status: "error",
      reason: "could not mint identity",
    };
    const rows = buildHealthRows(input);
    const row = rowById(rows, "identity");
    expect(row.status).toBe("error");
    expect(row.detail).toContain("could not mint identity");
    expect(overallHealthStatus(rows)).toBe("error");
  });

  it("derives containers from the incusd check, with Docker in the detail", () => {
    const input = healthyInput();
    input.checks = input.checks!.map((c) =>
      c.name === "incusd" ? { ...c, status: "error", detail: "incus daemon unreachable" } : c,
    );
    const row = rowById(buildHealthRows(input), "containers");
    expect(row.status).toBe("error");
    expect(row.detail).toContain("incus daemon unreachable");
    expect(row.detail).toContain("Docker ok");
    expect(row.appId).toBe("agents");
  });

  it("degrades containers when incus is absent but Docker is present", () => {
    const input = healthyInput();
    input.checks = input.checks!.map((c) =>
      c.name === "incusd" ? { ...c, status: "unavailable", detail: "incus not installed" } : c,
    );
    expect(rowById(buildHealthRows(input), "containers").status).toBe("degraded");
  });

  it("aggregates per-backend checks into one backends row", () => {
    const input = healthyInput();
    input.checks = [
      ...(input.checks ?? []),
      { name: "Backend: ollama", status: "error", detail: "unreachable", response_ms: 0 },
    ];
    const row = rowById(buildHealthRows(input), "backends");
    expect(row.status).toBe("degraded");
    expect(row.detail).toContain("1/2 reachable");
    expect(row.detail).toContain("ollama");
    expect(row.appId).toBe("providers");
  });

  it("marks the backends row error when every backend is down", () => {
    const input = healthyInput();
    input.checks = [{ name: "Backend: rkllama", status: "error", detail: "unreachable", response_ms: 0 }];
    expect(rowById(buildHealthRows(input), "backends").status).toBe("error");
  });

  it("reports no configured backends as unavailable, not an error", () => {
    const input = healthyInput();
    input.checks = (input.checks ?? []).filter((c) => !c.name.startsWith("Backend:"));
    const row = rowById(buildHealthRows(input), "backends");
    expect(row.status).toBe("unavailable");
    expect(row.detail).toMatch(/no backends/i);
  });

  it("flags a pipeline stage error on the memory row", () => {
    const input = healthyInput();
    input.memoryStats = {
      pipeline: { archive: "idle", catalog: "error", vector: "idle", kg: "idle", crystal: "idle" },
      recent_errors: [],
    };
    const row = rowById(buildHealthRows(input), "memory");
    expect(row.status).toBe("error");
    expect(row.detail).toContain("catalog: error");
    expect(row.appId).toBe("memory");
  });

  it("degrades the memory row when QMD is down even if stages are idle", () => {
    const input = healthyInput();
    input.checks = input.checks!.map((c) =>
      c.name === "QMD Server" ? { ...c, status: "error", detail: "unreachable" } : c,
    );
    const row = rowById(buildHealthRows(input), "memory");
    expect(row.status).toBe("degraded");
    expect(row.detail).toContain("QMD error");
  });

  it("treats an unreachable memory service as an error and errors row unavailable", () => {
    const input = healthyInput();
    input.memoryStats = null;
    const rows = buildHealthRows(input);
    expect(rowById(rows, "memory").status).toBe("error");
    expect(rowById(rows, "errors").status).toBe("unavailable");
  });

  it("counts recent errors on their own row, linking to Memory", () => {
    const input = healthyInput();
    input.memoryStats = {
      pipeline: { archive: "idle" },
      recent_errors: ["embed failed: connection refused", "catalog write timeout"],
    };
    const rows = buildHealthRows(input);
    const row = rowById(rows, "errors");
    expect(row.status).toBe("degraded");
    expect(row.detail).toContain("2 recent errors");
    expect(row.detail).toContain("embed failed");
    expect(row.appId).toBe("memory");
    // Recent errors also keep the memory row from reading fully healthy.
    expect(rowById(rows, "memory").status).toBe("degraded");
  });
});

describe("overallHealthStatus", () => {
  it("lets errors dominate, then degraded", () => {
    const input = healthyInput();
    input.agents = [{ name: "a", status: "stopped" }];
    expect(overallHealthStatus(buildHealthRows(input))).toBe("degraded");
    input.memoryStats = null;
    expect(overallHealthStatus(buildHealthRows(input))).toBe("error");
  });

  it("does not let unavailable rows drag down an otherwise healthy verdict", () => {
    const input = healthyInput();
    input.agents = [];
    input.checks = (input.checks ?? []).filter((c) => !c.name.startsWith("Backend:"));
    expect(overallHealthStatus(buildHealthRows(input))).toBe("ok");
  });

  it("reports error when nothing could be read at all (controller unreachable)", () => {
    const rows = buildHealthRows({ health: null, checks: null, agents: null, memoryStats: null });
    expect(overallHealthStatus(rows)).toBe("error");
  });
});
