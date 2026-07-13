import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useBoardData } from "../useBoardData";
import { projectsApi } from "../../../../lib/projects";

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(projectsApi.elements, "list").mockResolvedValue([]);
});

const seed = (over: Record<string, unknown>) => ({
  id: "t1", title: "T", status: "open", priority: 2, labels: [],
  project_id: "p1", parent_task_id: null, body: "",
  assignee_id: null, claimed_by: null, claimed_at: null,
  closed_at: null, closed_by: null, close_reason: null,
  created_by: "u", created_at: 0, updated_at: 0,
  ...over,
}) as any;

describe("useBoardData", () => {
  it("loads the initial task list", async () => {
    vi.spyOn(projectsApi.tasks, "list").mockResolvedValue([seed({})]);
    const { result } = renderHook(() => useBoardData("p1"));
    await waitFor(() => expect(result.current.tasks.length).toBe(1));
  });

  it("applies a task.updated event", async () => {
    vi.spyOn(projectsApi.tasks, "list").mockImplementation(async (_pid, status) => {
      return status === "open" ? [seed({})] : [];
    });
    const { result } = renderHook(() => useBoardData("p1"));
    await waitFor(() => expect(result.current.tasks.length).toBe(1));
    act(() => result.current.applyEvent({ kind: "task.updated", payload: { id: "t1", patch: { title: "Renamed" } }, ts: 0 }));
    expect(result.current.tasks[0].title).toBe("Renamed");
  });

  it("loads the project's elements", async () => {
    const el = { id: "el1", project_id: "p1", name: "Website", slug: "website", type: "website", description: "", assignee_id: null, settings: {}, created_at: 0, updated_at: 0, archived_at: null };
    vi.spyOn(projectsApi.tasks, "list").mockResolvedValue([]);
    vi.spyOn(projectsApi.elements, "list").mockResolvedValue([el as any]);
    const { result } = renderHook(() => useBoardData("p1"));
    await waitFor(() => expect(result.current.elements.length).toBe(1));
    expect(result.current.elements[0].id).toBe("el1");
  });
});
