import { describe, it, expect, vi, beforeEach } from "vitest";
import { projectsApi } from "../projects";

const fetchMock = vi.fn();
beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

function ok(data: unknown) {
  return { ok: true, status: 200, json: async () => data };
}

describe("projectsApi.tasks (extended)", () => {
  it("update PATCHes the task", async () => {
    fetchMock.mockResolvedValueOnce(ok({ id: "t1", title: "renamed" }));
    const r = await projectsApi.tasks.update("p1", "t1", { title: "renamed" });
    expect(fetchMock.mock.calls[0][0]).toMatch("/api/projects/p1/tasks/t1");
    expect(fetchMock.mock.calls[0][1].method).toBe("PATCH");
    expect(r.title).toBe("renamed");
  });

  it("listComments fetches comment list", async () => {
    fetchMock.mockResolvedValueOnce(ok({ items: [{ id: "c1" }] }));
    const r = await projectsApi.tasks.listComments("p1", "t1");
    expect(fetchMock.mock.calls[0][0]).toMatch("/api/projects/p1/tasks/t1/comments");
    expect(r).toEqual([{ id: "c1" }]);
  });

  it("addComment posts a comment", async () => {
    fetchMock.mockResolvedValueOnce(ok({ id: "c1" }));
    await projectsApi.tasks.addComment("p1", "t1", { body: "hi", author_id: "u" });
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
  });

  it("addRelationship posts an edge", async () => {
    fetchMock.mockResolvedValueOnce(ok({ from_task_id: "a", to_task_id: "b", kind: "blocks" }));
    await projectsApi.tasks.addRelationship("p1", "a", { to_task_id: "b", kind: "blocks", created_by: "u" });
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
  });

  it("listRelationships fetches edges", async () => {
    fetchMock.mockResolvedValueOnce(ok({ items: [] }));
    await projectsApi.tasks.listRelationships("p1", "t1", "from");
    expect(fetchMock.mock.calls[0][0]).toMatch("/relationships?direction=from");
  });
});

describe("projectsApi.subscribeEvents", () => {
  it("opens an EventSource for the project events endpoint", () => {
    const closeSpy = vi.fn();
    // subscribeEvents calls `new EventSource(...)`, so the mock must be
    // constructable. An arrow function is not (`new (() => {})` throws), so use
    // a regular function whose returned object becomes the instance.
    const eventSourceMock = vi.fn(function () {
      return { close: closeSpy, onmessage: null as ((e: MessageEvent) => void) | null };
    });
    vi.stubGlobal("EventSource", eventSourceMock);
    const off = projectsApi.subscribeEvents("p1", () => {});
    expect(eventSourceMock).toHaveBeenCalledWith(expect.stringMatching("/api/projects/p1/events"));
    off();
    expect(closeSpy).toHaveBeenCalled();
  });
});
