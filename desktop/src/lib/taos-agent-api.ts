/**
 * API helpers for the taOS system agent configuration endpoints.
 */

export interface TaosAgentConfig {
  model: string | null;
  permitted_models: string[];
  persona: string;
  key_masked: string | null;
  framework: "opencode";
  system: true;
}

async function _request(url: string, method: string, body?: unknown): Promise<unknown> {
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const err = await res.json();
      if (err?.error) detail = String(err.error);
      else if (err?.detail) detail = String(err.detail);
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

export async function fetchTaosAgentConfig(): Promise<TaosAgentConfig> {
  return _request("/api/taos-agent/config", "GET") as Promise<TaosAgentConfig>;
}

export async function setTaosAgentModel(model: string): Promise<{ model: string }> {
  return _request("/api/taos-agent/settings", "PATCH", { model }) as Promise<{ model: string }>;
}

export async function setTaosAgentPermitted(
  models: string[],
): Promise<{ permitted_models: string[]; key_rescoped: boolean }> {
  return _request("/api/taos-agent/permitted-models", "PUT", { models }) as Promise<{
    permitted_models: string[];
    key_rescoped: boolean;
  }>;
}

export async function setTaosAgentPersona(persona: string): Promise<{ persona: string }> {
  return _request("/api/taos-agent/persona", "PUT", { persona }) as Promise<{ persona: string }>;
}
