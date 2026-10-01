import { withCsrf } from "@/lib/csrf";

/**
 * API-client helpers for an agent's scope grants (taOS #2985).
 *
 * ``listAgentGrants`` reads the agent's grants from the registry feed
 * endpoint narrowed with ``?canonical_id=``. An admin session gets the full
 * list (expired rows included); the UI filters to active grants itself.
 *
 * ``revokeAgentProjectGrants`` posts to the owner/admin-gated revoke route
 * backing the Agents-app revoke surface. The CSRF header is attached by
 * ``withCsrf`` so a cookie-authenticated session passes the router-wide
 * ``verify_csrf`` gate. An omitted/empty ``scopes`` list revokes everything
 * the agent holds on that one project.
 */

export type AgentGrant = {
  canonical_id: string;
  scope: string;
  tier?: string;
  project_id: string | null;
  granted_at: string;
  expires_at: string | null;
};

export type RevokeAgentResult = {
  canonical_id: string;
  project_id: string;
  revoked_scopes: string[];
  active_scopes: string[];
};

export async function listAgentGrants(canonicalId: string): Promise<AgentGrant[]> {
  const resp = await fetch(
    `/api/agents/registry/grants?canonical_id=${encodeURIComponent(canonicalId)}`,
    { credentials: "include" },
  );
  if (!resp.ok) {
    throw new Error(`Failed to load grants (HTTP ${resp.status})`);
  }
  const body = (await resp.json()) as { grants?: AgentGrant[] };
  return Array.isArray(body?.grants) ? body.grants : [];
}

export async function revokeAgentProjectGrants(
  projectId: string,
  canonicalId: string,
  scopes: string[] = [],
): Promise<RevokeAgentResult> {
  const resp = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/members/revoke-agent`,
    withCsrf({
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ canonical_id: canonicalId, scopes }),
    }),
  );
  if (!resp.ok) {
    const err = (await resp.json().catch(() => ({}))) as {
      error?: string;
      detail?: string;
    };
    throw new Error(
      err.error ?? err.detail ?? `Failed to revoke grants (HTTP ${resp.status})`,
    );
  }
  return (await resp.json()) as RevokeAgentResult;
}