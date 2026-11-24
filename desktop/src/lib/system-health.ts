// #621: derivation logic for the System health glance card
// (apps/ActivityApp.systemHealth.tsx). Pure functions over the shapes of
// four existing endpoints — no new backend surface:
//
//   GET /api/health?detailed=true  per-subsystem blocks (cluster,
//                                   scheduler, qmd, controller identity)
//   GET /api/health-check           timed checks (incusd, Docker, each
//                                   backend, QMD, disk, RAM, workers)
//   GET /api/agents                 configured agents with a stored status
//   GET /api/memory/stats           memory pipeline stages + recent_errors
//
// The issue's "LiteLLM proxy up/down" item predates the LiteLLM removal:
// there is no proxy process any more, every agent's traffic goes through
// the in-process LLM gateway (tinyagentos/llm_gateway), so the gateway is
// up exactly when the controller answers /api/health. The gateway row
// below tracks that probe; the backends row tracks whether the gateway
// has anything reachable behind it.
//
// Disk, RAM and the scheduler are deliberately NOT rows here: the
// Activity app already gives them dedicated cards, and the issue asks to
// fill the gap rather than duplicate.

export type HealthStatus = "ok" | "degraded" | "error" | "unavailable";

export interface HealthSubsystem {
  status?: string;
  workers_total?: number;
  workers_online?: number;
  backends?: number;
  url?: string | null;
  reason?: string;
}

export interface DetailedHealth {
  status?: string;
  agents?: number;
  backends?: number;
  subsystems?: Record<string, HealthSubsystem>;
}

export interface HealthCheckEntry {
  name: string;
  status: string;
  detail?: string;
  response_ms?: number;
}

export interface AgentSummary {
  name?: string;
  status?: string;
}

export interface MemoryStats {
  pipeline?: Record<string, string>;
  recent_errors?: string[];
}

export interface SystemHealthInput {
  health: DetailedHealth | null;
  checks: HealthCheckEntry[] | null;
  agents: AgentSummary[] | null;
  memoryStats: MemoryStats | null;
}

export interface HealthRow {
  id: string;
  label: string;
  status: HealthStatus;
  detail: string;
  /** App to open for a closer look / a fix. Set only on unhealthy rows:
   *  the issue asks that each unhealthy item links to the relevant app. */
  appId?: string;
}

/** Map a backend status word onto the glance vocabulary. The health
 *  endpoints use ok / warning / error / unavailable (health-check) and
 *  ok / degraded / unavailable (detailed subsystems). */
export function mapHealthStatus(raw: string | undefined | null): HealthStatus {
  switch ((raw ?? "").toLowerCase()) {
    case "ok":
      return "ok";
    case "warning":
    case "warn":
    case "degraded":
      return "degraded";
    case "error":
    case "failed":
    case "failure":
      return "error";
    default:
      return "unavailable";
  }
}

function findCheck(checks: HealthCheckEntry[] | null, name: string): HealthCheckEntry | undefined {
  return checks?.find((c) => c.name.toLowerCase() === name.toLowerCase());
}

function gatewayRow(health: DetailedHealth | null): HealthRow {
  if (!health) {
    return {
      id: "gateway",
      label: "LLM gateway",
      status: "error",
      detail: "Controller unreachable — the gateway runs in-process with it",
      appId: "providers",
    };
  }
  if (health.status === "ok") {
    return {
      id: "gateway",
      label: "LLM gateway",
      status: "ok",
      detail: "Up — in-process gateway is responding",
    };
  }
  return {
    id: "gateway",
    label: "LLM gateway",
    status: "degraded",
    detail: `Controller reports "${health.status ?? "unknown"}"`,
    appId: "providers",
  };
}

function identityRow(health: DetailedHealth | null): HealthRow | null {
  const identity = health?.subsystems?.controller_identity;
  if (!identity || identity.status !== "error") return null;
  return {
    id: "identity",
    label: "Controller identity",
    status: "error",
    detail: identity.reason || "Identity could not be minted",
  };
}

function agentsRow(agents: AgentSummary[] | null): HealthRow {
  if (!agents) {
    return { id: "agents", label: "Agents", status: "unavailable", detail: "Agent list unavailable" };
  }
  if (agents.length === 0) {
    return { id: "agents", label: "Agents", status: "unavailable", detail: "No agents deployed" };
  }
  const running = agents.filter((a) => a.status === "running").length;
  const errored = agents.filter((a) => a.status === "error").length;
  const stopped = agents.length - running - errored;
  let detail = `${running} running · ${stopped} stopped`;
  if (errored > 0) detail += ` · ${errored} in error`;
  if (errored > 0) {
    return { id: "agents", label: "Agents", status: "error", detail, appId: "agents" };
  }
  if (running === 0) {
    return { id: "agents", label: "Agents", status: "degraded", detail, appId: "agents" };
  }
  return { id: "agents", label: "Agents", status: "ok", detail };
}

function clusterRow(health: DetailedHealth | null): HealthRow {
  const cluster = health?.subsystems?.cluster;
  if (!cluster) {
    return {
      id: "cluster",
      label: "Cluster workers",
      status: "unavailable",
      detail: "Cluster status unavailable",
    };
  }
  const status = mapHealthStatus(cluster.status);
  const detail =
    typeof cluster.workers_total === "number"
      ? cluster.workers_total === 0
        ? "No workers registered"
        : `${cluster.workers_online ?? 0}/${cluster.workers_total} online`
      : `Status: ${cluster.status ?? "unknown"}`;
  const row: HealthRow = { id: "cluster", label: "Cluster workers", status, detail };
  if (status === "degraded" || status === "error") row.appId = "cluster";
  return row;
}

function containersRow(checks: HealthCheckEntry[] | null): HealthRow {
  const incus = findCheck(checks, "incusd");
  const docker = findCheck(checks, "Docker");
  if (!incus && !docker) {
    return {
      id: "containers",
      label: "Containers",
      status: "unavailable",
      detail: "Container checks unavailable",
    };
  }
  const parts: string[] = [];
  if (incus?.detail) parts.push(incus.detail);
  if (docker) parts.push(`Docker ${docker.status}`);
  const detail = parts.join(" · ") || "No detail reported";
  let status: HealthStatus;
  if (incus) {
    status = mapHealthStatus(incus.status);
    if (status === "unavailable" && docker && mapHealthStatus(docker.status) === "ok") {
      // incus is the agent container backend; Docker alone still runs
      // Store apps, so the surface is degraded rather than absent.
      status = "degraded";
    }
  } else {
    status = docker ? mapHealthStatus(docker.status) : "unavailable";
  }
  const row: HealthRow = { id: "containers", label: "Containers", status, detail };
  if (status === "degraded" || status === "error") row.appId = "agents";
  return row;
}

function backendsRow(checks: HealthCheckEntry[] | null): HealthRow {
  if (!checks) {
    return {
      id: "backends",
      label: "Model backends",
      status: "unavailable",
      detail: "Backend checks unavailable",
    };
  }
  const backends = checks.filter((c) => c.name.startsWith("Backend:"));
  if (backends.length === 0) {
    return {
      id: "backends",
      label: "Model backends",
      status: "unavailable",
      detail: "No backends configured",
    };
  }
  const okCount = backends.filter((c) => mapHealthStatus(c.status) === "ok").length;
  if (okCount === backends.length) {
    return {
      id: "backends",
      label: "Model backends",
      status: "ok",
      detail: `${backends.length} reachable`,
    };
  }
  const firstFailure = backends.find((c) => mapHealthStatus(c.status) !== "ok");
  const allDown = backends.every((c) => mapHealthStatus(c.status) === "error");
  const status: HealthStatus = allDown ? "error" : "degraded";
  return {
    id: "backends",
    label: "Model backends",
    status,
    detail: `${okCount}/${backends.length} reachable · ${firstFailure!.name.replace(/^Backend:\s*/, "")} ${firstFailure!.status}`,
    appId: "providers",
  };
}

const STAGE_OK = new Set([
  "idle",
  "ok",
  "running",
  "active",
  "processing",
  "complete",
  "completed",
  "done",
  "ready",
]);
const STAGE_ERROR = new Set(["error", "failed", "failure", "down", "stalled"]);

function memoryRow(
  memoryStats: MemoryStats | null,
  checks: HealthCheckEntry[] | null,
  recentErrorCount: number,
): HealthRow {
  if (!memoryStats) {
    return {
      id: "memory",
      label: "Memory pipeline",
      status: "error",
      detail: "Memory service unreachable",
      appId: "memory",
    };
  }
  const pipeline = memoryStats.pipeline ?? {};
  const stages = Object.entries(pipeline);
  const errorStages = stages.filter(([, v]) => STAGE_ERROR.has(String(v).toLowerCase()));
  const oddStages = stages.filter(
    ([, v]) => !STAGE_OK.has(String(v).toLowerCase()) && !STAGE_ERROR.has(String(v).toLowerCase()),
  );
  const qmd = findCheck(checks, "QMD Server");
  const qmdDown = qmd !== undefined && mapHealthStatus(qmd.status) === "error";

  const parts: string[] = [];
  if (stages.length === 0) {
    parts.push("Pipeline status not reported");
  } else if (errorStages.length > 0) {
    parts.push(errorStages.map(([k, v]) => `${k}: ${v}`).join(", "));
  } else if (oddStages.length > 0) {
    parts.push(oddStages.map(([k, v]) => `${k}: ${v}`).join(", "));
  } else {
    const active = stages.filter(([, v]) => String(v).toLowerCase() !== "idle");
    parts.push(
      active.length > 0
        ? `Active: ${active.map(([k, v]) => `${k}: ${v}`).join(", ")}`
        : "All stages idle",
    );
  }
  if (qmd) parts.push(`QMD ${qmd.status}`);

  let status: HealthStatus = "ok";
  if (errorStages.length > 0) status = "error";
  else if (oddStages.length > 0 || qmdDown || recentErrorCount > 0) status = "degraded";
  const row: HealthRow = {
    id: "memory",
    label: "Memory pipeline",
    status,
    detail: parts.join(" · "),
  };
  if (status === "degraded" || status === "error") row.appId = "memory";
  return row;
}

function errorsRow(memoryStats: MemoryStats | null): HealthRow {
  if (!memoryStats) {
    return {
      id: "errors",
      label: "Recent errors",
      status: "unavailable",
      detail: "Error history unavailable",
    };
  }
  const errors = Array.isArray(memoryStats.recent_errors) ? memoryStats.recent_errors : [];
  if (errors.length === 0) {
    return { id: "errors", label: "Recent errors", status: "ok", detail: "No recent errors" };
  }
  const first = String(errors[0]);
  const truncated = first.length > 90 ? `${first.slice(0, 90)}…` : first;
  return {
    id: "errors",
    label: "Recent errors",
    status: "degraded",
    detail: `${errors.length} recent error${errors.length === 1 ? "" : "s"} — ${truncated}`,
    appId: "memory",
  };
}

/** Build the glance rows from the four endpoint payloads. Any payload may
 *  be null (that fetch failed); each row degrades independently so one
 *  dead endpoint never blanks the whole card. */
export function buildHealthRows(input: SystemHealthInput): HealthRow[] {
  const recentErrorCount = Array.isArray(input.memoryStats?.recent_errors)
    ? input.memoryStats.recent_errors.length
    : 0;
  const rows: HealthRow[] = [gatewayRow(input.health)];
  const identity = identityRow(input.health);
  if (identity) rows.push(identity);
  rows.push(
    agentsRow(input.agents),
    clusterRow(input.health),
    containersRow(input.checks),
    backendsRow(input.checks),
    memoryRow(input.memoryStats, input.checks, recentErrorCount),
    errorsRow(input.memoryStats),
  );
  return rows;
}

/** The card's headline verdict. Errors dominate, then degraded.
 *  Unavailable rows mean "not configured / not applicable" on this
 *  install (no workers, no backends yet) and do not drag the verdict
 *  down; the verdict is "unavailable" only when nothing at all could
 *  be read as healthy or unhealthy. */
export function overallHealthStatus(rows: HealthRow[]): HealthStatus {
  if (rows.some((r) => r.status === "error")) return "error";
  if (rows.some((r) => r.status === "degraded")) return "degraded";
  if (rows.some((r) => r.status === "ok")) return "ok";
  return "unavailable";
}
