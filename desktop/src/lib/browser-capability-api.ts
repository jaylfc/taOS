/**
 * Fetch wrappers for /api/desktop/browser/capabilities.
 * Silent on 401 (returns []/null/false to match other browser-* api wrappers).
 */
export interface CapabilityGrant {
  agent_id: string;
  host_pattern: string;
  permissions: string;
  granted_at: string;
  expires_at: string | null;
}

export async function listCapabilities(
  profileId: string,
  agentId?: string,
): Promise<CapabilityGrant[]> {
  const params = new URLSearchParams({ profile_id: profileId });
  if (agentId) params.set("agent_id", agentId);
  try {
    const resp = await fetch(`/api/desktop/browser/capabilities?${params}`, {
      credentials: "include",
    });
    if (!resp.ok) return [];
    const body = await resp.json();
    return Array.isArray(body?.grants) ? body.grants : [];
  } catch {
    return [];
  }
}

export async function grantCapability(
  profileId: string,
  agentId: string,
  hostPattern: string,
  permissions: string,
  expiresAt: string | null = null,
): Promise<{ granted: boolean } | { error: string } | null> {
  try {
    const resp = await fetch("/api/desktop/browser/capabilities", {
      method: "POST",
      credentials: "include",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        profile_id: profileId,
        agent_id: agentId,
        host_pattern: hostPattern,
        permissions,
        expires_at: expiresAt,
      }),
    });
    if (resp.ok) return await resp.json();
    if (resp.status === 400) {
      const body = await resp.json().catch(() => ({}));
      return { error: typeof body?.error === "string" ? body.error : "Bad request" };
    }
    return null;
  } catch {
    return null;
  }
}

export async function revokeCapability(
  profileId: string,
  agentId: string,
  hostPattern: string,
): Promise<boolean> {
  const params = new URLSearchParams({
    profile_id: profileId,
    agent_id: agentId,
    host_pattern: hostPattern,
  });
  try {
    const resp = await fetch(`/api/desktop/browser/capabilities?${params}`, {
      method: "DELETE",
      credentials: "include",
    });
    return resp.ok;
  } catch {
    return false;
  }
}
