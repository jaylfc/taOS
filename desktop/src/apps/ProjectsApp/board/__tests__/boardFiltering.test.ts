import { describe, it, expect } from "vitest";
import { applyFilters } from "../boardFiltering";
import { EMPTY_FILTERS } from "../types";
import type { Task } from "../types";

const t = (over: Partial<Task>): Task => ({
  id: "t1", project_id: "p1", parent_task_id: null, title: "T", body: "",
  status: "open", priority: 2, labels: [], assignee_id: null,
  claimed_by: null, claimed_at: null, closed_at: null, closed_by: null,
  element_id: null,
  created_by: "u", created_at: "2026-01-01", updated_at: "2026-01-01",
  ...over,
});

describe("applyFilters", () => {
  const tasks = [
    t({ id: "a", assignee_id: "alice", labels: ["bug"], priority: 0, title: "Auth break" }),
    t({ id: "b", assignee_id: "bob", labels: ["feature"], priority: 1, title: "New feature" }),
    t({ id: "c", assignee_id: "alice", labels: [], priority: 2, status: "closed", title: "Old work" }),
  ];

  it("returns all tasks with empty filters", () => {
    expect(applyFilters(tasks, EMPTY_FILTERS).map(t => t.id).sort()).toEqual(["a", "b", "c"]);
  });

  it("filters by assignee", () => {
    expect(applyFilters(tasks, { ...EMPTY_FILTERS, assignees: ["alice"] }).map(t => t.id).sort())
      .toEqual(["a", "c"]);
  });

  it("filters by label", () => {
    expect(applyFilters(tasks, { ...EMPTY_FILTERS, labels: ["bug"] }).map(t => t.id))
      .toEqual(["a"]);
  });

  it("filters by priority", () => {
    expect(applyFilters(tasks, { ...EMPTY_FILTERS, priorities: [0, 1] }).map(t => t.id).sort())
      .toEqual(["a", "b"]);
  });

  it("hideClosed removes closed tasks", () => {
    expect(applyFilters(tasks, { ...EMPTY_FILTERS, hideClosed: true }).map(t => t.id).sort())
      .toEqual(["a", "b"]);
  });

  it("search matches title (case-insensitive)", () => {
    expect(applyFilters(tasks, { ...EMPTY_FILTERS, search: "auth" }).map(t => t.id))
      .toEqual(["a"]);
  });

  it("AND-combines filters", () => {
    const result = applyFilters(tasks, { ...EMPTY_FILTERS, assignees: ["alice"], labels: ["bug"] });
    expect(result.map(t => t.id)).toEqual(["a"]);
  });

  it("returns all tasks with elementId null", () => {
    const elTasks = [
      t({ id: "e", element_id: "el1" }),
      t({ id: "f", element_id: null }),
      t({ id: "g", element_id: "el2" }),
    ];
    expect(applyFilters(elTasks, EMPTY_FILTERS).map(t => t.id).sort()).toEqual(["e", "f", "g"]);
  });

  it("scopes to a single element", () => {
    const elTasks = [
      t({ id: "e", element_id: "el1" }),
      t({ id: "f", element_id: null }),
      t({ id: "g", element_id: "el2" }),
    ];
    expect(applyFilters(elTasks, { ...EMPTY_FILTERS, elementId: "el1" }).map(t => t.id)).toEqual(["e"]);
  });

  it("'none' returns only untagged (project-level) tasks", () => {
    const elTasks = [
      t({ id: "e", element_id: "el1" }),
      t({ id: "f", element_id: null }),
      t({ id: "g", element_id: "el2" }),
    ];
    expect(applyFilters(elTasks, { ...EMPTY_FILTERS, elementId: "none" }).map(t => t.id)).toEqual(["f"]);
  });
});
