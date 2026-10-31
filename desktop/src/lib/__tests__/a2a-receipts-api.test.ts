import { describe, it, expect, vi, beforeEach } from "vitest";
import { getReceipts, markSeen, type Receipt } from "../a2a-receipts-api";

describe("a2a-receipts-api", () => {
  beforeEach(() => {
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) }),
    ) as unknown as typeof fetch;
  });

  it("getReceipts hits /api/chat/messages/<id>/receipts and returns body.receipts", async () => {
    const mockReceipts: Receipt[] = [
      { message_id: "m1", agent_id: "a1", reader_type: "agent", delivered_at: 1000, seen_at: 2000 },
      { message_id: "m1", agent_id: "a2", reader_type: "user", delivered_at: 1500, seen_at: null },
    ];
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ receipts: mockReceipts }),
      }),
    ) as unknown as typeof fetch;

    const result = await getReceipts("m1");

    expect(fetch).toHaveBeenCalledWith("/api/chat/messages/m1/receipts");
    expect(result).toEqual(mockReceipts);
  });

  it("getReceipts returns empty array when receipts missing", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({}),
      }),
    ) as unknown as typeof fetch;

    const result = await getReceipts("m1");

    expect(result).toEqual([]);
  });

  it("markSeen sends PATCH /api/chat/receipts with {message_id}", async () => {
    await markSeen("m1");

    expect(fetch).toHaveBeenCalledWith(
      "/api/chat/receipts",
      expect.objectContaining({
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message_id: "m1" }),
      }),
    );
  });

  it("throws on non-OK response with body error", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false,
        status: 404,
        json: () => Promise.resolve({ error: "not found" }),
      }),
    ) as unknown as typeof fetch;

    await expect(getReceipts("m1")).rejects.toThrow("not found");
  });

  it("markSeen throws on non-OK response with body error", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false,
        status: 400,
        json: () => Promise.resolve({ error: "bad request" }),
      }),
    ) as unknown as typeof fetch;

    await expect(markSeen("m1")).rejects.toThrow("bad request");
  });

  it("throws on non-OK with fallback to HTTP status when no error in body", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false,
        status: 500,
        json: () => Promise.resolve({}),
      }),
    ) as unknown as typeof fetch;

    await expect(getReceipts("m1")).rejects.toThrow("HTTP 500");
  });
});