import { useCallback, useEffect, useState } from "react";
import { projectsApi } from "../../../lib/projects";
import type { ProjectElement } from "../../../lib/projects";
import type { Task } from "./types";
import type { BoardLiveEvent } from "./useBoardLive";

export function useBoardData(projectId: string) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [elements, setElements] = useState<ProjectElement[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    (async () => {
      const [open, claimed, closed, els] = await Promise.all([
        projectsApi.tasks.list(projectId, "open"),
        projectsApi.tasks.list(projectId, "claimed"),
        projectsApi.tasks.list(projectId, "closed"),
        projectsApi.elements.list(projectId).catch(() => [] as ProjectElement[]),
      ]);
      if (!cancelled) {
        const seen = new Set<string>();
        const all = [...open, ...claimed, ...closed].filter(t => {
          if (seen.has(t.id)) return false;
          seen.add(t.id);
          return true;
        });
        setTasks(all as unknown as Task[]);
        setElements(els);
        setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [projectId]);

  const applyEvent = useCallback((e: BoardLiveEvent) => {
    setTasks(prev => {
      const p = e.payload as Record<string, any>;
      switch (e.kind) {
        case "task.created":
          if (prev.find(t => t.id === p.id)) return prev;
          return [...prev, p.task as Task];
        case "task.updated":
          return prev.map(t => t.id === p.id ? { ...t, ...(p.patch as Partial<Task>) } : t);
        case "task.claimed":
          return prev.map(t => t.id === p.id ? { ...t, status: "claimed", claimed_by: p.claimed_by ?? null } : t);
        case "task.released":
          return prev.map(t => t.id === p.id ? { ...t, status: "open", claimed_by: null } : t);
        case "task.closed":
          return prev.map(t => t.id === p.id ? { ...t, status: "closed", closed_by: p.closed_by ?? null } : t);
        case "task.deleted":
          return prev.filter(t => t.id !== p.id);
        default:
          return prev;
      }
    });
  }, []);

  return { tasks, elements, loading, setTasks, applyEvent };
}
