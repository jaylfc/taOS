import type { Task, Lane } from "./types";

export function groupByAssignee(tasks: Task[]): Lane[] {
  const buckets = new Map<string, Task[]>();
  for (const t of tasks) {
    const k = t.assignee_id ?? "__unassigned__";
    if (!buckets.has(k)) buckets.set(k, []);
    buckets.get(k)!.push(t);
  }
  return [...buckets.entries()].map(([key, cards]) => ({
    header: {
      key,
      kind: "assignee",
      title: key === "__unassigned__" ? "Unassigned" : key,
      subtitle: `${cards.length} task${cards.length === 1 ? "" : "s"}`,
      avatarSeed: key,
    },
    cards,
  }));
}

export function groupByParent(tasks: Task[]): Lane[] {
  const byId = new Map(tasks.map(t => [t.id, t]));
  const buckets = new Map<string, Task[]>();
  for (const t of tasks) {
    const parent = t.parent_task_id;
    if (parent && byId.has(parent)) {
      if (!buckets.has(parent)) buckets.set(parent, []);
      buckets.get(parent)!.push(t);
    }
  }
  const lanes: Lane[] = [];
  for (const [parentId, cards] of buckets) {
    const parent = byId.get(parentId)!;
    lanes.push({
      header: {
        key: parentId,
        kind: "parent",
        title: parent.title,
        subtitle: `${cards.length} sub-task${cards.length === 1 ? "" : "s"}`,
      },
      cards,
    });
  }
  // Orphan lane: tasks with no parent and no children
  const childIds = new Set([...buckets.values()].flat().map(t => t.id));
  const parentIds = new Set(buckets.keys());
  const orphans = tasks.filter(t => !childIds.has(t.id) && !parentIds.has(t.id));
  if (orphans.length > 0) {
    lanes.push({
      header: { key: "__orphans__", kind: "parent", title: "Standalone", subtitle: `${orphans.length}` },
      cards: orphans,
    });
  }
  return lanes;
}

export function groupByLabel(tasks: Task[]): Lane[] {
  const buckets = new Map<string, Task[]>();
  for (const t of tasks) {
    if (t.labels.length === 0) {
      const k = "__unlabeled__";
      if (!buckets.has(k)) buckets.set(k, []);
      buckets.get(k)!.push(t);
    } else {
      for (const lbl of t.labels) {
        if (!buckets.has(lbl)) buckets.set(lbl, []);
        buckets.get(lbl)!.push(t);
      }
    }
  }
  return [...buckets.entries()].map(([key, cards]) => ({
    header: {
      key,
      kind: "label",
      title: key === "__unlabeled__" ? "Unlabeled" : key,
      subtitle: `${cards.length}`,
    },
    cards,
  }));
}

export function groupByPriority(tasks: Task[]): Lane[] {
  const buckets = new Map<string, { title: string; cards: Task[] }>([
    ["p0", { title: "P0 — urgent", cards: [] }],
    ["p1", { title: "P1 — high", cards: [] }],
    ["p2", { title: "P2 — normal", cards: [] }],
    ["backlog", { title: "Backlog", cards: [] }],
  ]);
  for (const t of tasks) {
    const k = t.priority === 0 ? "p0" : t.priority === 1 ? "p1" : t.priority === 2 ? "p2" : "backlog";
    buckets.get(k)!.cards.push(t);
  }
  const lanes: Lane[] = [];
  for (const [k, { title, cards }] of buckets) {
    if (cards.length > 0) {
      lanes.push({
        header: { key: k, kind: "priority", title, subtitle: `${cards.length}` },
        cards,
      });
    }
  }
  return lanes;
}
