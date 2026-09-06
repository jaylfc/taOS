import { useEffect, useState } from "react";
import { projectsApi } from "../../../../lib/projects";
import type { ProjectRelationship, TaskContext } from "../../../../lib/projects";

export function Relationships({ projectId, taskId }: { projectId: string; taskId: string }) {
  const [rels, setRels] = useState<ProjectRelationship[]>([]);
  const [context, setContext] = useState<TaskContext | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [from, to] = await Promise.all([
        projectsApi.tasks.listRelationships(projectId, taskId, "from"),
        projectsApi.tasks.listRelationships(projectId, taskId, "to"),
      ]);
      if (!cancelled) setRels([...from, ...to]);
    })();
    return () => { cancelled = true; };
  }, [projectId, taskId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const ctx = await projectsApi.tasks.getContext(taskId);
        if (!cancelled) setContext(ctx);
      } catch {
        // Context is best-effort UI enrichment — a fetch failure just means
        // the breadcrumb/blockers panel stays hidden.
      }
    })();
    return () => { cancelled = true; };
  }, [taskId]);

  const ancestry = context?.ancestry ?? [];
  const blockers = context?.blockers ?? [];
  const isBlocked = context?.is_blocked ?? false;

  if (rels.length === 0 && ancestry.length === 0 && blockers.length === 0) return null;

  return (
    <>
      {ancestry.length > 0 && (
        <section className="board-section" aria-label="Task ancestry">
          <h3>Context</h3>
          <nav aria-label="Goal breadcrumb">
            {[context?.project.name || "Project", ...ancestry.map(a => a.title)].join(" › ")}
          </nav>
        </section>
      )}
      {blockers.length > 0 && (
        <section className="board-section" aria-label="Blockers">
          <h3>Blockers{isBlocked ? " ⛔" : ""}</h3>
          <ul>
            {blockers.map(b => {
              const open = b.status !== "closed" && b.status !== "cancelled";
              return (
                <li key={b.id} aria-label={open ? `${b.title} — blocking` : `${b.title} — resolved`}>
                  {open ? "⛔ " : "✅ "}
                  {b.title} <span>({b.status})</span>
                </li>
              );
            })}
          </ul>
        </section>
      )}
      {rels.length > 0 && (
        <section className="board-section">
          <h3>Relationships</h3>
          <ul>
            {rels.map(r => (
              <li key={r.id}>
                <b>{r.kind}</b>{" "}
                {r.from_task_id === taskId ? "→" : "←"}{" "}
                {r.from_task_id === taskId ? r.to_task_id : r.from_task_id}
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
