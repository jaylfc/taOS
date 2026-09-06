import type { Task, ViewMode, GroupBy, TaskStatus } from "./types";

export type ApiCall =
  | { kind: "claim"; taskId: string; claimerId: string }
  | { kind: "release"; taskId: string; releaserId: string }
  | { kind: "close"; taskId: string; closedBy: string }
  | { kind: "update"; taskId: string; patch: Partial<Task> };

export type DndResult = { calls: ApiCall[] } | { blocked: string };

export interface DndInput {
  task: Task;
  target: {
    columnStatus: "ready" | "claimed" | "closed";
    laneKey?: string;
    groupBy?: GroupBy;
  };
  viewMode: ViewMode;
  currentUserId: string;
}

export function dndAction(input: DndInput): DndResult {
  const { task, target, viewMode, currentUserId } = input;
  const calls: ApiCall[] = [];

  // 1) Lane change first (Lanes mode only)
  if (viewMode === "lanes" && target.laneKey && target.groupBy) {
    const patch = lanePatch(task, target.laneKey, target.groupBy);
    if (patch) calls.push({ kind: "update", taskId: task.id, patch });
  }

  // 2) Column status change
  const fromStatus = mapStatus(task.status);
  if (fromStatus !== target.columnStatus) {
    if (fromStatus === "closed") {
      return { blocked: "Re-open by creating a follow-up task" };
    }
    if (target.columnStatus === "claimed") {
      calls.push({ kind: "claim", taskId: task.id, claimerId: currentUserId });
    } else if (target.columnStatus === "ready") {
      calls.push({ kind: "release", taskId: task.id, releaserId: currentUserId });
    } else if (target.columnStatus === "closed") {
      calls.push({ kind: "close", taskId: task.id, closedBy: currentUserId });
    }
  }

  return { calls };
}

function mapStatus(s: TaskStatus): "ready" | "claimed" | "closed" {
  return s === "open" ? "ready" : s === "claimed" ? "claimed" : "closed";
}

function sameMembers(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const sa = new Set(a);
  return b.every(x => sa.has(x));
}

function lanePatch(task: Task, laneKey: string, groupBy: GroupBy): Partial<Task> | null {
  switch (groupBy) {
    case "assignee":
      if (laneKey === "__unassigned__") return { assignee_id: null };
      if (task.assignee_id === laneKey) return null;
      return { assignee_id: laneKey };
    case "parent":
      if (laneKey === "__orphans__") return { parent_task_id: null };
      if (task.parent_task_id === laneKey) return null;
      return { parent_task_id: laneKey };
    case "priority": {
      const p = laneKey === "p0" ? 0 : laneKey === "p1" ? 1 : laneKey === "p2" ? 2 : 3;
      if (task.priority === p) return null;
      return { priority: p };
    }
    case "label": {
      // Replace category labels with target; preserve cover:* markers.
      // __unlabeled__ removes all category labels but keeps cover:* labels.
      const covers = task.labels.filter(l => l.startsWith("cover:"));
      const targetLbl = laneKey === "__unlabeled__" ? null : laneKey;
      const next = targetLbl ? [...covers, targetLbl] : covers;
      if (sameMembers(task.labels, next)) return null;
      return { labels: next };
    }
  }
}
