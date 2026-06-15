import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fetchServerNotifications,
  markServerRead,
  markAllServerRead,
  sourceToTarget,
} from "./server-notifications";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("server-notifications", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
  });

  describe("fetchServerNotifications", () => {
    it("maps backend rows: seconds->ms, srv- prefix, message->body, source fallback", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        headers: { get: () => "application/json" },
        json: async () => [
          { id: 7, timestamp: 1000, level: "warning", title: "Disk low", message: "90% full", read: false, source: "disk_quota" },
          { id: 8, timestamp: 2000, level: "info", title: "Worker", message: "joined", read: true, source: "" },
        ],
      });

      const items = await fetchServerNotifications();

      expect(items).toHaveLength(2);
      expect(items[0]).toMatchObject({
        id: "srv-7",
        source: "disk_quota",
        title: "Disk low",
        body: "90% full",
        level: "warning",
        read: false,
        timestamp: 1_000_000,
      });
      // Empty source falls back to "system".
      expect(items[1].id).toBe("srv-8");
      expect(items[1].source).toBe("system");
      expect(items[1].timestamp).toBe(2_000_000);
    });

    it("maps an unknown level to info", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        headers: { get: () => "application/json" },
        json: async () => [
          { id: 1, timestamp: 5, level: "critical", title: "X", message: "y", read: false, source: "system" },
        ],
      });
      const items = await fetchServerNotifications();
      expect(items[0].level).toBe("info");
    });

    it("returns [] when the request fails (non-ok)", async () => {
      fetchMock.mockResolvedValue({ ok: false, headers: { get: () => "application/json" }, json: async () => [] });
      expect(await fetchServerNotifications()).toEqual([]);
    });

    it("returns [] when fetch rejects", async () => {
      fetchMock.mockRejectedValue(new Error("network down"));
      expect(await fetchServerNotifications()).toEqual([]);
    });

    it("returns [] when the body is not JSON", async () => {
      fetchMock.mockResolvedValue({ ok: true, headers: { get: () => "text/html" }, json: async () => "<div/>" });
      expect(await fetchServerNotifications()).toEqual([]);
    });

    it("returns [] when the JSON body is not an array", async () => {
      fetchMock.mockResolvedValue({ ok: true, headers: { get: () => "application/json" }, json: async () => ({ oops: 1 }) });
      expect(await fetchServerNotifications()).toEqual([]);
    });

    it("never sends an hx-request header (gets JSON back)", async () => {
      fetchMock.mockResolvedValue({ ok: true, headers: { get: () => "application/json" }, json: async () => [] });
      await fetchServerNotifications();
      const [, init] = fetchMock.mock.calls[0];
      const headers = (init?.headers ?? {}) as Record<string, string>;
      expect(Object.keys(headers).map((k) => k.toLowerCase())).not.toContain("hx-request");
    });
  });

  describe("sourceToTarget", () => {
    it("routes update/lifecycle to the Settings updates section", () => {
      expect(sourceToTarget("system.update")).toEqual({ action: "settings", meta: { section: "updates" } });
      expect(sourceToTarget("system.lifecycle")).toEqual({ action: "settings", meta: { section: "updates" } });
    });

    it("routes disk_quota to the Settings storage section", () => {
      expect(sourceToTarget("disk_quota")).toEqual({ action: "settings", meta: { section: "storage" } });
    });

    it("routes worker/backend events to the Cluster app with no meta", () => {
      for (const src of ["worker.join", "worker.online", "worker.leave", "backend.up", "backend.down"]) {
        expect(sourceToTarget(src)).toEqual({ action: "cluster" });
      }
    });

    it("routes training events to the Agents app", () => {
      expect(sourceToTarget("training.complete")).toEqual({ action: "agents" });
      expect(sourceToTarget("training.failed")).toEqual({ action: "agents" });
    });

    it("routes app events to the Store app", () => {
      expect(sourceToTarget("app.installed")).toEqual({ action: "store" });
      expect(sourceToTarget("app.failed")).toEqual({ action: "store" });
    });

    it("returns no action for unknown sources", () => {
      expect(sourceToTarget("something.else")).toEqual({});
      expect(sourceToTarget("")).toEqual({});
    });
  });

  describe("mapRow action/meta wiring", () => {
    it("sets action and meta from the source mapping", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        headers: { get: () => "application/json" },
        json: async () => [
          { id: 1, timestamp: 1, level: "warning", title: "Disk", message: "full", read: false, source: "disk_quota" },
          { id: 2, timestamp: 2, level: "info", title: "Worker", message: "joined", read: false, source: "worker.join" },
          { id: 3, timestamp: 3, level: "info", title: "Other", message: "x", read: false, source: "mystery" },
        ],
      });
      const items = await fetchServerNotifications();
      expect(items[0]).toMatchObject({ action: "settings", meta: { section: "storage" } });
      expect(items[1].action).toBe("cluster");
      expect(items[1].meta).toBeUndefined();
      expect(items[2].action).toBeUndefined();
      expect(items[2].meta).toBeUndefined();
    });
  });

  describe("markServerRead", () => {
    it("POSTs the numeric id for srv- ids", async () => {
      fetchMock.mockResolvedValue({ ok: true });
      await markServerRead("srv-42");
      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toBe("/api/notifications/42/read");
      expect(init?.method).toBe("POST");
    });

    it("is a no-op for client (notif-) ids", async () => {
      await markServerRead("notif-3");
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it("swallows fetch failures", async () => {
      fetchMock.mockRejectedValue(new Error("boom"));
      await expect(markServerRead("srv-1")).resolves.toBeUndefined();
    });
  });

  describe("markAllServerRead", () => {
    it("POSTs read-all", async () => {
      fetchMock.mockResolvedValue({ ok: true });
      await markAllServerRead();
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toBe("/api/notifications/read-all");
      expect(init?.method).toBe("POST");
    });
  });
});
