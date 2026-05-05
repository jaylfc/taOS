import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  listCapabilities,
  grantCapability,
  revokeCapability,
} from "./browser-capability-api";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("browser-capability-api", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
  });

  describe("listCapabilities", () => {
    it("returns grants array on 200", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          grants: [
            {
              agent_id: "agent-1",
              host_pattern: "example.com",
              permissions: "drive",
              granted_at: "2026-01-01T00:00:00Z",
              expires_at: null,
            },
          ],
        }),
      });
      const result = await listCapabilities("profile-1");
      expect(result).toHaveLength(1);
      expect(result[0].agent_id).toBe("agent-1");
      expect(result[0].host_pattern).toBe("example.com");
    });

    it("returns [] on 401", async () => {
      fetchMock.mockResolvedValue({ ok: false, status: 401 });
      expect(await listCapabilities("profile-1")).toEqual([]);
    });

    it("returns [] on network error", async () => {
      fetchMock.mockRejectedValue(new Error("network failure"));
      expect(await listCapabilities("profile-1")).toEqual([]);
    });

    it("includes agent_id query param when supplied", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        json: async () => ({ grants: [] }),
      });
      await listCapabilities("profile-1", "agent-42");
      const [url] = fetchMock.mock.calls[0];
      expect(url).toContain("agent_id=agent-42");
    });

    it("includes credentials", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        json: async () => ({ grants: [] }),
      });
      await listCapabilities("profile-1");
      const [, opts] = fetchMock.mock.calls[0];
      expect(opts.credentials).toBe("include");
    });
  });

  describe("grantCapability", () => {
    it("returns { granted: true } on 200", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ granted: true }),
      });
      const result = await grantCapability("profile-1", "agent-1", "example.com", "drive");
      expect(result).toEqual({ granted: true });
    });

    it("returns { error } on 400", async () => {
      fetchMock.mockResolvedValue({
        ok: false,
        status: 400,
        json: async () => ({ error: "invalid host pattern" }),
      });
      const result = await grantCapability("profile-1", "agent-1", "bad pattern", "drive");
      expect(result).toEqual({ error: "invalid host pattern" });
    });

    it("returns null on 401", async () => {
      fetchMock.mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({}),
      });
      expect(await grantCapability("profile-1", "agent-1", "example.com", "drive")).toBeNull();
    });

    it("returns null on network error", async () => {
      fetchMock.mockRejectedValue(new Error("offline"));
      expect(await grantCapability("profile-1", "agent-1", "example.com", "drive")).toBeNull();
    });

    it("posts JSON body with all fields", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        json: async () => ({ granted: true }),
      });
      await grantCapability(
        "profile-1",
        "agent-1",
        "example.com",
        "drive",
        "2026-12-31T00:00:00Z",
      );
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toBe("/api/desktop/browser/capabilities");
      expect(opts.method).toBe("POST");
      expect(opts.headers["content-type"]).toBe("application/json");
      const body = JSON.parse(opts.body);
      expect(body.profile_id).toBe("profile-1");
      expect(body.agent_id).toBe("agent-1");
      expect(body.host_pattern).toBe("example.com");
      expect(body.permissions).toBe("drive");
      expect(body.expires_at).toBe("2026-12-31T00:00:00Z");
    });

    it("posts expires_at: null when expiresAt arg is null", async () => {
      fetchMock.mockResolvedValue({
        ok: true,
        json: async () => ({ granted: true }),
      });
      await grantCapability("profile-1", "agent-1", "example.com", "drive", null);
      const [, opts] = fetchMock.mock.calls[0];
      const body = JSON.parse(opts.body);
      expect(body.expires_at).toBeNull();
    });
  });

  describe("revokeCapability", () => {
    it("returns true on 204", async () => {
      fetchMock.mockResolvedValue({ ok: true, status: 204 });
      const result = await revokeCapability("profile-1", "agent-1", "example.com");
      expect(result).toBe(true);
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toContain("/api/desktop/browser/capabilities");
      expect(opts.method).toBe("DELETE");
    });

    it("returns false on 401", async () => {
      fetchMock.mockResolvedValue({ ok: false, status: 401 });
      expect(await revokeCapability("profile-1", "agent-1", "example.com")).toBe(false);
    });

    it("returns false on network error", async () => {
      fetchMock.mockRejectedValue(new Error("offline"));
      expect(await revokeCapability("profile-1", "agent-1", "example.com")).toBe(false);
    });

    it("encodes URL params correctly", async () => {
      fetchMock.mockResolvedValue({ ok: true, status: 204 });
      await revokeCapability("my profile", "agent/1", "example.com");
      const [url] = fetchMock.mock.calls[0];
      expect(url).toContain("profile_id=my+profile");
      expect(url).toContain("agent_id=agent%2F1");
      expect(url).toContain("host_pattern=example.com");
    });
  });
});
