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

describe("projectsApi.routines", () => {
  it("list fetches routines for a project", async () => {
    fetchMock.mockResolvedValueOnce(ok({ items: [{ id: "rtn-1" }] }));
    const r = await projectsApi.routines.list("p1");
    expect(fetchMock.mock.calls[0][0]).toMatch("/api/projects/p1/routines");
    expect(r).toEqual([{ id: "rtn-1" }]);
  });

  it("create POSTs the routine payload", async () => {
    fetchMock.mockResolvedValueOnce(ok({ id: "rtn-1", trigger_kind: "cron" }));
    await projectsApi.routines.create("p1", {
      title: "Nightly",
      trigger_kind: "cron",
      cron_expr: "0 3 * * *",
    });
    expect(fetchMock.mock.calls[0][0]).toMatch("/api/projects/p1/routines");
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body).toEqual({ title: "Nightly", trigger_kind: "cron", cron_expr: "0 3 * * *" });
  });

  it("update PATCHes the routine", async () => {
    fetchMock.mockResolvedValueOnce(ok({ id: "rtn-1", enabled: 0 }));
    await projectsApi.routines.update("p1", "rtn-1", { enabled: false });
    expect(fetchMock.mock.calls[0][0]).toMatch("/api/projects/p1/routines/rtn-1");
    expect(fetchMock.mock.calls[0][1].method).toBe("PATCH");
  });

  it("remove DELETEs the routine", async () => {
    fetchMock.mockResolvedValueOnce(ok({ ok: true }));
    await projectsApi.routines.remove("p1", "rtn-1");
    expect(fetchMock.mock.calls[0][1].method).toBe("DELETE");
  });

  it("trigger POSTs to the /trigger endpoint", async () => {
    fetchMock.mockResolvedValueOnce(ok({ ok: true, task: { id: "tsk-1" } }));
    const r = await projectsApi.routines.trigger("p1", "rtn-1");
    expect(fetchMock.mock.calls[0][0]).toMatch("/api/projects/p1/routines/rtn-1/trigger");
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
    expect(r.task.id).toBe("tsk-1");
  });
});
