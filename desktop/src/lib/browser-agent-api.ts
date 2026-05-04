/**
 * Fetch wrappers for /api/desktop/browser/pins and copilot ticket minting.
 * Silent on 401 (matches other browser-* api wrappers).
 */

export interface PinDto {
  agent_id: string;
  pinned_at: string;
}

export interface CopilotTicket {
  ticket: string;
  ttl_seconds: number;
}

export async function listPins(profileId: string, tabId: string): Promise<PinDto[]> {
  const params = new URLSearchParams({ profile_id: profileId, tab_id: tabId });
  try {
    const resp = await fetch(`/api/desktop/browser/pins?${params}`, {
      credentials: "include",
    });
    if (!resp.ok) return [];
    const body = await resp.json();
    return Array.isArray(body?.pins) ? body.pins : [];
  } catch {
    return [];
  }
}

/** Returns the boolean from the backend on success, an error string on 4xx, null on network/5xx. */
export async function pinAgent(
  profileId: string,
  tabId: string,
  agentId: string,
): Promise<{ pinned: boolean } | { error: string } | null> {
  try {
    const resp = await fetch("/api/desktop/browser/pins", {
      method: "POST",
      credentials: "include",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        profile_id: profileId,
        tab_id: tabId,
        agent_id: agentId,
      }),
    });
    if (resp.ok) {
      return await resp.json(); // { pinned: boolean }
    }
    if (resp.status === 400 || resp.status === 404) {
      const body = await resp.json().catch(() => ({}));
      return { error: typeof body?.error === "string" ? body.error : `HTTP ${resp.status}` };
    }
    return null;
  } catch {
    return null;
  }
}

export async function unpinAgent(
  profileId: string,
  tabId: string,
  agentId: string,
): Promise<boolean> {
  const params = new URLSearchParams({
    profile_id: profileId,
    tab_id: tabId,
    agent_id: agentId,
  });
  try {
    const resp = await fetch(`/api/desktop/browser/pins?${params}`, {
      method: "DELETE",
      credentials: "include",
    });
    return resp.ok;
  } catch {
    return false;
  }
}

export async function mintCopilotTicket(
  profileId: string,
  tabId: string,
  agentId: string,
): Promise<CopilotTicket | null> {
  try {
    const resp = await fetch("/api/desktop/browser/copilot/ticket", {
      method: "POST",
      credentials: "include",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        profile_id: profileId,
        tab_id: tabId,
        agent_id: agentId,
      }),
    });
    if (!resp.ok) return null;
    return await resp.json();
  } catch {
    return null;
  }
}
