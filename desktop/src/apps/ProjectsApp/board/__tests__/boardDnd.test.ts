import { describe, it, expect } from "vitest";
import { dndAction } from "../boardDnd";
import type { Task } from "../types";

const t = (over: Partial<Task>): Task => ({
  id: "t1", project_id: "p1", parent_task_id: null, title: "T", body: "",
  status: "open", priority: 2, labels: [], assignee_id: null,
  claimed_by: null, claimed_at: null, closed_at: null, closed_by: null,
  element_id: null,
  created_by: "u", created_at: "2026-01-01", updated_at: "2026-01-01",
  ...over,
});

describe("dndAction — kanban view", () => {
  it("ready→claimed produces a claim call", () => {
    const r = dndAction({
      task: t({ status: "open" }),
      target: { columnStatus: "claimed" },
      viewMode: "kanban",
      currentUserId: "u1",
    });
    expect(r).toEqual({ calls: [{ kind: "claim", taskId: "t1", claimerId: "u1" }] });
  });

  it("claimed→ready produces a release call", () => {
    const r = dndAction({
      task: t({ status: "claimed", claimed_by: "u1" }),
      target: { columnStatus: "ready" },
      viewMode: "kanban",
      currentUserId: "u1",
    });
    expect(r).toEqual({ calls: [{ kind: "release", taskId: "t1", releaserId: "u1" }] });
  });

  it("any→closed produces a close call", () => {
    expect(dndAction({
      task: t({ status: "claimed", claimed_by: "u1" }),
      target: { columnStatus: "closed" },
      viewMode: "kanban",
      currentUserId: "u1",
    })).toEqual({ calls: [{ kind: "close", taskId: "t1", closedBy: "u1" }] });
  });

  it("closed→ready is blocked", () => {
    expect(dndAction({
      task: t({ status: "closed" }),
      target: { columnStatus: "ready" },
      viewMode: "kanban",
      currentUserId: "u1",
    })).toEqual({ blocked: "Re-open by creating a follow-up task" });
  });
});

describe("dndAction — lanes view", () => {
  it("change lane (assignee) emits PATCH then column action", () => {
    expect(dndAction({
      task: t({ status: "open", assignee_id: "alice" }),
      target: { columnStatus: "claimed", laneKey: "bob", groupBy: "assignee" },
      viewMode: "lanes",
      currentUserId: "u1",
    })).toEqual({
      calls: [
        { kind: "update", taskId: "t1", patch: { assignee_id: "bob" } },
        { kind: "claim", taskId: "t1", claimerId: "u1" },
      ],
    });
  });

  it("change lane (priority) emits PATCH on priority", () => {
    expect(dndAction({
      task: t({ status: "open", priority: 2 }),
      target: { columnStatus: "ready", laneKey: "p0", groupBy: "priority" },
      viewMode: "lanes",
      currentUserId: "u1",
    })).toEqual({
      calls: [{ kind: "update", taskId: "t1", patch: { priority: 0 } }],
    });
  });
});
