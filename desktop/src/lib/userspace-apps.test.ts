import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchUserspaceApps,
  grantUserspacePermissions,
  installUserspaceApp,
  toAppManifest,
  USERSPACE_APPS_CHANGED,
} from "./userspace-apps";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("USERSPACE_APPS_CHANGED", () => {
  it("is the expected event name", () => {
    expect(USERSPACE_APPS_CHANGED).toBe("taos:userspace-apps-changed");
  });
});

describe("toAppManifest", () => {
  it("maps a row with trust to a manifest with userspace: prefix", () => {
    const manifest = toAppManifest({
      app_id: "my-app",
      name: "My App",
      icon: "icon.png",
      app_type: "web",
      version: "1.0.0",
      enabled: 1,
      permissions_requested: ["camera"],
      permissions_granted: [],
      trust: "community",
    });
    expect(manifest.id).toBe("userspace:my-app");
    expect(manifest.name).toBe("My App");
    expect(manifest.category).toBe("userspace");
    expect(manifest.icon).toBe("layout-grid");
    expect(manifest.singleton).toBe(true);
    expect(manifest.pinned).toBe(false);
  });

  it("defaults trust to community when omitted", () => {
    const manifest = toAppManifest({
      app_id: "no-trust",
      name: "No Trust",
      icon: "icon.png",
      app_type: "container",
      version: "0.1.0",
      enabled: 1,
      permissions_requested: [],
      permissions_granted: [],
    });
    expect(manifest.id).toBe("userspace:no-trust");
  });
});

describe("installUserspaceApp", () => {
  it("POSTs FormData to /api/userspace-apps/install and returns parsed result", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        app_id: "new-app",
        permissions_requested: ["camera"],
        needs_consent: true,
        new_permissions: ["camera"],
      }),
    });
    global.fetch = fetchMock;

    const file = new File(["content"], "app.zip", { type: "application/zip" });
    const result = await installUserspaceApp(file);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/userspace-apps/install");
    expect(opts.method).toBe("POST");
    expect(opts.credentials).toBe("include");
    expect(opts.body).toBeInstanceOf(FormData);
    expect(result.app_id).toBe("new-app");
    expect(result.needs_consent).toBe(true);
    expect(result.new_permissions).toEqual(["camera"]);
  });

  it("throws with body.error on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: "invalid package" }),
    });

    const file = new File(["bad"], "bad.zip");
    await expect(installUserspaceApp(file)).rejects.toThrow("invalid package");
  });

  it("throws with status message when error body is non-JSON", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => {
        throw new Error("not json");
      },
    });

    const file = new File(["x"], "x.zip");
    await expect(installUserspaceApp(file)).rejects.toThrow("install failed (500)");
  });
});

describe("grantUserspacePermissions", () => {
  it("POSTs JSON to /api/userspace-apps/{appId}/permissions", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
    });
    global.fetch = fetchMock;

    await grantUserspacePermissions("my-app", ["camera", "mic"]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/userspace-apps/my-app/permissions");
    expect(opts.method).toBe("POST");
    expect(opts.credentials).toBe("include");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    const body = JSON.parse(opts.body);
    expect(body.granted).toEqual(["camera", "mic"]);
  });

  it("encodes appId in the URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
    });
    global.fetch = fetchMock;

    await grantUserspacePermissions("app/with/slashes", []);

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("app%2Fwith%2Fslashes");
  });

  it("throws on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
    });

    await expect(grantUserspacePermissions("my-app", ["camera"]))
      .rejects.toThrow("granting permissions failed (403)");
  });
});

describe("fetchUserspaceApps", () => {
  it("GETs /api/userspace-apps and returns manifests for enabled apps", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [
        {
          app_id: "enabled-app",
          name: "Enabled",
          icon: "icon.png",
          app_type: "web",
          version: "1.0.0",
          enabled: 1,
          permissions_requested: [],
          permissions_granted: [],
        },
        {
          app_id: "disabled-app",
          name: "Disabled",
          icon: "icon.png",
          app_type: "web",
          version: "1.0.0",
          enabled: 0,
          permissions_requested: [],
          permissions_granted: [],
        },
      ],
    });
    global.fetch = fetchMock;

    const result = await fetchUserspaceApps();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/userspace-apps");
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("userspace:enabled-app");
    expect(result[0].name).toBe("Enabled");
  });

  it("returns [] on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
    });

    const result = await fetchUserspaceApps();
    expect(result).toEqual([]);
  });

  it("returns [] on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("network failure"));

    const result = await fetchUserspaceApps();
    expect(result).toEqual([]);
  });
});
