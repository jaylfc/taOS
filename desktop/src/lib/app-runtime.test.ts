import { describe, expect, it } from "vitest";
import { appRuntime, shellWindowRuntime } from "./app-runtime";
import { toAppManifest } from "./userspace-apps";
import type { AppManifest } from "@/registry/app-registry";

function manifest(overrides: Partial<AppManifest> = {}): AppManifest {
  return {
    id: "x",
    name: "X",
    icon: "circle",
    category: "platform",
    component: () => import("@/apps/NotesApp").then((m) => ({ default: m.NotesApp })),
    defaultSize: { w: 1, h: 1 },
    minSize: { w: 1, h: 1 },
    singleton: true,
    pinned: false,
    launchpadOrder: 1,
    ...overrides,
  };
}

function row(overrides: Record<string, unknown> = {}) {
  return {
    app_id: "app",
    name: "App",
    icon: "icon.png",
    app_type: "web",
    version: "1.0.0",
    enabled: 1,
    permissions_requested: [],
    permissions_granted: [],
    ...overrides,
  };
}

describe("appRuntime", () => {
  it("defaults every built-in manifest to taos", () => {
    expect(appRuntime(manifest())).toBe("taos");
  });

  it("honours an explicit runtime", () => {
    expect(appRuntime(manifest({ runtime: "linux" }))).toBe("linux");
    expect(appRuntime(manifest({ runtime: "agent" }))).toBe("agent");
  });

  it("explicit runtime wins over a service: id", () => {
    expect(appRuntime(manifest({ id: "service:foo", runtime: "web" }))).toBe("web");
  });

  it("classifies service: ids as container", () => {
    expect(appRuntime(manifest({ id: "service:foo" }))).toBe("container");
  });

  it("classifies userspace: ids as taos (their runtime is set by toAppManifest)", () => {
    expect(appRuntime(manifest({ id: "userspace:foo" }))).toBe("taos");
  });
});

describe("shellWindowRuntime", () => {
  it("classifies waydroid windows as android", () => {
    expect(shellWindowRuntime("waydroid.com.termux")).toBe("android");
  });

  it("classifies chrome- windows as taos", () => {
    expect(shellWindowRuntime("chrome-messages")).toBe("taos");
  });

  it("classifies anything else as linux", () => {
    expect(shellWindowRuntime("firefox")).toBe("linux");
    expect(shellWindowRuntime("alacritty")).toBe("linux");
  });
});

describe("toAppManifest runtime mapping", () => {
  it("maps a web row to a web runtime", () => {
    expect(toAppManifest(row({ app_type: "web" })).runtime).toBe("web");
  });

  it("maps a container row to a container runtime", () => {
    expect(toAppManifest(row({ app_type: "container" })).runtime).toBe("container");
  });

  it("ai-generated provenance wins over a container app_type", () => {
    expect(
      toAppManifest({
        ...row({ app_type: "container" }),
        provenance: "ai-generated",
      }).runtime,
    ).toBe("agent");
  });

  it("ai-generated provenance wins over a web app_type", () => {
    expect(
      toAppManifest({
        ...row({ app_type: "web" }),
        provenance: "ai-generated",
      }).runtime,
    ).toBe("agent");
  });
});