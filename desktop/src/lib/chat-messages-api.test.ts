import { afterEach, describe, expect, it, vi } from "vitest";
import {
  pinMessage,
  unpinMessage,
  listPins,
  editMessage,
  deleteMessage,
  markUnread,
} from "./chat-messages-api";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("pinMessage", () => {
  it("calls POST /api/chat/messages/:id/pin and returns void on success", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    await pinMessage("msg-1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/chat/messages/msg-1/pin");
    expect(opts.method).toBe("POST");
  });

  it("throws on non-ok response with error body", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: "already pinned" }),
    });

    await expect(pinMessage("msg-1")).rejects.toThrow("already pinned");
  });

  it("throws with HTTP status when body has no error", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({}),
    });

    await expect(pinMessage("msg-1")).rejects.toThrow("HTTP 500");
  });
});

describe("unpinMessage", () => {
  it("calls DELETE /api/chat/messages/:id/pin and returns void on success", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    await unpinMessage("msg-1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/chat/messages/msg-1/pin");
    expect(opts.method).toBe("DELETE");
  });

  it("throws on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ error: "not found" }),
    });

    await expect(unpinMessage("msg-1")).rejects.toThrow("not found");
  });
});

describe("listPins", () => {
  it("calls GET /api/chat/channels/:id/pins and returns pins array", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ pins: ["msg-1", "msg-2"] }),
    });
    global.fetch = fetchMock;

    const result = await listPins("ch-1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/chat/channels/ch-1/pins");
    expect(result).toEqual(["msg-1", "msg-2"]);
  });

  it("returns empty array when pins is falsy", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ pins: null }),
    });

    const result = await listPins("ch-1");
    expect(result).toEqual([]);
  });

  it("throws on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({ error: "forbidden" }),
    });

    await expect(listPins("ch-1")).rejects.toThrow("forbidden");
  });
});

describe("editMessage", () => {
  it("calls PATCH /api/chat/messages/:id with content and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ id: "msg-1", content: "updated" }),
    });
    global.fetch = fetchMock;

    const result = await editMessage("msg-1", "updated");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/chat/messages/msg-1");
    expect(opts.method).toBe("PATCH");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    const body = JSON.parse(opts.body);
    expect(body.content).toBe("updated");
    expect(result).toEqual({ id: "msg-1", content: "updated" });
  });

  it("throws on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: "edit failed" }),
    });

    await expect(editMessage("msg-1", "x")).rejects.toThrow("edit failed");
  });
});

describe("deleteMessage", () => {
  it("calls DELETE /api/chat/messages/:id and returns void on success", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    await deleteMessage("msg-1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/chat/messages/msg-1");
    expect(opts.method).toBe("DELETE");
  });

  it("throws on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ error: "not found" }),
    });

    await expect(deleteMessage("msg-1")).rejects.toThrow("not found");
  });
});

describe("markUnread", () => {
  it("calls POST /api/chat/channels/:id/read-cursor/rewind with before_message_id", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    await markUnread("ch-1", "msg-5");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/chat/channels/ch-1/read-cursor/rewind");
    expect(opts.method).toBe("POST");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    const body = JSON.parse(opts.body);
    expect(body.before_message_id).toBe("msg-5");
  });

  it("throws on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ error: "server error" }),
    });

    await expect(markUnread("ch-1", "msg-5")).rejects.toThrow("server error");
  });
});
