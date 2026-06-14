import { describe, it, expect, vi, afterEach } from "vitest";
import { fetchUserspaceApps, toAppManifest, installUserspaceApp, grantUserspacePermissions } from "../userspace-apps";

describe("userspace apps", () => {
  it("maps a userspace app row to an AppManifest in the 'userspace' category", () => {
    const m = toAppManifest({ app_id: "todo", name: "Todo", icon: "", app_type: "web", version: "1", enabled: 1, permissions_requested: [], permissions_granted: [] });
    expect(m.id).toBe("todo");
    expect(m.name).toBe("Todo");
    expect(m.category).toBe("userspace");
    expect(typeof m.component).toBe("function");
  });

  it("fetchUserspaceApps returns only enabled apps as manifests", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => [
      { app_id: "a", name: "A", icon: "", app_type: "web", version: "1", enabled: 1, permissions_requested: [], permissions_granted: [] },
      { app_id: "b", name: "B", icon: "", app_type: "web", version: "1", enabled: 0, permissions_requested: [], permissions_granted: [] },
    ]}));
    const apps = await fetchUserspaceApps();
    expect(apps.map(a => a.id)).toEqual(["a"]);
    vi.unstubAllGlobals();
  });

  it("fetchUserspaceApps returns [] on fetch failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    expect(await fetchUserspaceApps()).toEqual([]);
    vi.unstubAllGlobals();
  });
});

describe("installUserspaceApp", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("posts multipart to install endpoint and returns parsed InstallResult", async () => {
    const mockResult = { app_id: "todo", permissions_requested: ["app.net"], needs_consent: false, new_permissions: [] };
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, json: async () => mockResult });
    vi.stubGlobal("fetch", mockFetch);

    const file = new File(["data"], "todo.taosapp");
    const result = await installUserspaceApp(file);

    expect(result).toEqual(mockResult);
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/userspace-apps/install",
      expect.objectContaining({ method: "POST", credentials: "include" })
    );
  });

  it("throws with server error string when res.ok is false", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 400, json: async () => ({ error: "bundle too large" }) }));

    const file = new File(["data"], "todo.taosapp");
    await expect(installUserspaceApp(file)).rejects.toThrow("bundle too large");
  });
});

describe("grantUserspacePermissions", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("posts JSON granted list to the permissions URL with credentials include", async () => {
    const mockFetch = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", mockFetch);

    await grantUserspacePermissions("todo", ["app.net"]);

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/userspace-apps/todo/permissions",
      expect.objectContaining({ method: "POST", credentials: "include" })
    );
    const callArgs = mockFetch.mock.calls[0][1];
    expect(JSON.parse(callArgs.body)).toEqual({ granted: ["app.net"] });
  });
});
