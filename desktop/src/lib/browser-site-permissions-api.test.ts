import { afterEach, describe, expect, it, vi } from "vitest";
import { listSitePermissions, revokeSitePermission } from "./browser-site-permissions-api";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("listSitePermissions", () => {
  it("returns grants array on 200", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        grants: [
          { host_pattern: "https://a.test/*", permission: "camera", state: "allow" },
          { host_pattern: "https://b.test/*", permission: "microphone", state: "deny" },
        ],
      }),
    });

    const result = await listSitePermissions("profile-1");
    expect(result).toHaveLength(2);
    expect(result[0].host_pattern).toBe("https://a.test/*");
    expect(result[0].permission).toBe("camera");
    expect(result[0].state).toBe("allow");
    expect(result[1].state).toBe("deny");
  });

  it("throws on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    await expect(listSitePermissions("profile-1")).rejects.toThrow("HTTP 500");
  });

  it("throws on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("network failure"));
    await expect(listSitePermissions("profile-1")).rejects.toThrow("network failure");
  });

  it("returns [] when body.grants is not an array", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ grants: null }),
    });
    expect(await listSitePermissions("profile-1")).toEqual([]);
  });

  it("includes credentials and profile_id param", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ grants: [] }),
    });
    global.fetch = fetchMock;
    await listSitePermissions("profile-1");
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("profile_id=profile-1");
    expect(opts.credentials).toBe("include");
  });

  it("encodes profile_id with spaces", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ grants: [] }),
    });
    global.fetch = fetchMock;
    await listSitePermissions("my profile");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("profile_id=my+profile");
  });
});

describe("revokeSitePermission", () => {
  it("returns true on 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    expect(await revokeSitePermission("profile-1", "https://a.test/*", "camera")).toBe(true);

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/desktop/browser/site-permissions");
    expect(url).toContain("profile_id=profile-1");
    expect(url).toContain("host_pattern=https%3A%2F%2Fa.test%2F*");
    expect(url).toContain("permission=camera");
    expect(opts.method).toBe("DELETE");
  });

  it("returns false on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 404 });
    expect(await revokeSitePermission("profile-1", "https://a.test/*", "camera")).toBe(false);
  });

  it("returns false on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await revokeSitePermission("profile-1", "https://a.test/*", "camera")).toBe(false);
  });

  it("includes credentials", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;
    await revokeSitePermission("profile-1", "https://a.test/*", "camera");
    const [, opts] = fetchMock.mock.calls[0];
    expect(opts.credentials).toBe("include");
  });
});
