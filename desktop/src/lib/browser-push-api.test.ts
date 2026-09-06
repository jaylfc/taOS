import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  getVapidPublicKey,
  subscribePush,
  listPushSubscriptions,
  unsubscribePush,
  listPushMutes,
  setPushMute,
} from "./browser-push-api";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("browser-push-api", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
  });

  // ---------------------------------------------------------------------------
  // getVapidPublicKey
  // ---------------------------------------------------------------------------
  describe("getVapidPublicKey", () => {
    it("extracts public_key from response body on 200", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ public_key: "BFoo123" }),
      });
      const key = await getVapidPublicKey();
      expect(key).toBe("BFoo123");
    });

    it("hits the correct URL with credentials", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        json: async () => ({ public_key: "BFoo123" }),
      });
      await getVapidPublicKey();
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toBe("/api/desktop/browser/push/vapid-public-key");
      expect(opts.credentials).toBe("include");
    });

    it("throws on 401", async () => {
      fetchMock.mockResolvedValue({ ok: false, status: 401 });
      await expect(getVapidPublicKey()).rejects.toThrow("HTTP 401");
    });

    it("throws when public_key is missing", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        json: async () => ({ other: "stuff" }),
      });
      await expect(getVapidPublicKey()).rejects.toThrow(/Missing public_key/);
    });

    it("throws on network error", async () => {
      fetchMock.mockRejectedValue(new Error("offline"));
      await expect(getVapidPublicKey()).rejects.toThrow("offline");
    });
  });

  // ---------------------------------------------------------------------------
  // subscribePush
  // ---------------------------------------------------------------------------
  describe("subscribePush", () => {
    const ARGS = {
      device_id: "dev-1",
      endpoint: "https://push.example.com/1",
      p256dh_key: "p256dhKey==",
      auth_key: "authKey==",
      user_agent: "TestBrowser/1.0",
    };

    it("returns { ok: true } on 200", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ ok: true }),
      });
      const result = await subscribePush(ARGS);
      expect(result).toEqual({ ok: true });
    });

    it("posts to correct URL with JSON body and credentials", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        json: async () => ({ ok: true }),
      });
      await subscribePush(ARGS);
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toBe("/api/desktop/browser/push/subscribe");
      expect(opts.method).toBe("POST");
      expect(opts.credentials).toBe("include");
      expect(opts.headers["content-type"]).toBe("application/json");
      const body = JSON.parse(opts.body);
      expect(body.device_id).toBe("dev-1");
      expect(body.endpoint).toBe("https://push.example.com/1");
      expect(body.p256dh_key).toBe("p256dhKey==");
      expect(body.auth_key).toBe("authKey==");
    });

    it("throws on 401", async () => {
      fetchMock.mockResolvedValue({ ok: false, status: 401 });
      await expect(subscribePush(ARGS)).rejects.toThrow("HTTP 401");
    });

    it("throws on 400", async () => {
      fetchMock.mockResolvedValue({ ok: false, status: 400 });
      await expect(subscribePush(ARGS)).rejects.toThrow("HTTP 400");
    });

    it("throws on network error", async () => {
      fetchMock.mockRejectedValue(new Error("offline"));
      await expect(subscribePush(ARGS)).rejects.toThrow("offline");
    });
  });

  // ---------------------------------------------------------------------------
  // listPushSubscriptions
  // ---------------------------------------------------------------------------
  describe("listPushSubscriptions", () => {
    it("returns subscriptions array from subscriptions key on 200", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          subscriptions: [
            {
              device_id: "dev-1",
              endpoint: "https://push.example.com/1",
              user_agent: "Mozilla/5.0",
              created_at: 1000,
              last_seen_at: 2000,
            },
          ],
        }),
      });
      const result = await listPushSubscriptions();
      expect(result).toHaveLength(1);
      expect(result[0].device_id).toBe("dev-1");
      expect(result[0].endpoint).toBe("https://push.example.com/1");
      expect(result[0].last_seen_at).toBe(2000);
    });

    it("returns [] on 401", async () => {
      fetchMock.mockResolvedValue({ ok: false, status: 401 });
      expect(await listPushSubscriptions()).toEqual([]);
    });

    it("returns [] on network error", async () => {
      fetchMock.mockRejectedValue(new Error("offline"));
      expect(await listPushSubscriptions()).toEqual([]);
    });

    it("returns [] when subscriptions key is not an array", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        json: async () => ({ subscriptions: null }),
      });
      expect(await listPushSubscriptions()).toEqual([]);
    });

    it("uses credentials", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        json: async () => ({ subscriptions: [] }),
      });
      await listPushSubscriptions();
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toBe("/api/desktop/browser/push/subscriptions");
      expect(opts.credentials).toBe("include");
    });
  });

  // ---------------------------------------------------------------------------
  // unsubscribePush
  // ---------------------------------------------------------------------------
  describe("unsubscribePush", () => {
    it("returns { ok: true } on 200", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ ok: true }),
      });
      expect(await unsubscribePush("dev-1")).toEqual({ ok: true });
    });

    it("encodes device_id in the URL path", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        json: async () => ({ ok: true }),
      });
      await unsubscribePush("dev/with/slashes");
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toContain("dev%2Fwith%2Fslashes");
      expect(opts.method).toBe("DELETE");
    });

    it("returns { ok: false } on 401", async () => {
      fetchMock.mockResolvedValue({ ok: false, status: 401 });
      expect(await unsubscribePush("dev-1")).toEqual({ ok: false });
    });

    it("returns { ok: false } on network error", async () => {
      fetchMock.mockRejectedValue(new Error("offline"));
      expect(await unsubscribePush("dev-1")).toEqual({ ok: false });
    });
  });

  // ---------------------------------------------------------------------------
  // listPushMutes
  // ---------------------------------------------------------------------------
  describe("listPushMutes", () => {
    it("returns mutes array from mutes key on 200", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          mutes: [
            { agent_id: "agent-1", kind: "chat", muted_at: 1000 },
            { agent_id: "agent-1", kind: "drive-started", muted_at: 2000 },
          ],
        }),
      });
      const result = await listPushMutes();
      expect(result).toHaveLength(2);
      expect(result[0].agent_id).toBe("agent-1");
      expect(result[0].kind).toBe("chat");
      expect(result[1].kind).toBe("drive-started");
    });

    it("returns [] on 401", async () => {
      fetchMock.mockResolvedValue({ ok: false, status: 401 });
      expect(await listPushMutes()).toEqual([]);
    });

    it("returns [] on network error", async () => {
      fetchMock.mockRejectedValue(new Error("offline"));
      expect(await listPushMutes()).toEqual([]);
    });

    it("returns [] when mutes key is not an array", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        json: async () => ({ mutes: null }),
      });
      expect(await listPushMutes()).toEqual([]);
    });

    it("uses credentials", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        json: async () => ({ mutes: [] }),
      });
      await listPushMutes();
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toBe("/api/desktop/browser/push/mutes");
      expect(opts.credentials).toBe("include");
    });
  });

  // ---------------------------------------------------------------------------
  // setPushMute
  // ---------------------------------------------------------------------------
  describe("setPushMute", () => {
    it("returns { ok: true } on 200", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ ok: true }),
      });
      const result = await setPushMute({ agent_id: "agent-1", kind: "chat", muted: true });
      expect(result).toEqual({ ok: true });
    });

    it("sends PUT to correct URL with JSON body", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        json: async () => ({ ok: true }),
      });
      await setPushMute({ agent_id: "agent-1", kind: "drive-started", muted: false });
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toBe("/api/desktop/browser/push/mutes");
      expect(opts.method).toBe("PUT");
      expect(opts.credentials).toBe("include");
      expect(opts.headers["content-type"]).toBe("application/json");
      const body = JSON.parse(opts.body);
      expect(body.agent_id).toBe("agent-1");
      expect(body.kind).toBe("drive-started");
      expect(body.muted).toBe(false);
    });

    it("throws on 401", async () => {
      fetchMock.mockResolvedValue({ ok: false, status: 401 });
      await expect(
        setPushMute({ agent_id: "agent-1", kind: "chat", muted: true }),
      ).rejects.toThrow("HTTP 401");
    });

    it("throws on 400", async () => {
      fetchMock.mockResolvedValue({ ok: false, status: 400 });
      await expect(
        setPushMute({ agent_id: "agent-1", kind: "chat", muted: true }),
      ).rejects.toThrow("HTTP 400");
    });

    it("throws on network error", async () => {
      fetchMock.mockRejectedValue(new Error("offline"));
      await expect(
        setPushMute({ agent_id: "agent-1", kind: "chat", muted: true }),
      ).rejects.toThrow("offline");
    });
  });
});
