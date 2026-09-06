import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchLibrary, fetchPersonaDetail } from "./personas-api";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("fetchLibrary", () => {
  it("calls GET /api/personas/library and returns personas array", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        personas: [
          { source: "builtin", id: "base", name: "Base", preview: "hello" },
          { source: "user", id: "custom-1", name: "Custom", preview: "world" },
        ],
      }),
    });
    global.fetch = fetchMock;

    const result = await fetchLibrary({});

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/personas/library?");
    expect(result).toHaveLength(2);
    expect(result[0].id).toBe("base");
    expect(result[0].source).toBe("builtin");
    expect(result[1].name).toBe("Custom");
  });

  it("appends source, q, limit, offset params when provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ personas: [] }),
    });
    global.fetch = fetchMock;

    await fetchLibrary({ source: "user", q: "test", limit: 10, offset: 5 });

    const [url] = fetchMock.mock.calls[0];
    const qs = new URLSearchParams(url.split("?")[1]);
    expect(qs.get("source")).toBe("user");
    expect(qs.get("q")).toBe("test");
    expect(qs.get("limit")).toBe("10");
    expect(qs.get("offset")).toBe("5");
  });

  it("omits params when not provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ personas: [] }),
    });
    global.fetch = fetchMock;

    await fetchLibrary({});

    const [url] = fetchMock.mock.calls[0];
    const qs = new URLSearchParams(url.split("?")[1]);
    expect(qs.has("source")).toBe(false);
    expect(qs.has("q")).toBe(false);
    expect(qs.has("limit")).toBe(false);
    expect(qs.has("offset")).toBe(false);
  });
});

describe("fetchPersonaDetail", () => {
  it("fetches builtin source from /api/templates/{id}", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        id: "base",
        name: "Base",
        system_prompt: "You are a helpful assistant.",
      }),
    });
    global.fetch = fetchMock;

    const result = await fetchPersonaDetail("builtin", "base");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/templates/base");
    expect(result.id).toBe("base");
    expect(result.name).toBe("Base");
    expect(result.source).toBe("builtin");
    expect(result.soul_md).toBe("You are a helpful assistant.");
    expect(result.agent_md).toBeUndefined();
  });

  it("fetches awesome-openclaw source from /api/templates/{id}", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        id: "cool-persona",
        name: "Cool",
        system_prompt: "Be cool.",
      }),
    });
    global.fetch = fetchMock;

    const result = await fetchPersonaDetail("awesome-openclaw", "cool-persona");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/templates/cool-persona");
    expect(result.source).toBe("awesome-openclaw");
    expect(result.soul_md).toBe("Be cool.");
  });

  it("fetches prompt-library source from /api/templates/{id}", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        id: "lib-tmpl",
        name: "Lib Template",
        system_prompt: "Template prompt.",
      }),
    });
    global.fetch = fetchMock;

    const result = await fetchPersonaDetail("prompt-library", "lib-tmpl");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/templates/lib-tmpl");
    expect(result.source).toBe("prompt-library");
  });

  it("encodes id in template URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        id: "a/b",
        name: "AB",
        system_prompt: "prompt",
      }),
    });
    global.fetch = fetchMock;

    await fetchPersonaDetail("builtin", "a/b");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/templates/a%2Fb");
  });

  it("fetches user source from /api/user-personas/{id}", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        id: "my-persona",
        name: "My Persona",
        soul_md: "Soul content.",
        agent_md: "Agent content.",
      }),
    });
    global.fetch = fetchMock;

    const result = await fetchPersonaDetail("user", "my-persona");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/user-personas/my-persona");
    expect(result.id).toBe("my-persona");
    expect(result.name).toBe("My Persona");
    expect(result.source).toBe("user");
    expect(result.soul_md).toBe("Soul content.");
    expect(result.agent_md).toBe("Agent content.");
  });

  it("encodes id in user-personas URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        id: "x y",
        name: "XY",
        soul_md: "s",
      }),
    });
    global.fetch = fetchMock;

    await fetchPersonaDetail("user", "x y");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/user-personas/x%20y");
  });

  it("returns empty soul_md when system_prompt is missing for builtin", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        id: "base",
        name: "Base",
      }),
    });
    global.fetch = fetchMock;

    const result = await fetchPersonaDetail("builtin", "base");

    expect(result.soul_md).toBe("");
  });

  it("returns empty soul_md when soul_md is missing for user", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        id: "u-1",
        name: "U1",
      }),
    });
    global.fetch = fetchMock;

    const result = await fetchPersonaDetail("user", "u-1");

    expect(result.soul_md).toBe("");
  });

  it("throws on non-ok response for builtin template", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
    });

    await expect(fetchPersonaDetail("builtin", "missing")).rejects.toThrow(
      "Template not found: missing",
    );
  });

  it("throws on non-ok response for user persona", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
    });

    await expect(fetchPersonaDetail("user", "nope")).rejects.toThrow(
      "User persona not found: nope",
    );
  });

  it("throws on unsupported source", async () => {
    await expect(fetchPersonaDetail("unknown", "x")).rejects.toThrow(
      "Unsupported source for detail fetch: unknown",
    );
  });
});
