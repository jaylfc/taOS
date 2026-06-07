export type FrameworkVersion = { tag: string | null; sha: string | null };
export type LatestVersion = { tag: string; sha: string; published_at?: string };

export interface FrameworkState {
  framework: string;
  installed: FrameworkVersion;
  latest: LatestVersion | null;
  update_available: boolean;
  update_status: "idle" | "updating" | "failed";
  update_started_at: number | null;
  last_error: string | null;
  last_snapshot: string | null;
}

export async function fetchFrameworkState(slug: string): Promise<FrameworkState> {
  const r = await fetch(`/api/agents/${encodeURIComponent(slug)}/framework`);
  if (!r.ok) throw new Error(`framework fetch ${r.status}`);
  return r.json();
}

export async function startFrameworkUpdate(slug: string, targetVersion?: string): Promise<void> {
  const r = await fetch(`/api/agents/${encodeURIComponent(slug)}/framework/update`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(targetVersion ? { target_version: targetVersion } : {}),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.error || `update start ${r.status}`);
  }
}

export async function fetchLatestFrameworks(refresh = false): Promise<Record<string, LatestVersion>> {
  const r = await fetch(`/api/frameworks/latest${refresh ? "?refresh=true" : ""}`);
  if (!r.ok) throw new Error(`latest frameworks ${r.status}`);
  return r.json();
}

export interface PermittedModelsState {
  permitted: string[];
  current: string;
}

export async function fetchPermittedModels(name: string): Promise<PermittedModelsState> {
  const r = await fetch(`/api/agents/${encodeURIComponent(name)}/permitted-models`);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.error || `permitted-models fetch ${r.status}`);
  }
  return r.json();
}

export async function setPermittedModels(name: string, models: string[]): Promise<PermittedModelsState> {
  const r = await fetch(`/api/agents/${encodeURIComponent(name)}/permitted-models`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ models }),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.error || `permitted-models set ${r.status}`);
  }
  return r.json();
}
