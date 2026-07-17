import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { projectsApi, type Project } from "@/lib/projects";
import { RegistryEntry } from "./RegistryPanel";

const SCOPE_PRESETS: { value: string; label: string; defaultOn: boolean; disabled?: boolean; hint?: string }[] = [
  { value: "project_tasks", label: "project_tasks", defaultOn: true, disabled: true, hint: "required" },
  { value: "canvas_read", label: "canvas_read", defaultOn: true },
  { value: "canvas_write", label: "canvas_write", defaultOn: true },
];

export function AssignAgentToProjectDialog({
  entry,
  onClose,
  onAssigned,
}: {
  entry: Pick<RegistryEntry, "canonical_id" | "handle" | "status">;
  onClose: () => void;
  onAssigned?: () => void;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loadingProjects, setLoadingProjects] = useState(true);
  const [projectErr, setProjectErr] = useState<string | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [scopes, setScopes] = useState<Record<string, boolean>>(() => {
    const init: Record<string, boolean> = {};
    for (const s of SCOPE_PRESETS) init[s.value] = s.defaultOn;
    return init;
  });
  const [isLead, setIsLead] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoadingProjects(true);
    setProjectErr(null);
    projectsApi
      .list()
      .then((rows) => {
        if (!active) return;
        const list: Project[] = Array.isArray(rows) ? rows : [];
        setProjects(list);
        if (list.length > 0) setSelectedProjectId((prev) => prev || list[0]!.id);
      })
      .catch((e: unknown) => {
        if (active) setProjectErr(e instanceof Error ? e.message : "Failed to load projects");
      })
      .finally(() => {
        if (active) setLoadingProjects(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const buildScopes = (): string[] => {
    const out = new Set<string>();
    // project_tasks is always granted regardless of its (disabled) checkbox state.
    out.add("project_tasks");
    for (const s of SCOPE_PRESETS) {
      if (s.disabled) continue;
      if (scopes[s.value]) out.add(s.value);
    }
    return [...out];
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting || !selectedProjectId) return;
    setSubmitting(true);
    setError(null);
    try {
      const body: {
        canonical_id: string;
        scopes: string[];
        is_lead?: boolean;
      } = {
        canonical_id: entry.canonical_id,
        scopes: buildScopes(),
      };
      if (isLead) body.is_lead = true;
      const r = await fetch(`/api/projects/${selectedProjectId}/members/assign-agent`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error((err as { error?: string }).error ?? `HTTP ${r.status}`);
      }
      const project = projects.find((p) => p.id === selectedProjectId);
      const label = project?.name || project?.slug || selectedProjectId;
      setConfirmation(`Assigned ${entry.handle} to ${label}`);
      onAssigned?.();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Assign agent to project"
      className="fixed inset-0 bg-black/50 flex items-center justify-center p-4"
    >
      <form
        onSubmit={onSubmit}
        className="bg-zinc-900 p-4 rounded shadow w-full max-w-md space-y-3"
      >
        <h3 className="text-lg font-semibold">Assign {entry.handle} to project</h3>

        {confirmation ? (
          <section className="space-y-3" aria-label="Assignment result">
            <p className="text-sm text-emerald-300">{confirmation}</p>
            <div className="flex justify-end">
              <button
                type="button"
                onClick={onClose}
                className="px-3 py-1 bg-zinc-800 rounded hover:bg-zinc-700 text-sm"
              >
                Done
              </button>
            </div>
          </section>
        ) : (
          <>
            <label className="block text-sm">
              <span className="text-zinc-400">Target project</span>
              {loadingProjects ? (
                <div className="text-xs text-zinc-500 mt-1">Loading projects…</div>
              ) : projectErr ? (
                <p role="alert" className="text-red-400 text-xs mt-1">{projectErr}</p>
              ) : (
                <select
                  value={selectedProjectId}
                  onChange={(e) => setSelectedProjectId(e.target.value)}
                  className="w-full mt-1 px-2 py-1 bg-zinc-800 rounded"
                  aria-label="Target project"
                >
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name || p.slug}
                    </option>
                  ))}
                </select>
              )}
            </label>

            <fieldset className="border border-zinc-800 p-2 rounded">
              <legend className="text-xs px-1">Scopes</legend>
              {SCOPE_PRESETS.map((s) => (
                <label key={s.value} className="flex items-center gap-2 text-sm py-0.5">
                  <input
                    type="checkbox"
                    checked={scopes[s.value]}
                    disabled={s.disabled}
                    onChange={(e) => setScopes((prev) => ({ ...prev, [s.value]: e.target.checked }))}
                    aria-label={`Scope ${s.label}`}
                  />
                  <span>{s.label}</span>
                  {s.disabled && s.hint && (
                    <span className="text-[10px] text-zinc-500">({s.hint})</span>
                  )}
                </label>
              ))}
              <label className="flex items-center gap-2 text-sm py-0.5 mt-1">
                <input
                  type="checkbox"
                  checked={isLead}
                  onChange={(e) => setIsLead(e.target.checked)}
                  aria-label="Make this agent the project lead"
                />
                <span>Lead</span>
              </label>
            </fieldset>

            {error && <div role="alert" className="text-red-400 text-xs">{error}</div>}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
                disabled={submitting}
                className="px-3 py-1 text-sm disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting || !selectedProjectId || loadingProjects}
                className="px-3 py-1 bg-blue-600 rounded text-sm disabled:opacity-50"
              >
                {submitting ? "Assigning…" : "Assign to project"}
              </button>
            </div>
          </>
        )}
      </form>
    </div>,
    document.body,
  );
}
