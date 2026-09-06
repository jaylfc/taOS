import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  pinMessage, unpinMessage, listPins,
  editMessage, deleteMessage, markUnread,
} from "../chat-messages-api";

describe("chat-messages-api", () => {
  beforeEach(() => {
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) }),
    ) as unknown as typeof fetch;
  });

  it("pinMessage POSTs /messages/{id}/pin", async () => {
    await pinMessage("m1");
    expect(fetch).toHaveBeenCalledWith(
      "/api/chat/messages/m1/pin",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("unpinMessage DELETEs", async () => {
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, status: 204 })) as unknown as typeof fetch;
    await unpinMessage("m1");
    expect(fetch).toHaveBeenCalledWith(
      "/api/chat/messages/m1/pin",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("listPins GETs and returns pins", async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve({ pins: [{ id: "m1" }] }),
    })) as unknown as typeof fetch;
    const pins = await listPins("c1");
    expect(fetch).toHaveBeenCalledWith("/api/chat/channels/c1/pins");
    expect(pins).toEqual([{ id: "m1" }]);
  });

  it("editMessage PATCHes with content", async () => {
    await editMessage("m1", "new text");
    expect(fetch).toHaveBeenCalledWith(
      "/api/chat/messages/m1",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ content: "new text" }),
      }),
    );
  });

  it("deleteMessage DELETEs", async () => {
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, status: 204 })) as unknown as typeof fetch;
    await deleteMessage("m1");
    expect(fetch).toHaveBeenCalledWith(
      "/api/chat/messages/m1",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("markUnread POSTs rewind endpoint", async () => {
    await markUnread("c1", "m2");
    expect(fetch).toHaveBeenCalledWith(
      "/api/chat/channels/c1/read-cursor/rewind",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ before_message_id: "m2" }),
      }),
    );
  });

  it("throws on non-OK with server error", async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false, status: 409,
      json: () => Promise.resolve({ error: "pin cap (50) reached" }),
    })) as unknown as typeof fetch;
    await expect(pinMessage("m1")).rejects.toThrow("pin cap");
  });
});
