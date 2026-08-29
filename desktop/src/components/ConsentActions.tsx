import { useEffect, useState } from "react";
import { CheckCircle, XCircle } from "lucide-react";
import { withCsrf } from "@/lib/csrf";

/**
 * Inline Allow / Deny actions for an external-agent access request.
 *
 * Rendered in the compact consent surfaces -- the bell notification and the
 * non-blocking toast (mirroring AgentPausedActions) and reused in the Decisions
 * app.
 *
 * What is being consented to must be legible before the approver clicks. Two
 * things historically went unchecked in this surface:
 *
 *  - The requested PROJECT. The request payload may name a project that is
 *    not visible to the approver (nonexistent on this install, or owned
 *    elsewhere). The picker never adopted a requested id it could not resolve
 *    in the operator's project list, which is correct -- but it also rendered a
 *    BLANK dropdown with no hint of what was asked, training the approver to
 *    click through an approval they could not see. The requested project is now
 *    preselected AND labelled by name when it resolves, and an explicit
 *    not-found message is shown (in red) when it does not, so the failure is
 *    legible instead of silent.
 *
 *  - The SCOPES. The granted set is always a subset of the requested set (the
 *    backend enforces narrow-not-widen); this surface now renders both the
 *    Requested and the Granted lists and highlights any scope that is dropped
 *    from -- or added beyond -- the request, so a narrowing or a widening can
 *    never happen silently.
 *
 * When the request includes a project scope, that scope is bound to a single
 * project, so the approver must choose which project it applies to before
 * allowing. The picker lists the operator's projects and can create one inline,
 * so a request that named no project (or the wrong one) can still be assigned
 * the right project at approval time. The chosen project id is passed to the
 * approve endpoint, which mints the token bound to it.
 */
const PROJECT_SCOPES = new Set([
  "project_tasks",
  "canvas_read",
  "canvas_write",
  "project_tasks_create",
  "project_tasks_update",
  "project_lists",
  "project_notes",
  "files_read",
  "files_write",
]);

interface ProjectOption {
  id: string;
  name: string;
}

/** Difference between the requested scope set and the granted scope set.
 * `dropped` = requested but not granted (narrowing); `added` = granted but not
 * requested (widening). Exported so the diff logic is unit-testable in isolation.
 */
export function computeScopeDiff(
  requested: readonly string[],
  granted: readonly string[],
) {
  const r = new Set(requested);
  const g = new Set(granted);
  return {
    dropped: requested.filter((s) => !g.has(s)),
    added: granted.filter((s) => !r.has(s)),
  };
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

function ScopeBadge({
  scope,
  dropped,
  added,
}: {
  scope: string;
  dropped?: boolean;
  added?: boolean;
}) {
  if (dropped) {
    return (
      <span
        className="inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-mono text-red-200 bg-red-500/15 border border-red-500/30"
        aria-label={`${scope} (dropped from request)`}
        data-state="dropped"
      >
        {scope}
      </span>
    );
  }
  if (added) {
    return (
      <span
        className="inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-mono text-amber-200 bg-amber-500/15 border border-amber-500/30"
        aria-label={`${scope} (granted beyond request)`}
        data-state="added"
      >
        {scope}
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center rounded px-2 py-0.5 text-[11px] font-mono text-shell-text-secondary bg-shell-surface border border-white/10"
      aria-label={scope}
      data-state="normal"
    >
      {scope}
    </span>
  );
}

export function ConsentActions({
  requestId,
  scopes,
  grantedScopes,
  requestedProjectId,
  onResolved,
  source = "auth_requests",
  canonicalId,
}: {
  requestId: string;
  scopes: string[];
  /** Scopes actually being granted. Defaults to the full requested set; pass a
   *  narrower set to approve with fewer scopes (the backend allows
   *  narrow-not-widen) and the diff against `scopes` is highlighted inline. */
  grantedScopes?: string[];
  requestedProjectId?: string;
  onResolved?: () => void;
  source?: string;
  canonicalId?: string;
}) {
  // `granted` is what the approve call will actually send. It defaults to the
  // full requested set, so the out-of-the-box behaviour (grant exactly what was
  // asked) is unchanged; the prop exists so the Requested vs Granted contrast
  // is always explicit and a narrowing can never be silent.
  const granted = grantedScopes ?? scopes;
  const needsProject = granted.some((s) => PROJECT_SCOPES.has(s));
  const { dropped, added } = computeScopeDiff(scopes, granted);
  const hasScopeDiff = dropped.length > 0 || added.length > 0;

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [projects, setProjects] = useState<ProjectOption[]>([]);
  const [loadingProjects, setLoadingProjects] = useState(false);
  // Start empty on purpose: a requestedProjectId is only adopted after it is
  // verified to exist in the fetched project list (below), so a stale or
  // unowned id from the request can never be sent to approve. What changes here
  // is that a requested id that does NOT resolve is now reported explicitly
  // instead of leaving a silent blank dropdown.
  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const [resolvedRequestedProject, setResolvedRequestedProject] =
    useState<ProjectOption | null>(null);
  const [requestedProjectNotFound, setRequestedProjectNotFound] =
    useState<boolean>(false);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");

  useEffect(() => {
    if (!needsProject) return;
    let cancelled = false;
    setLoadingProjects(true);
    setRequestedProjectNotFound(false);
    setResolvedRequestedProject(null);
    fetch("/api/projects?status=active", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : { items: [] }))
      .then((d: { items?: ProjectOption[] }) => {
        if (cancelled) return;
        const items = Array.isArray(d.items) ? d.items : [];
        setProjects(items);
        // Preselect the requested project if it exists in the operator's
        // visible set -- and label it -- else leave the picker blank with an
        // explicit not-found message so the approver is never guessing.
        if (requestedProjectId) {
          const match = items.find((p) => p.id === requestedProjectId);
          if (match) {
            setResolvedRequestedProject(match);
            setRequestedProjectNotFound(false);
            setSelectedProjectId((cur) => cur || match.id);
          } else {
            setResolvedRequestedProject(null);
            setRequestedProjectNotFound(true);
            // Do not adopt the requested id; leave the picker blank so the
            // operator must actively choose a project they can see.
            setSelectedProjectId((cur) => cur);
          }
        } else {
          setResolvedRequestedProject(null);
          setRequestedProjectNotFound(false);
          setSelectedProjectId((cur) => {
            if (cur) return cur;
            return items.length === 1 && items[0] ? items[0].id : "";
          });
        }
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
    const res = await fetch(
      "/api/projects",
      withCsrf({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ name, slug: slugify(name) }),
      }),
    );
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      setError(
        (d as { error?: string }).error ??
          `Could not create project (${res.status})`,
      );
      return null;
    }
    const p = (await res.json().catch(() => null)) as
      | Partial<ProjectOption>
      | null;
    const id = typeof p?.id === "string" ? p.id : "";
    if (!id) {
      setError(
        "Project created but the response was unexpected; reload and pick it from the list.",
      );
      return null;
    }
    const created: ProjectOption = {
      id,
      name: typeof p?.name === "string" ? p.name : id,
    };
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
    // If approving with a project scope while mid-create, create the project first.
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
    if (source === "agent_scope_requests" && !canonicalId) {
      setError("Missing agent identifier; cannot process this scope request.");
      setBusy(false);
      return;
    }
    const baseUrl =
      source === "agent_scope_requests"
        ? `/api/agents/registry/${encodeURIComponent(canonicalId!)}/scope-requests/${encodeURIComponent(requestId)}`
        : `/api/agents/auth-requests/${encodeURIComponent(requestId)}`;
    const url = `${baseUrl}/${approved ? "approve" : "deny"}`;
    let body: string | undefined;
    if (approved) {
      const payload: { granted_scopes: string[]; project_id?: string } = {
        granted_scopes: granted,
      };
      if (needsProject && projectId) payload.project_id = projectId;
      body = JSON.stringify(payload);
    }
    try {
      const res = await fetch(
        url,
        withCsrf({
          method: "POST",
          headers: body ? { "Content-Type": "application/json" } : {},
          body,
          credentials: "include",
        }),
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(
          (data as { detail?: string }).detail ??
            `Request failed (${res.status})`,
        );
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
    busy ||
    (needsProject &&
      (creating ? !newName.trim() : !selectedProjectId));

  return (
    <div className="mt-2" role="group" aria-label="Consent actions">
      {scopes.length > 0 && (
        <div className="mb-2">
          <div className="flex flex-wrap items-baseline gap-1">
            <span className="font-medium text-[11px] uppercase tracking-wide text-shell-text-tertiary">
              Requested
            </span>
            {scopes.map((s) => (
              <ScopeBadge
                key={`req-${s}`}
                scope={s}
                dropped={dropped.includes(s)}
              />
            ))}
          </div>
          <div className="flex flex-wrap items-baseline gap-1 mt-0.5">
            <span className="font-medium text-[11px] uppercase tracking-wide text-shell-text-tertiary">
              Granted
            </span>
            {granted.map((s) => (
              <ScopeBadge
                key={`grant-${s}`}
                scope={s}
                added={added.includes(s)}
              />
            ))}
          </div>
          {hasScopeDiff && (
            <p
              role="alert"
              className="mt-1 text-[11px] text-red-300"
            >
              {dropped.length > 0 && (
                <span>
                  Dropping {dropped.length} requested scope(s):{" "}
                  {dropped.join(", ")}.
                </span>
              )}
              {added.length > 0 && (
                <span>
                  {" "}
                  Granting {added.length} scope(s) beyond the request:{" "}
                  {added.join(", ")}.
                </span>
              )}
            </p>
          )}
        </div>
      )}
      {needsProject && (
        <div className="mb-2">
          {requestedProjectId && requestedProjectNotFound && (
            <p
              role="alert"
              className="mb-1.5 text-[11px] text-red-300"
            >
              Requested project {requestedProjectId} not found (not visible to
              you). Pick a project you can see below, or create one.
            </p>
          )}
          {requestedProjectId && resolvedRequestedProject && (
            <p className="mb-1 text-[11px] text-shell-text-secondary">
              Requesting access for{" "}
              <span className="font-medium text-shell-text">
                {resolvedRequestedProject.name}
              </span>
              {" "}
              (<code className="text-shell-text-tertiary">
                {resolvedRequestedProject.id}
              </code>)
            </p>
          )}
          {!creating ? (
            <div className="flex items-center gap-1.5">
              <select
                id={`consent-project-${requestId}`}
                aria-label="Grant project access for"
                value={selectedProjectId}
                onChange={(e) => {
                  e.stopPropagation();
                  setSelectedProjectId(e.target.value);
                  setRequestedProjectNotFound(false);
                }}
                onClick={(e) => e.stopPropagation()}
                disabled={busy || loadingProjects}
                className="flex-1 min-w-0 px-2 py-1 rounded-md text-[11px] bg-white/5 border border-white/10 text-shell-text disabled:opacity-50"
                aria-invalid={requestedProjectNotFound ? "true" : undefined}
              >
                <option value="">
                  {loadingProjects ? "Loading..." : "Select a project"}
                </option>
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
                  setRequestedProjectNotFound(false);
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
 * project) out of a notification's `data` field. Returns null when the shape
 * is missing. */
export function consentPayload(
  data: Record<string, unknown> | undefined,
): { requestId: string; scopes: string[]; projectId?: string; canonicalId?: string } | null {
  if (!data) return null;
  const requestId = data.request_id;
  if (typeof requestId !== "string") return null;
  const raw = data.requested_scopes;
  const scopes = Array.isArray(raw) ? raw.filter((s): s is string => typeof s === "string") : [];
  const projectId = typeof data.project_id === "string" ? data.project_id : undefined;
  const canonicalId = typeof data.canonical_id === "string" ? data.canonical_id : undefined;
  return { requestId, scopes, projectId, canonicalId };
}
