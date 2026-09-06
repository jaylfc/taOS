import { describe, it, expect } from "vitest";
import {
  groupByAssignee, groupByParent, groupByLabel, groupByPriority,
} from "../boardGrouping";
import type { Task } from "../types";

const t = (over: Partial<Task>): Task => ({
  id: "t1", project_id: "p1", parent_task_id: null, title: "T", body: "",
  status: "open", priority: 2, labels: [], assignee_id: null,
  claimed_by: null, claimed_at: null, closed_at: null, closed_by: null,
  element_id: null,
  created_by: "u", created_at: "2026-01-01", updated_at: "2026-01-01",
  ...over,
});

describe("groupByAssignee", () => {
  it("groups by assignee_id, with unassigned bucket", () => {
    const lanes = groupByAssignee([
      t({ id: "a", assignee_id: "alice" }),
      t({ id: "b", assignee_id: "bob" }),
      t({ id: "c", assignee_id: null }),
      t({ id: "d", assignee_id: "alice" }),
    ]);
    const keys = lanes.map(l => l.header.key);
    expect(keys).toContain("alice");
    expect(keys).toContain("bob");
    expect(keys).toContain("__unassigned__");
    const alice = lanes.find(l => l.header.key === "alice")!;
    expect(alice.cards.map(c => c.id).sort()).toEqual(["a", "d"]);
  });

  it("returns empty when no tasks", () => {
    expect(groupByAssignee([])).toEqual([]);
  });
});

describe("groupByParent", () => {
  it("buckets children under their parent and orphans separately", () => {
    const lanes = groupByParent([
      t({ id: "p1", parent_task_id: null }),
      t({ id: "c1", parent_task_id: "p1" }),
      t({ id: "c2", parent_task_id: "p1" }),
      t({ id: "o1", parent_task_id: null }),
    ]);
    const p = lanes.find(l => l.header.key === "p1")!;
    expect(p.cards.map(c => c.id).sort()).toEqual(["c1", "c2"]);
    const orphans = lanes.find(l => l.header.key === "__orphans__")!;
    expect(orphans.cards.map(c => c.id)).toEqual(["o1"]);
  });
});

describe("groupByLabel", () => {
  it("renders multi-label tasks in each matching lane", () => {
    const lanes = groupByLabel([
      t({ id: "a", labels: ["bug", "auth"] }),
      t({ id: "b", labels: ["bug"] }),
      t({ id: "c", labels: [] }),
    ]);
    const bug = lanes.find(l => l.header.key === "bug")!;
    expect(bug.cards.map(c => c.id).sort()).toEqual(["a", "b"]);
    const auth = lanes.find(l => l.header.key === "auth")!;
    expect(auth.cards.map(c => c.id)).toEqual(["a"]);
    const unlabeled = lanes.find(l => l.header.key === "__unlabeled__")!;
    expect(unlabeled.cards.map(c => c.id)).toEqual(["c"]);
  });
});

describe("groupByPriority", () => {
  it("buckets into P0/P1/P2/backlog", () => {
    const lanes = groupByPriority([
      t({ id: "a", priority: 0 }),
      t({ id: "b", priority: 1 }),
      t({ id: "c", priority: 2 }),
      t({ id: "d", priority: 5 }),
    ]);
    expect(lanes.find(l => l.header.key === "p0")!.cards[0].id).toBe("a");
    expect(lanes.find(l => l.header.key === "p1")!.cards[0].id).toBe("b");
    expect(lanes.find(l => l.header.key === "p2")!.cards[0].id).toBe("c");
    expect(lanes.find(l => l.header.key === "backlog")!.cards[0].id).toBe("d");
  });
});
