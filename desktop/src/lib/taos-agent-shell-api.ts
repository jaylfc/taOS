/**
 * API helpers for the taOS Agent's shell profile: the programs the user has
 * allowed or denied for the Claude Code harness's shell, built up as the
 * agent asks. See tinyagentos/taos_agent_shell_profile.py.
 */

export interface ShellProfile {
  allow: string[];
  deny: string[];
  /** Programs with an approve/deny Decision still waiting, mapped to its id. */
  asked: Record<string, string>;
}

export type ShellEffect = "allow" | "deny";

async function _request(url: string, method: string, body?: unknown): Promise<ShellProfile> {
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
  return res.json() as Promise<ShellProfile>;
}

export async function fetchShellProfile(): Promise<ShellProfile> {
  return _request("/api/taos-agent/shell-profile", "GET");
}

export async function setShellRule(program: string, effect: ShellEffect): Promise<ShellProfile> {
  return _request(`/api/taos-agent/shell-profile/${encodeURIComponent(program)}`, "PUT", { effect });
}

export async function revokeShellRule(program: string): Promise<ShellProfile> {
  return _request(`/api/taos-agent/shell-profile/${encodeURIComponent(program)}`, "DELETE");
}
