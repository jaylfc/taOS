/**
 * Shared cluster-wide model aggregation.
 *
 * The Models app and the Deploy Agent wizard both want to show a UNION of
 * models available anywhere in the TAOS cluster:
 *   1. Controller-local downloaded models (from /api/models downloaded_files)
 *   2. Worker-hosted models reported via /api/cluster/workers
 *      (each worker.backends[].models[])
 *   3. Cloud provider models (from /api/providers)
 *
 * Each entry carries a `host` field (controller name, worker name, or
 * provider display name) so the UI can show a host badge matching the
 * teal badge pattern used by the Loaded Models widget in ActivityApp.
 */
import type { ClusterWorker } from "@/lib/cluster";

export type ModelHostKind = "controller" | "worker" | "cloud";

export interface AggregatedModel {
  /** Stable identifier unique per (host, id). */
  key: string;
  /** Model identifier (filename for local, model id for workers/cloud). */
  id: string;
  /** Display name. */
  name: string;
  /** Where the model lives. */
  host: string;
  hostKind: ModelHostKind;
  /** Optional backend/runtime label (e.g. "llama.cpp", "ollama"). */
  backend?: string;
  /** Optional size label, if known. */
  size?: string;
  /** Optional format (GGUF, etc.). */
  format?: string;
  quantization?: string;
}

const SIZE_RE = /[Qq]\d[_A-Za-z0-9]*/;

function fmtSize(mb: number): string {
  if (!mb || mb <= 0) return "";
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`;
}

export interface ControllerDownloaded {
  filename: string;
  size_mb?: number;
  format?: string;
}

/** Map a controller /api/models downloaded_files entry to an AggregatedModel. */
export function controllerDownloadedToAggregated(
  d: ControllerDownloaded,
  host = "controller",
): AggregatedModel {
  const filename = d.filename ?? "unknown";
  const quant = filename.match(SIZE_RE)?.[0]?.toUpperCase();
  return {
    key: `controller:${filename}`,
    id: filename,
    name: filename,
    host,
    hostKind: "controller",
    size: fmtSize(d.size_mb ?? 0),
    format: (d.format ?? "bin").toUpperCase(),
    quantization: quant,
  };
}

/** Resolve a display name for a worker — prefer the registered name unless it's
 *  generic ("localhost", "127.0.0.1"), in which case fall back to the URL hostname. */
function workerDisplayName(w: ClusterWorker): string {
  const name = (w.name ?? "").trim();
  const generic = !name || name === "localhost" || name === "127.0.0.1";
  if (!generic) return name;
  try { return new URL(w.url).hostname || name || "worker"; } catch { return name || "worker"; }
}

/** Flatten worker.backends[].models[] into AggregatedModel entries.
 *  Falls back to top-level w.models[] for older workers that don't report backends. */
export function workersToAggregated(workers: ClusterWorker[]): AggregatedModel[] {
  const out: AggregatedModel[] = [];
  for (const w of workers) {
    const wname = workerDisplayName(w);

    // Prefer nested backends[].models[] — more info (backend name/type)
    const backends = w.backends ?? [];
    if (backends.length > 0) {
      for (const b of backends) {
        const backendName = b.name ?? b.type ?? "backend";
        for (const model of b.models ?? []) {
          if (typeof model === "string") {
            out.push({ key: `worker:${wname}:${backendName}:${model}`, id: model, name: model, host: wname, hostKind: "worker", backend: backendName });
          } else if (model && typeof model === "object") {
            const id = (model.id ?? model.name ?? "unknown") as string;
            const nm = (model.name ?? model.id ?? "unknown") as string;
            const sz = typeof model.size_mb === "number" ? fmtSize(model.size_mb) : undefined;
            out.push({ key: `worker:${wname}:${backendName}:${id}`, id, name: nm, host: wname, hostKind: "worker", backend: backendName, size: sz });
          }
        }
      }
    } else if (Array.isArray(w.models) && w.models.length > 0) {
      // Fallback: flat model list with no backend detail
      for (const model of w.models) {
        out.push({ key: `worker:${wname}:${model}`, id: model, name: model, host: wname, hostKind: "worker" });
      }
    }
  }
  return out;
}

export interface CloudProvider {
  name?: string;
  type?: string;
  model?: string;
  models?: { id?: string; name?: string }[];
}

export const CLOUD_PROVIDER_TYPES = ["openai", "anthropic", "openrouter", "kilocode", "openai-compatible"] as const;

/** Flatten /api/providers cloud providers into AggregatedModel entries. */
export function cloudProvidersToAggregated(providers: CloudProvider[]): AggregatedModel[] {
  const out: AggregatedModel[] = [];
  for (const p of providers ?? []) {
    if (!p || !p.type || !(CLOUD_PROVIDER_TYPES as readonly string[]).includes(p.type)) continue;
    const providerName = p.name ?? p.type;
    const list = Array.isArray(p.models) ? p.models : [];
    if (list.length === 0) {
      const id = p.model ?? "default";
      out.push({
        key: `cloud:${providerName}:${id}`,
        id,
        name: `${providerName} default`,
        host: providerName,
        hostKind: "cloud",
        backend: p.type,
      });
      continue;
    }
    for (const m of list) {
      const id = m.id ?? m.name ?? "unknown";
      out.push({
        key: `cloud:${providerName}:${id}`,
        id,
        name: m.name ?? id,
        host: providerName,
        hostKind: "cloud",
        backend: p.type,
      });
    }
  }
  return out;
}

/**
 * Fetch /api/cluster/workers and return the workers list.
 * Returns [] on any failure so callers can cheerfully union it in.
 */
export async function fetchClusterWorkers(): Promise<ClusterWorker[]> {
  try {
    const res = await fetch("/api/cluster/workers", {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return [];
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return [];
    const data = await res.json();
    if (Array.isArray(data)) return data as ClusterWorker[];
    if (Array.isArray(data?.workers)) return data.workers as ClusterWorker[];
    return [];
  } catch {
    return [];
  }
}

/** Fetch /api/providers and return the raw array. */
export async function fetchCloudProviders(): Promise<CloudProvider[]> {
  try {
    const res = await fetch("/api/providers", {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return [];
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return [];
    const data = await res.json();
    return Array.isArray(data) ? (data as CloudProvider[]) : [];
  } catch {
    return [];
  }
}

/** Teal host badge class matching the Loaded Models widget. */
export const HOST_BADGE_CLASS =
  "text-[9px] px-1.5 py-0.5 rounded-full bg-teal-500/15 text-teal-200 font-semibold whitespace-nowrap";
