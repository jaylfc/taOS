import { useEffect, useState } from "react";
import { CheckCircle, XCircle } from "lucide-react";

/**
 * Inline Allow / Deny actions for an external-agent access request.
 *
 * Rendered in the compact consent surfaces — the bell notification and the
 * non-blocking toast (mirroring AgentPausedActions) and reused in the Decisions
 * app. The granted scope set is exactly the requested scopes.
 *
 * When the request includes the `project_tasks` scope, that scope is bound to a
 * single project, so the approver must choose which project it applies to before
 * allowing. The picker lists the operator's projects and can create one inline,
 * so a request that named no project (or the wrong one) can still be assigned the
 * right project at approval time. The chosen project id is passed to the approve
 * endpoint, which mints the token bound to it.
 */
const PROJECT_SCOPES = new Set(["project_tasks", "canvas_read", "canvas_write"]);

interface ProjectOption {
  id: string;
  name: string;
}

function slugify(name: string): string {
  return (
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 63) || "project"
  );
}

export function ConsentActions({
  requestId,
  scopes,
  requestedProjectId,
  onResolved,
}: {
  requestId: string;
  scopes: string[];
  requestedProjectId?: string;
  onResolved?: () => void;
}) {
  const needsProject = scopes.some((s) => PROJECT_SCOPES.has(s));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [projects, setProjects] = useState<ProjectOption[]>([]);
  const [loadingProjects, setLoadingProjects] = useState(false);
  // Start empty on purpose: a requestedProjectId is only adopted after it is
  // verified to exist in the fetched project list (below), so a stale or
  // unowned id from the request can never be sent to approve.
  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");

  useEffect(() => {
    if (!needsProject) return;
    let cancelled = false;
    setLoadingProjects(true);
    fetch("/api/projects?status=active", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : { items: [] }))
      .then((d: { items?: ProjectOption[] }) => {
        if (cancelled) return;
        const items = Array.isArray(d.items) ? d.items : [];
        setProjects(items);
        // Preselect the requested project if it exists, else auto-pick when there
        // is exactly one project. Never overwrite an operator's manual choice.
        setSelectedProjectId((cur) => {
          if (cur) return cur;
          if (requestedProjectId && items.some((p) => p.id === requestedProjectId)) {
            return requestedProjectId;
          }
          return items.length === 1 && items[0] ? items[0].id : "";
        });
      })
      .catch(() => {
        if (!cancelled) setProjects([]);
      })
      .finally(() => {
        if (!cancelled) setLoadingProjects(false);
      });
    return () => {
      cancelled = true;
    };
  }, [needsProject, requestedProjectId]);

  async function createProject(): Promise<string | null> {
    const name = newName.trim();
    if (!name) return null;
    const res = await fetch("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ name, slug: slugify(name) }),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      setError((d as { error?: string }).error ?? `Could not create project (${res.status})`);
      return null;
    }
    const p = (await res.json().catch(() => null)) as Partial<ProjectOption> | null;
    const id = typeof p?.id === "string" ? p.id : "";
    if (!id) {
      setError("Project created but the response was unexpected; reload and pick it from the list.");
      return null;
    }
    const created: ProjectOption = { id, name: typeof p?.name === "string" ? p.name : id };
    setProjects((prev) => [created, ...prev]);
    setSelectedProjectId(id);
    setCreating(false);
    setNewName("");
    return id;
  }

  async function decide(approved: boolean) {
    setBusy(true);
    setError(null);
    let projectId = selectedProjectId;
    // If approving with project_tasks while mid-create, create the project first.
    if (approved && needsProject && creating && newName.trim()) {
      const created = await createProject();
      if (!created) {
        setBusy(false);
        return;
      }
      projectId = created;
    }
    if (approved && needsProject && !projectId) {
      setError("Select or create a project for the requested project access.");
      setBusy(false);
      return;
    }
    const url = approved
      ? `/api/agents/auth-requests/${encodeURIComponent(requestId)}/approve`
      : `/api/agents/auth-requests/${encodeURIComponent(requestId)}/deny`;
    let body: string | undefined;
    if (approved) {
      const payload: { granted_scopes: string[]; project_id?: string } = { granted_scopes: scopes };
      if (needsProject && projectId) payload.project_id = projectId;
      body = JSON.stringify(payload);
    }
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: body ? { "Content-Type": "application/json" } : {},
        body,
        credentials: "include",
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError((data as { detail?: string }).detail ?? `Request failed (${res.status})`);
        setBusy(false);
        return;
      }
      onResolved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Network error");
      setBusy(false);
    }
  }

  const allowDisabled =
    busy || (needsProject && (creating ? !newName.trim() : !selectedProjectId));

  return (
    <div className="mt-2" role="group" aria-label="Consent actions">
      {needsProject && (
        <div className="mb-2">
          <label
            htmlFor={`consent-project-${requestId}`}
            className="block text-[11px] text-shell-text-secondary mb-1"
          >
            Grant project access for
          </label>
          {!creating ? (
            <div className="flex items-center gap-1.5">
              <select
                id={`consent-project-${requestId}`}
                value={selectedProjectId}
                onChange={(e) => {
                  e.stopPropagation();
                  setSelectedProjectId(e.target.value);
                }}
                onClick={(e) => e.stopPropagation()}
                disabled={busy || loadingProjects}
                className="flex-1 min-w-0 px-2 py-1 rounded-md text-[11px] bg-white/5 border border-white/10 text-shell-text disabled:opacity-50"
              >
                <option value="">{loadingProjects ? "Loading..." : "Select a project"}</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setCreating(true);
                }}
                disabled={busy}
                className="px-2 py-1 rounded-md text-[11px] bg-white/5 hover:bg-white/10 border border-white/10 text-shell-text-secondary disabled:opacity-50"
              >
                New
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-1.5">
              <input
                aria-label="New project name"
                value={newName}
                onChange={(e) => {
                  e.stopPropagation();
                  setNewName(e.target.value);
                }}
                onClick={(e) => e.stopPropagation()}
                placeholder="New project name"
                className="flex-1 min-w-0 px-2 py-1 rounded-md text-[11px] bg-white/5 border border-white/10 text-shell-text"
              />
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setCreating(false);
                  setNewName("");
                }}
                disabled={busy}
                className="px-2 py-1 rounded-md text-[11px] bg-white/5 hover:bg-white/10 border border-white/10 text-shell-text-secondary disabled:opacity-50"
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      )}
      <div className="flex flex-wrap gap-1.5">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            decide(true);
          }}
          disabled={allowDisabled}
          className="flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-400 border border-emerald-500/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <CheckCircle size={11} />
          Allow
        </button>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            decide(false);
          }}
          disabled={busy}
          className="flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium bg-white/5 hover:bg-red-500/15 hover:text-red-300 text-shell-text-secondary border border-white/10 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <XCircle size={11} />
          Deny
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-1.5 text-[11px] text-red-300">
          {error}
        </p>
      )}
    </div>
  );
}

/** Pull the consent payload (request id + requested scopes + any requested
 *  project) out of a notification's `data` field. Returns null when the shape
 *  is missing. */
export function consentPayload(
  data: Record<string, unknown> | undefined,
): { requestId: string; scopes: string[]; projectId?: string } | null {
  if (!data) return null;
  const requestId = data.request_id;
  if (typeof requestId !== "string") return null;
  const raw = data.requested_scopes;
  const scopes = Array.isArray(raw) ? raw.filter((s): s is string => typeof s === "string") : [];
  const projectId = typeof data.project_id === "string" ? data.project_id : undefined;
  return { requestId, scopes, projectId };
}
