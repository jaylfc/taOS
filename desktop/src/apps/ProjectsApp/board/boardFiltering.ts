import type { Task, Filters } from "./types";

export function applyFilters(tasks: Task[], f: Filters): Task[] {
  const search = f.search.trim().toLowerCase();
  return tasks.filter(t => {
    if (f.assignees.length && !f.assignees.includes(t.assignee_id ?? "")) return false;
    if (f.labels.length && !f.labels.some(l => t.labels.includes(l))) return false;
    if (f.priorities.length && !f.priorities.includes(t.priority)) return false;
    if (f.parentTaskId && t.parent_task_id !== f.parentTaskId) return false;
    if (f.elementId === "none" && t.element_id != null) return false;
    if (
      typeof f.elementId === "string" &&
      f.elementId !== "none" &&
      t.element_id !== f.elementId
    ) {
      return false;
    }
    if (f.hideClosed && t.status === "closed") return false;
    if (search) {
      const hay = `${t.title} ${t.body} ${t.id}`.toLowerCase();
      if (!hay.includes(search)) return false;
    }
    // hasAttachments: not modeled yet — pass-through. When attachments land, check t.attachments.length > 0.
    return true;
  });
}
