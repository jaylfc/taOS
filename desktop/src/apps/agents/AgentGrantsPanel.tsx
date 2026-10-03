import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import {
  listAgentGrants,
  revokeAgentProjectGrants,
  type AgentGrant,
} from "@/lib/agent-grants";
import { projectsApi, type Project } from "@/lib/projects";
import { useFocusTrap } from "@/hooks/use-focus-trap";

/**
 * Agents-app revoke surface (taOS #2985).
 *
 * A modal an operator opens for a registered agent. It lists that agent's
 * ACTIVE grants grouped by project, with a per-scope Revoke and a per-project
 * "Revoke all", both backed by the admin-gated
 * `POST /api/projects/{id}/members/revoke-agent` route. Global grants
 * (project_id === null) are shown but not revocable here: there is no
 * project to revoke against, and the identity-level registry Revoke already
 * covers them.
 */

export type AgentGrantsTarget = {
  canonical_id: string;
  handle?: string;
  display_name?: string;
};

function isActive(g: AgentGrant, now: number): boolean {
  if (!g.expires_at) return true;
  const t = Date.parse(g.expires_at);
  return !Number.isNaN(t) && t > now;
}

function agentLabel(target: AgentGrantsTarget): string {
  return target.display_name || (target.handle ? `@${target.handle}` : target.canonical_id);
}

type GrantGroup = {
  key: string; // stable React key: project id, or "__global__" for null
  projectId: string | null;
  label: string;
  isGlobal: boolean;
  grants: AgentGrant[];
};

function projectLabel(projectId: string | null, projects: Project[]): string {
  if (projectId === null) return "Global (no project)";
  const p = projects.find((x) => x.id === projectId);
  return p?.name || p?.slug || projectId;
}

/**
 * Resolve the project labels used by the grant groups.
 *
 * `projectsApi.list()` defaults to `status="active"`, so a grant that lives on
 * an archived project would fall back to its raw id. Fetch the archived page
 * too and merge both by id (active wins on a collision). Both calls are
 * best-effort: a failure yields an empty page rather than blocking the panel,
 * and `projectLabel` still falls back to the id.
 */
async function loadProjectLabels(): Promise<Project[]> {
  const [active, archived] = await Promise.all([
    projectsApi.list("active").catch(() => []),
    projectsApi.list("archived").catch(() => []),
  ]);
  const byId = new Map<string, Project>();
  for (const list of [active, archived]) {
    if (!Array.isArray(list)) continue;
    for (const p of list) {
      if (p?.id && !byId.has(p.id)) byId.set(p.id, p);
    }
  }
  return [...byId.values()];
}

export function AgentGrantsPanel({
  target,
  onClose,
}: {
  target: AgentGrantsTarget;
  onClose: () => void;
}) {
  const [grants, setGrants] = useState<AgentGrant[] | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const loadSeq = useRef(0);
  const dialogRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  // Same modal shell as LicenseAcceptDialog/PermissionConsent: focus moves into
  // the dialog, Tab wraps inside it, and focus returns to the opener on close.
  useFocusTrap(dialogRef, true);

  // Wrapped so the effect dep is stable (matches RegistryPanel/LogsPanel); the
  // loadSeq guard still drops stale completions when the target changes.
  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    setError(null);
    try {
      const [gs, ps] = await Promise.all([
        listAgentGrants(target.canonical_id),
        // project labels are cosmetic; never block grants on them
        loadProjectLabels().catch(() => []),
      ]);
      if (seq !== loadSeq.current) return;
      setGrants(gs);
      setProjects(Array.isArray(ps) ? ps : []);
    } catch (e: unknown) {
      if (seq !== loadSeq.current) return;
      setError(e instanceof Error ? e.message : "Failed to load grants");
      setGrants(null);
    }
  }, [target.canonical_id]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCloseRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const groups = useMemo<GrantGroup[]>(() => {
    const rows = grants ?? [];
    const now = Date.now();
    const map = new Map<string, AgentGrant[]>();
    for (const g of rows) {
      if (!g?.scope) continue;
      if (!isActive(g, now)) continue;
      const raw = g.project_id ?? null;
      const key = raw === null ? "__global__" : raw;
      const bucket = map.get(key);
      if (bucket) bucket.push(g);
      else map.set(key, [g]);
    }
    return [...map.entries()]
      .map(([key, list]): GrantGroup => ({
        key,
        projectId: key === "__global__" ? null : key,
        label: projectLabel(key === "__global__" ? null : key, projects),
        isGlobal: key === "__global__",
        grants: list.slice().sort((a, b) => a.scope.localeCompare(b.scope)),
      }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [grants, projects]);

  async function doRevoke(projectId: string, scopes: string[], key: string) {
    setBusyKey(key);
    setError(null);
    try {
      await revokeAgentProjectGrants(projectId, target.canonical_id, scopes);
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to revoke");
    } finally {
      setBusyKey(null);
    }
  }

  return createPortal(
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={`Manage grants for ${agentLabel(target)}`}
        className="relative bg-zinc-900 rounded-md shadow-xl border border-white/10 w-[560px] max-w-full max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between px-4 py-3 border-b border-white/10 shrink-0">
          <h2 className="text-sm font-medium">Active grants for {agentLabel(target)}</h2>
          <button
            type="button"
            aria-label="Close grants"
            onClick={onClose}
            className="p-1 rounded hover:bg-white/10 text-shell-text-secondary"
          >
            <X size={16} />
          </button>
        </header>

        {error && (
          <div
            className="mx-4 mt-3 px-3 py-2 rounded bg-red-500/10 border border-red-500/30 text-red-400 text-xs"
            role="alert"
          >
            {error}
          </div>
        )}

        <div className="flex-1 overflow-y-auto p-4">
          {grants === null && !error ? (
            <p className="text-xs opacity-60 italic">Loading…</p>
          ) : groups.length === 0 ? (
            <p className="text-xs opacity-60 italic">No active grants</p>
          ) : (
            <div className="flex flex-col gap-4">
              {groups.map((group) => (
                <section
                  key={group.key}
                  aria-label={`Grants on ${group.label}`}
                  className="rounded-md border border-white/10 p-3"
                >
                  <div className="flex items-center justify-between mb-2 gap-2">
                    <h3 className="text-xs font-medium uppercase tracking-wide text-shell-text-secondary truncate">
                      {group.label}
                    </h3>
                    {!group.isGlobal && (
                      <button
                        type="button"
                        onClick={() => doRevoke(group.projectId!, [], `all:${group.key}`)}
                        disabled={busyKey !== null}
                        aria-label={`Revoke all on ${group.label}`}
                        className="shrink-0 px-2 py-1 rounded bg-red-500/20 text-red-300 hover:bg-red-500/30 disabled:opacity-50 text-[11px]"
                      >
                        {busyKey === `all:${group.key}` ? "Revoking…" : "Revoke all"}
                      </button>
                    )}
                  </div>

                  {group.isGlobal && (
                    <p className="text-[10px] text-shell-text-tertiary mb-2">
                      Global scopes are not project-bound. Revoke via the registry&apos;s identity Revoke.
                    </p>
                  )}

                  <ul aria-label={`Scopes on ${group.label}`} className="flex flex-wrap gap-1.5">
                    {group.grants.map((g) => {
                      const scopeKey = `scope:${g.scope}:${group.key}`;
                      return (
                        <li
                          key={scopeKey}
                          className="flex items-center gap-1 bg-white/10 rounded px-2 py-0.5 text-[11px]"
                        >
                          <code>{g.scope}</code>
                          {!group.isGlobal && (
                            <button
                              type="button"
                              onClick={() => doRevoke(group.projectId!, [g.scope], scopeKey)}
                              disabled={busyKey !== null}
                              aria-label={`Revoke ${g.scope}`}
                              title={`Revoke ${g.scope} on ${group.label}`}
                              className="opacity-60 hover:opacity-100 hover:text-red-400 disabled:opacity-20 leading-none"
                            >
                              ×
                            </button>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                </section>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}