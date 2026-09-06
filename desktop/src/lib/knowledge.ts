/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface MonitorConfig {
  frequency?: number;
  current_interval?: number;
  decay_rate?: number;
  stop_after_days?: number;
  pinned?: boolean;
  last_poll?: number;
  last_hash?: string;
}

export interface KnowledgeItem {
  id: string;
  source_type: string;
  source_url: string;
  source_id: string | null;
  title: string;
  author: string;
  summary: string;
  content: string;
  media_path: string | null;
  thumbnail: string | null;
  categories: string[];
  tags: string[];
  metadata: Record<string, unknown>;
  status: string;
  monitor: MonitorConfig;
  created_at: number;
  updated_at: number;
}

export interface Snapshot {
  id: number;
  item_id: string;
  snapshot_at: number;
  content_hash: string;
  diff_json: Record<string, unknown>;
  metadata_json: Record<string, unknown>;
}

export interface CategoryRule {
  id: number;
  pattern: string;
  match_on: string;
  category: string;
  priority: number;
}

export interface AgentSubscription {
  agent_name: string;
  category: string;
  auto_ingest: boolean;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

async function fetchJson<T>(url: string, fallback: T, init?: RequestInit): Promise<T> {
  try {
    const headers = new Headers(init?.headers);
    headers.set("Accept", "application/json");
    const res = await fetch(url, { ...init, headers });
    if (!res.ok) return fallback;
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return fallback;
    return await res.json();
  } catch {
    return fallback;
  }
}

async function postJson<T>(url: string, body: unknown, fallback: T): Promise<T> {
  return fetchJson(url, fallback, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/* ------------------------------------------------------------------ */
/*  Items                                                              */
/* ------------------------------------------------------------------ */

export interface ListItemsParams {
  source_type?: string;
  status?: string;
  category?: string;
  limit?: number;
  offset?: number;
}

export async function listItems(params?: ListItemsParams): Promise<{ items: KnowledgeItem[]; count: number }> {
  const qs = new URLSearchParams();
  if (params?.source_type) qs.set("source_type", params.source_type);
  if (params?.status) qs.set("status", params.status);
  if (params?.category) qs.set("category", params.category);
  if (params?.limit != null) qs.set("limit", String(params.limit));
  if (params?.offset != null) qs.set("offset", String(params.offset));
  const query = qs.toString();
  const url = `/api/knowledge/items${query ? `?${query}` : ""}`;
  const data = await fetchJson<{ items: KnowledgeItem[]; count: number }>(url, { items: [], count: 0 });
  return { items: Array.isArray(data.items) ? data.items : [], count: data.count ?? 0 };
}

export async function getItem(id: string): Promise<KnowledgeItem | null> {
  try {
    const res = await fetch(`/api/knowledge/items/${encodeURIComponent(id)}`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return null;
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function deleteItem(id: string): Promise<boolean> {
  try {
    const res = await fetch(`/api/knowledge/items/${encodeURIComponent(id)}`, {
      method: "DELETE",
      headers: { Accept: "application/json" },
    });
    return res.ok;
  } catch {
    return false;
  }
}

/* ------------------------------------------------------------------ */
/*  Search                                                             */
/* ------------------------------------------------------------------ */

export async function searchItems(
  query: string,
  mode: "keyword" | "semantic" = "keyword",
  limit = 20,
): Promise<{ results: KnowledgeItem[]; mode: string }> {
  const qs = new URLSearchParams({ q: query, mode, limit: String(limit) });
  const data = await fetchJson<{ results: KnowledgeItem[]; mode: string }>(
    `/api/knowledge/search?${qs}`,
    { results: [], mode },
  );
  return { results: Array.isArray(data.results) ? data.results : [], mode: data.mode ?? mode };
}

/* ------------------------------------------------------------------ */
/*  Ingest                                                             */
/* ------------------------------------------------------------------ */

export interface IngestOptions {
  title?: string;
  text?: string;
  categories?: string[];
  source?: string;
}

export async function ingestUrl(
  url: string,
  opts?: IngestOptions,
): Promise<{ id: string; status: string } | null> {
  try {
    const res = await fetch("/api/knowledge/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        url,
        title: opts?.title ?? "",
        text: opts?.text ?? "",
        categories: opts?.categories ?? [],
        source: opts?.source ?? "library",
      }),
    });
    if (!res.ok) return null;
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------------ */
/*  Snapshots                                                          */
/* ------------------------------------------------------------------ */

export async function listSnapshots(itemId: string, limit = 20): Promise<Snapshot[]> {
  const data = await fetchJson<{ snapshots: Snapshot[] }>(
    `/api/knowledge/items/${encodeURIComponent(itemId)}/snapshots?limit=${limit}`,
    { snapshots: [] },
  );
  return Array.isArray(data.snapshots) ? data.snapshots : [];
}

/* ------------------------------------------------------------------ */
/*  Category rules                                                     */
/* ------------------------------------------------------------------ */

export async function listRules(): Promise<CategoryRule[]> {
  const data = await fetchJson<{ rules: CategoryRule[] }>("/api/knowledge/rules", { rules: [] });
  return Array.isArray(data.rules) ? data.rules : [];
}

export interface CreateRuleParams {
  pattern: string;
  match_on: string;
  category: string;
  priority: number;
}

export async function createRule(rule: CreateRuleParams): Promise<number | null> {
  const data = await postJson<{ id?: number }>("/api/knowledge/rules", rule, {});
  return data.id ?? null;
}

export async function deleteRule(id: number): Promise<boolean> {
  try {
    const res = await fetch(`/api/knowledge/rules/${id}`, {
      method: "DELETE",
      headers: { Accept: "application/json" },
    });
    return res.ok;
  } catch {
    return false;
  }
}

/* ------------------------------------------------------------------ */
/*  Agent subscriptions                                                */
/* ------------------------------------------------------------------ */

export async function listSubscriptions(agentName?: string): Promise<AgentSubscription[]> {
  const qs = agentName ? `?agent_name=${encodeURIComponent(agentName)}` : "";
  const data = await fetchJson<{ subscriptions: AgentSubscription[] }>(
    `/api/knowledge/subscriptions${qs}`,
    { subscriptions: [] },
  );
  return Array.isArray(data.subscriptions) ? data.subscriptions : [];
}

export async function setSubscription(sub: AgentSubscription): Promise<boolean> {
  const data = await postJson<{ status?: string }>("/api/knowledge/subscriptions", sub, {});
  return data.status === "ok";
}

export async function deleteSubscription(agentName: string, category: string): Promise<boolean> {
  try {
    const res = await fetch(
      `/api/knowledge/subscriptions/${encodeURIComponent(agentName)}/${encodeURIComponent(category)}`,
      { method: "DELETE", headers: { Accept: "application/json" } },
    );
    return res.ok;
  } catch {
    return false;
  }
}
