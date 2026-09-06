import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchTaosAgentConfig,
  setTaosAgentModel,
  setTaosAgentPermitted,
  setTaosAgentPersona,
  uploadChatAttachment,
} from "./taos-agent-api";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("fetchTaosAgentConfig", () => {
  it("calls the right URL and returns parsed config", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        model: "gpt-4",
        permitted_models: ["gpt-4", "claude-3"],
        persona: "default",
        key_masked: "sk-***",
        framework: "opencode",
        system: true,
      }),
    });
    global.fetch = fetchMock;

    const result = await fetchTaosAgentConfig();

    expect(fetchMock).toHaveBeenCalledWith("/api/taos-agent/config", {
      method: "GET",
    });
    expect(result.model).toBe("gpt-4");
    expect(result.permitted_models).toEqual(["gpt-4", "claude-3"]);
    expect(result.persona).toBe("default");
    expect(result.key_masked).toBe("sk-***");
    expect(result.framework).toBe("opencode");
  });

  it("throws body.error on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: "bad config" }),
    });

    await expect(fetchTaosAgentConfig()).rejects.toThrow("bad config");
  });

  it("throws body.detail on non-ok response when no error", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({ detail: "forbidden" }),
    });

    await expect(fetchTaosAgentConfig()).rejects.toThrow("forbidden");
  });

  it("throws fallback on non-ok when json has no error or detail", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({}),
    });

    await expect(fetchTaosAgentConfig()).rejects.toThrow(
      "Request failed (500)",
    );
  });
});

describe("setTaosAgentModel", () => {
  it("PATCHes settings and returns updated model", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ model: "claude-3" }),
    });
    global.fetch = fetchMock;

    const result = await setTaosAgentModel("claude-3");

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/taos-agent/settings");
    expect(opts.method).toBe("PATCH");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(opts.body)).toEqual({ model: "claude-3" });
    expect(result.model).toBe("claude-3");
  });

  it("throws body.error on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: "invalid model" }),
    });

    await expect(setTaosAgentModel("bad-model")).rejects.toThrow(
      "invalid model",
    );
  });
});

describe("setTaosAgentPermitted", () => {
  it("PUTs permitted-models and returns updated state", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        permitted_models: ["gpt-4", "claude-3"],
        key_rescoped: true,
      }),
    });
    global.fetch = fetchMock;

    const result = await setTaosAgentPermitted(["gpt-4", "claude-3"]);

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/taos-agent/permitted-models");
    expect(opts.method).toBe("PUT");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(opts.body)).toEqual({
      models: ["gpt-4", "claude-3"],
    });
    expect(result.permitted_models).toEqual(["gpt-4", "claude-3"]);
    expect(result.key_rescoped).toBe(true);
  });

  it("throws body.detail on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({ detail: "not allowed" }),
    });

    await expect(setTaosAgentPermitted([])).rejects.toThrow("not allowed");
  });
});

describe("setTaosAgentPersona", () => {
  it("PUTs persona and returns updated persona", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ persona: "helpful" }),
    });
    global.fetch = fetchMock;

    const result = await setTaosAgentPersona("helpful");

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/taos-agent/persona");
    expect(opts.method).toBe("PUT");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(opts.body)).toEqual({ persona: "helpful" });
    expect(result.persona).toBe("helpful");
  });

  it("throws fallback on non-ok when json parse fails", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => {
        throw new Error("not json");
      },
    });

    await expect(setTaosAgentPersona("x")).rejects.toThrow(
      "Request failed (500)",
    );
  });
});

describe("uploadChatAttachment", () => {
  it("POSTs form data and returns attachment record", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        filename: "photo.png",
        mime_type: "image/png",
        size: 1024,
        url: "/attachments/photo.png",
      }),
    });
    global.fetch = fetchMock;

    const form = new FormData();
    form.append("file", new Blob(["fake"]), "photo.png");

    const result = await uploadChatAttachment(form);

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/taos-agent/attachments/upload");
    expect(opts.method).toBe("POST");
    expect(opts.body).toBe(form);
    expect(result.filename).toBe("photo.png");
    expect(result.mime_type).toBe("image/png");
    expect(result.size).toBe(1024);
    expect(result.url).toBe("/attachments/photo.png");
  });

  it("throws response text on non-ok", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 413,
      text: async () => "file too large",
    });

    const form = new FormData();
    await expect(uploadChatAttachment(form)).rejects.toThrow("file too large");
  });

  it("throws fallback when text() fails on non-ok", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      text: async () => {
        throw new Error("no text");
      },
    });

    const form = new FormData();
    await expect(uploadChatAttachment(form)).rejects.toThrow("upload failed");
  });
});
