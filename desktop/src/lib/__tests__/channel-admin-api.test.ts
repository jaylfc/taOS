import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  patchChannel,
  addChannelMember,
  removeChannelMember,
  muteAgent,
  unmuteAgent,
} from "../channel-admin-api";

describe("channel-admin-api", () => {
  beforeEach(() => {
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) }),
    ) as unknown as typeof fetch;
  });

  it("patchChannel PATCHes with the provided body", async () => {
    await patchChannel("c1", { response_mode: "lively" });
    expect(fetch).toHaveBeenCalledWith(
      "/api/chat/channels/c1",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ response_mode: "lively" }),
      }),
    );
  });

  it("addChannelMember POSTs action=add", async () => {
    await addChannelMember("c1", "tom");
    expect(fetch).toHaveBeenCalledWith(
      "/api/chat/channels/c1/members",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ action: "add", slug: "tom" }),
      }),
    );
  });

  it("removeChannelMember POSTs action=remove", async () => {
    await removeChannelMember("c1", "tom");
    const call = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.at(-1)!;
    expect(call[0]).toBe("/api/chat/channels/c1/members");
    expect(call[1]).toMatchObject({ body: JSON.stringify({ action: "remove", slug: "tom" }) });
  });

  it("muteAgent / unmuteAgent hit the muted endpoint", async () => {
    await muteAgent("c1", "tom");
    expect((fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.at(-1)![0])
      .toBe("/api/chat/channels/c1/muted");

    await unmuteAgent("c1", "tom");
    const last = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.at(-1)!;
    expect(last[1]).toMatchObject({ body: JSON.stringify({ action: "remove", slug: "tom" }) });
  });

  it("throws with the server's error on non-OK response", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false, status: 400,
        json: () => Promise.resolve({ error: "max_hops must be 1..10" }),
      }),
    ) as unknown as typeof fetch;
    await expect(patchChannel("c1", { max_hops: 99 })).rejects.toThrow("max_hops must be 1..10");
  });
});
