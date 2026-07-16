import { afterEach, describe, expect, it, vi } from "vitest";
import { projectsApi } from "./projects";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

const mockFetch = (response: unknown, ok = true, status = 200) => {
  global.fetch = vi.fn().mockResolvedValue({
    ok,
    status,
    json: async () => response,
    text: async () => (typeof response === "string" ? response : JSON.stringify(response)),
  });
};

const mockFetchError = (status: number, body: string) => {
  global.fetch = vi.fn().mockResolvedValue({
    ok: false,
    status,
    statusText: body,
    text: async () => body,
    json: async () => ({}),
  });
};

describe("projectsApi.list", () => {
  it("returns items array on 200", async () => {
    mockFetch({
      items: [
        { id: "p-1", name: "One", slug: "one", description: "d", status: "active", created_by: "u-1", created_at: 1, updated_at: 1 },
      ],
    });
    const result = await projectsApi.list();
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("p-1");
  });

  it("passes status query param", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) });
    global.fetch = fetchMock;
    await projectsApi.list("archived");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("status=archived");
  });

  it("throws on non-ok response", async () => {
    mockFetchError(500, "server error");
    await expect(projectsApi.list()).rejects.toThrow("500");
  });
});

describe("projectsApi.get", () => {
  it("returns project on 200", async () => {
    mockFetch({ id: "p-1", name: "One", slug: "one", description: "d", status: "active", created_by: "u-1", created_at: 1, updated_at: 1 });
    const result = await projectsApi.get("p-1");
    expect(result.id).toBe("p-1");
    expect(result.name).toBe("One");
  });

  it("calls correct URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    global.fetch = fetchMock;
    await projectsApi.get("p-42");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects/p-42");
  });

  it("throws on non-ok response", async () => {
    mockFetchError(404, "not found");
    await expect(projectsApi.get("p-1")).rejects.toThrow("404");
  });
});

describe("projectsApi.create", () => {
  it("returns project on 200", async () => {
    mockFetch({ id: "p-1", name: "New", slug: "new", description: "", status: "active", created_by: "u-1", created_at: 1, updated_at: 1 });
    const result = await projectsApi.create({ name: "New", slug: "new" });
    expect(result.id).toBe("p-1");
  });

  it("posts with correct body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    global.fetch = fetchMock;
    await projectsApi.create({ name: "New", slug: "new", description: "desc" });
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects");
    expect(opts.method).toBe("POST");
    const body = JSON.parse(opts.body);
    expect(body.name).toBe("New");
    expect(body.slug).toBe("new");
    expect(body.description).toBe("desc");
  });

  it("throws on non-ok response", async () => {
    mockFetchError(400, "bad request");
    await expect(projectsApi.create({ name: "x", slug: "x" })).rejects.toThrow("400");
  });
});

describe("projectsApi.update", () => {
  it("returns project on 200", async () => {
    mockFetch({ id: "p-1", name: "Updated", slug: "one", description: "d", status: "active", created_by: "u-1", created_at: 1, updated_at: 2 });
    const result = await projectsApi.update("p-1", { name: "Updated" });
    expect(result.name).toBe("Updated");
  });

  it("patches with correct body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    global.fetch = fetchMock;
    await projectsApi.update("p-1", { name: "Updated", description: "new desc" });
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects/p-1");
    expect(opts.method).toBe("PATCH");
    const body = JSON.parse(opts.body);
    expect(body.name).toBe("Updated");
    expect(body.description).toBe("new desc");
  });

  it("throws on non-ok response", async () => {
    mockFetchError(403, "forbidden");
    await expect(projectsApi.update("p-1", { name: "x" })).rejects.toThrow("403");
  });
});

describe("projectsApi.archive", () => {
  it("returns project on 200", async () => {
    mockFetch({ id: "p-1", name: "One", slug: "one", description: "d", status: "archived", created_by: "u-1", created_at: 1, updated_at: 2 });
    const result = await projectsApi.archive("p-1");
    expect(result.status).toBe("archived");
  });

  it("posts to archive endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    global.fetch = fetchMock;
    await projectsApi.archive("p-1");
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects/p-1/archive");
    expect(opts.method).toBe("POST");
  });

  it("throws on non-ok response", async () => {
    mockFetchError(500, "error");
    await expect(projectsApi.archive("p-1")).rejects.toThrow("500");
  });
});

describe("projectsApi.remove", () => {
  it("returns project on 200", async () => {
    mockFetch({ id: "p-1", name: "One", slug: "one", description: "d", status: "deleted", created_by: "u-1", created_at: 1, updated_at: 2 });
    const result = await projectsApi.remove("p-1");
    expect(result.status).toBe("deleted");
  });

  it("sends DELETE to correct URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    global.fetch = fetchMock;
    await projectsApi.remove("p-1");
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects/p-1");
    expect(opts.method).toBe("DELETE");
  });

  it("throws on non-ok response", async () => {
    mockFetchError(404, "not found");
    await expect(projectsApi.remove("p-1")).rejects.toThrow("404");
  });
});

describe("projectsApi.members.list", () => {
  it("returns items array on 200", async () => {
    mockFetch({
      items: [
        { project_id: "p-1", member_id: "m-1", member_kind: "native", role: "editor", source_agent_id: null, memory_seed: "none", added_at: 1 },
      ],
    });
    const result = await projectsApi.members.list("p-1");
    expect(result).toHaveLength(1);
    expect(result[0].member_id).toBe("m-1");
  });

  it("calls correct URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) });
    global.fetch = fetchMock;
    await projectsApi.members.list("p-1");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects/p-1/members");
  });

  it("throws on non-ok response", async () => {
    mockFetchError(403, "forbidden");
    await expect(projectsApi.members.list("p-1")).rejects.toThrow("403");
  });
});

describe("projectsApi.members.addNative", () => {
  it("returns member on 200", async () => {
    mockFetch({ project_id: "p-1", member_id: "m-1", member_kind: "native", role: "editor", source_agent_id: "a-1", memory_seed: "none", added_at: 1 });
    const result = await projectsApi.members.addNative("p-1", "a-1");
    expect(result.member_kind).toBe("native");
  });

  it("posts with mode native and agent_id", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    global.fetch = fetchMock;
    await projectsApi.members.addNative("p-1", "a-1");
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects/p-1/members");
    expect(opts.method).toBe("POST");
    const body = JSON.parse(opts.body);
    expect(body.mode).toBe("native");
    expect(body.agent_id).toBe("a-1");
  });

  it("throws on non-ok response", async () => {
    mockFetchError(400, "bad request");
    await expect(projectsApi.members.addNative("p-1", "a-1")).rejects.toThrow("400");
  });
});

describe("projectsApi.members.addClone", () => {
  it("returns member on 200", async () => {
    mockFetch({ project_id: "p-1", member_id: "m-1", member_kind: "clone", role: "editor", source_agent_id: "a-1", memory_seed: "snapshot", added_at: 1 });
    const result = await projectsApi.members.addClone("p-1", "a-1", true);
    expect(result.member_kind).toBe("clone");
  });

  it("posts with mode clone, source_agent_id, and clone_memory", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    global.fetch = fetchMock;
    await projectsApi.members.addClone("p-1", "a-1", true);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects/p-1/members");
    expect(opts.method).toBe("POST");
    const body = JSON.parse(opts.body);
    expect(body.mode).toBe("clone");
    expect(body.source_agent_id).toBe("a-1");
    expect(body.clone_memory).toBe(true);
  });

  it("throws on non-ok response", async () => {
    mockFetchError(400, "bad request");
    await expect(projectsApi.members.addClone("p-1", "a-1", false)).rejects.toThrow("400");
  });
});

describe("projectsApi.members.remove", () => {
  it("returns ok on 200", async () => {
    mockFetch({ ok: true });
    const result = await projectsApi.members.remove("p-1", "m-1");
    expect(result.ok).toBe(true);
  });

  it("sends DELETE to correct URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    global.fetch = fetchMock;
    await projectsApi.members.remove("p-1", "m-1");
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects/p-1/members/m-1");
    expect(opts.method).toBe("DELETE");
  });

  it("throws on non-ok response", async () => {
    mockFetchError(404, "not found");
    await expect(projectsApi.members.remove("p-1", "m-1")).rejects.toThrow("404");
  });
});

describe("projectsApi.setLead (D7, exclusive project pointer)", () => {
  it("returns result on 200", async () => {
    mockFetch({ ok: true, lead_member_id: "m-1" });
    const result = await projectsApi.setLead("p-1", "m-1");
    expect(result.lead_member_id).toBe("m-1");
  });

  it("patches the project lead endpoint with a member_id body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true, lead_member_id: null }) });
    global.fetch = fetchMock;
    await projectsApi.setLead("p-1", null);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects/p-1/lead");
    expect(opts.method).toBe("PATCH");
    const body = JSON.parse(opts.body);
    expect(body.member_id).toBe(null);
  });

  it("throws on non-ok response", async () => {
    mockFetchError(403, "forbidden");
    await expect(projectsApi.setLead("p-1", "m-1")).rejects.toThrow("403");
  });
});

describe("projectsApi.tasks.list", () => {
  it("returns items array on 200", async () => {
    mockFetch({
      items: [
        { id: "t-1", project_id: "p-1", parent_task_id: null, title: "Task", body: "", status: "open", priority: 0, labels: [], assignee_id: null, claimed_by: null, claimed_at: null, closed_at: null, closed_by: null, close_reason: null, created_by: "u-1", created_at: 1, updated_at: 1 },
      ],
    });
    const result = await projectsApi.tasks.list("p-1");
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("t-1");
  });

  it("omits status param when not provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) });
    global.fetch = fetchMock;
    await projectsApi.tasks.list("p-1");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects/p-1/tasks");
  });

  it("includes status param when provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) });
    global.fetch = fetchMock;
    await projectsApi.tasks.list("p-1", "open");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("status=open");
  });

  it("throws on non-ok response", async () => {
    mockFetchError(500, "error");
    await expect(projectsApi.tasks.list("p-1")).rejects.toThrow("500");
  });
});

describe("projectsApi.tasks.ready", () => {
  it("returns items array on 200", async () => {
    mockFetch({ items: [] });
    const result = await projectsApi.tasks.ready("p-1");
    expect(result).toEqual([]);
  });

  it("calls correct URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) });
    global.fetch = fetchMock;
    await projectsApi.tasks.ready("p-1");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects/p-1/tasks/ready");
  });

  it("throws on non-ok response", async () => {
    mockFetchError(500, "error");
    await expect(projectsApi.tasks.ready("p-1")).rejects.toThrow("500");
  });
});

describe("projectsApi.tasks.create", () => {
  it("returns task on 200", async () => {
    mockFetch({ id: "t-1", project_id: "p-1", parent_task_id: null, title: "New", body: "", status: "open", priority: 0, labels: [], assignee_id: null, claimed_by: null, claimed_at: null, closed_at: null, closed_by: null, close_reason: null, created_by: "u-1", created_at: 1, updated_at: 1 });
    const result = await projectsApi.tasks.create("p-1", { title: "New" });
    expect(result.id).toBe("t-1");
  });

  it("posts with correct body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    global.fetch = fetchMock;
    await projectsApi.tasks.create("p-1", { title: "New", body: "desc", priority: 1 });
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects/p-1/tasks");
    expect(opts.method).toBe("POST");
    const body = JSON.parse(opts.body);
    expect(body.title).toBe("New");
    expect(body.body).toBe("desc");
    expect(body.priority).toBe(1);
  });

  it("throws on non-ok response", async () => {
    mockFetchError(400, "bad request");
    await expect(projectsApi.tasks.create("p-1", { title: "x" })).rejects.toThrow("400");
  });
});

describe("projectsApi.tasks.claim", () => {
  it("returns task on 200", async () => {
    mockFetch({ id: "t-1", project_id: "p-1", parent_task_id: null, title: "Task", body: "", status: "claimed", priority: 0, labels: [], assignee_id: "u-1", claimed_by: "u-1", claimed_at: 1, closed_at: null, closed_by: null, close_reason: null, created_by: "u-1", created_at: 1, updated_at: 1 });
    const result = await projectsApi.tasks.claim("p-1", "t-1", "u-1");
    expect(result.status).toBe("claimed");
  });

  it("posts with claimer_id", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    global.fetch = fetchMock;
    await projectsApi.tasks.claim("p-1", "t-1", "u-1");
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects/p-1/tasks/t-1/claim");
    expect(opts.method).toBe("POST");
    const body = JSON.parse(opts.body);
    expect(body.claimer_id).toBe("u-1");
  });

  it("throws on non-ok response", async () => {
    mockFetchError(409, "conflict");
    await expect(projectsApi.tasks.claim("p-1", "t-1", "u-1")).rejects.toThrow("409");
  });
});

describe("projectsApi.tasks.release", () => {
  it("returns task on 200", async () => {
    mockFetch({ id: "t-1", project_id: "p-1", parent_task_id: null, title: "Task", body: "", status: "open", priority: 0, labels: [], assignee_id: null, claimed_by: null, claimed_at: null, closed_at: null, closed_by: null, close_reason: null, created_by: "u-1", created_at: 1, updated_at: 1 });
    const result = await projectsApi.tasks.release("p-1", "t-1", "u-1");
    expect(result.status).toBe("open");
  });

  it("posts with releaser_id", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    global.fetch = fetchMock;
    await projectsApi.tasks.release("p-1", "t-1", "u-1");
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects/p-1/tasks/t-1/release");
    expect(opts.method).toBe("POST");
    const body = JSON.parse(opts.body);
    expect(body.releaser_id).toBe("u-1");
  });

  it("throws on non-ok response", async () => {
    mockFetchError(500, "error");
    await expect(projectsApi.tasks.release("p-1", "t-1", "u-1")).rejects.toThrow("500");
  });
});

describe("projectsApi.tasks.close", () => {
  it("returns task on 200", async () => {
    mockFetch({ id: "t-1", project_id: "p-1", parent_task_id: null, title: "Task", body: "", status: "closed", priority: 0, labels: [], assignee_id: null, claimed_by: null, claimed_at: null, closed_at: 1, closed_by: "u-1", close_reason: "done", created_by: "u-1", created_at: 1, updated_at: 1 });
    const result = await projectsApi.tasks.close("p-1", "t-1", "u-1", "done");
    expect(result.status).toBe("closed");
  });

  it("posts with closed_by and reason", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    global.fetch = fetchMock;
    await projectsApi.tasks.close("p-1", "t-1", "u-1", "done");
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects/p-1/tasks/t-1/close");
    expect(opts.method).toBe("POST");
    const body = JSON.parse(opts.body);
    expect(body.closed_by).toBe("u-1");
    expect(body.reason).toBe("done");
  });

  it("throws on non-ok response", async () => {
    mockFetchError(500, "error");
    await expect(projectsApi.tasks.close("p-1", "t-1", "u-1")).rejects.toThrow("500");
  });
});

describe("projectsApi.tasks.update", () => {
  it("returns task on 200", async () => {
    mockFetch({ id: "t-1", project_id: "p-1", parent_task_id: null, title: "Updated", body: "", status: "open", priority: 0, labels: [], assignee_id: null, claimed_by: null, claimed_at: null, closed_at: null, closed_by: null, close_reason: null, created_by: "u-1", created_at: 1, updated_at: 2 });
    const result = await projectsApi.tasks.update("p-1", "t-1", { title: "Updated" });
    expect(result.title).toBe("Updated");
  });

  it("patches with correct body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    global.fetch = fetchMock;
    await projectsApi.tasks.update("p-1", "t-1", { title: "Updated", status: "closed" });
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects/p-1/tasks/t-1");
    expect(opts.method).toBe("PATCH");
    const body = JSON.parse(opts.body);
    expect(body.title).toBe("Updated");
    expect(body.status).toBe("closed");
  });

  it("throws on non-ok response", async () => {
    mockFetchError(403, "forbidden");
    await expect(projectsApi.tasks.update("p-1", "t-1", { title: "x" })).rejects.toThrow("403");
  });
});

describe("projectsApi.tasks.listComments", () => {
  it("returns items array on 200", async () => {
    mockFetch({
      items: [
        { id: "c-1", task_id: "t-1", author_id: "u-1", body: "hi", replies_to_comment_id: null, created_at: 1 },
      ],
    });
    const result = await projectsApi.tasks.listComments("p-1", "t-1");
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("c-1");
  });

  it("calls correct URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) });
    global.fetch = fetchMock;
    await projectsApi.tasks.listComments("p-1", "t-1");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects/p-1/tasks/t-1/comments");
  });

  it("throws on non-ok response", async () => {
    mockFetchError(500, "error");
    await expect(projectsApi.tasks.listComments("p-1", "t-1")).rejects.toThrow("500");
  });
});

describe("projectsApi.tasks.addComment", () => {
  it("returns comment on 200", async () => {
    mockFetch({ id: "c-1", task_id: "t-1", author_id: "u-1", body: "hi", replies_to_comment_id: null, created_at: 1 });
    const result = await projectsApi.tasks.addComment("p-1", "t-1", { body: "hi", author_id: "u-1" });
    expect(result.id).toBe("c-1");
  });

  it("posts with correct body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    global.fetch = fetchMock;
    await projectsApi.tasks.addComment("p-1", "t-1", { body: "hi", author_id: "u-1", replies_to_comment_id: "c-0" });
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects/p-1/tasks/t-1/comments");
    expect(opts.method).toBe("POST");
    const body = JSON.parse(opts.body);
    expect(body.body).toBe("hi");
    expect(body.author_id).toBe("u-1");
    expect(body.replies_to_comment_id).toBe("c-0");
  });

  it("throws on non-ok response", async () => {
    mockFetchError(400, "bad request");
    await expect(projectsApi.tasks.addComment("p-1", "t-1", { body: "x", author_id: "u-1" })).rejects.toThrow("400");
  });
});

describe("projectsApi.tasks.listRelationships", () => {
  it("returns items array on 200", async () => {
    mockFetch({
      items: [
        { id: "r-1", project_id: "p-1", from_task_id: "t-1", to_task_id: "t-2", kind: "blocks", created_by: "u-1", created_at: 1 },
      ],
    });
    const result = await projectsApi.tasks.listRelationships("p-1", "t-1");
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("r-1");
  });

  it("defaults direction to from", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) });
    global.fetch = fetchMock;
    await projectsApi.tasks.listRelationships("p-1", "t-1");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("direction=from");
  });

  it("passes direction param", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) });
    global.fetch = fetchMock;
    await projectsApi.tasks.listRelationships("p-1", "t-1", "to");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("direction=to");
  });

  it("throws on non-ok response", async () => {
    mockFetchError(500, "error");
    await expect(projectsApi.tasks.listRelationships("p-1", "t-1")).rejects.toThrow("500");
  });
});

describe("projectsApi.tasks.addRelationship", () => {
  it("returns relationship on 200", async () => {
    mockFetch({ id: "r-1", project_id: "p-1", from_task_id: "t-1", to_task_id: "t-2", kind: "blocks", created_by: "u-1", created_at: 1 });
    const result = await projectsApi.tasks.addRelationship("p-1", "t-1", { to_task_id: "t-2", kind: "blocks", created_by: "u-1" });
    expect(result.id).toBe("r-1");
  });

  it("posts with correct body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    global.fetch = fetchMock;
    await projectsApi.tasks.addRelationship("p-1", "t-1", { to_task_id: "t-2", kind: "blocks", created_by: "u-1" });
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects/p-1/tasks/t-1/relationships");
    expect(opts.method).toBe("POST");
    const body = JSON.parse(opts.body);
    expect(body.to_task_id).toBe("t-2");
    expect(body.kind).toBe("blocks");
    expect(body.created_by).toBe("u-1");
  });

  it("throws on non-ok response", async () => {
    mockFetchError(400, "bad request");
    await expect(projectsApi.tasks.addRelationship("p-1", "t-1", { to_task_id: "t-2", kind: "x", created_by: "u-1" })).rejects.toThrow("400");
  });
});

describe("projectsApi.activity", () => {
  it("returns items array on 200", async () => {
    mockFetch({
      items: [
        { id: 1, project_id: "p-1", actor_id: "u-1", kind: "created", payload: {}, created_at: 1 },
      ],
    });
    const result = await projectsApi.activity("p-1");
    expect(result).toHaveLength(1);
    expect(result[0].kind).toBe("created");
  });

  it("calls correct URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) });
    global.fetch = fetchMock;
    await projectsApi.activity("p-1");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects/p-1/activity");
  });

  it("throws on non-ok response", async () => {
    mockFetchError(500, "error");
    await expect(projectsApi.activity("p-1")).rejects.toThrow("500");
  });
});
