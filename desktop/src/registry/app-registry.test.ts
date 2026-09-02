import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { AppManifest } from "./app-registry";
import { getApp, getOrRegisterServiceApp, getAllApps, getLaunchableApps, isDefaultSurfaceApp, prefetchApp, resolveApp, apps, APP_REDIRECTS, resolvePinnedId } from "./app-registry";

describe("resolveApp (deep-navigation token resolver)", () => {
  it("resolves an exact app id", () => {
    expect(resolveApp("messages")?.id).toBe("messages");
  });

  it("resolves a case-insensitive app name", () => {
    // The Activity app's id is "dashboard"; its name is "Activity".
    expect(resolveApp("Activity")?.id).toBe("dashboard");
    expect(resolveApp("activity")?.id).toBe("dashboard");
  });

  it("resolves friendly aliases", () => {
    expect(resolveApp("monitor")?.id).toBe("dashboard");
    expect(resolveApp("chat")?.id).toBe("messages");
  });

  it("trims and lowercases the token", () => {
    expect(resolveApp("  SETTINGS  ")?.id).toBe("settings");
  });

  it("returns undefined for unknown or empty tokens", () => {
    expect(resolveApp("does-not-exist")).toBeUndefined();
    expect(resolveApp("")).toBeUndefined();
    expect(resolveApp("   ")).toBeUndefined();
  });
});

describe("pwa flag", () => {
  it("messages has pwa:true", () => {
    expect(getApp("messages")?.pwa).toBe(true);
  });

  it("pwa is absent or falsy on all other apps", () => {
    const others = getAllApps().filter((a) => a.id !== "messages");
    for (const app of others) {
      expect(app.pwa, `${app.id} should not have pwa:true`).toBeFalsy();
    }
  });
});

describe("prefetchApp", () => {
  it("invokes the lazy component thunk once per app (memoized)", () => {
    const thunk = vi.fn(() => Promise.resolve({ default: () => null }));
    // Register a service app whose manifest we can spy on via getOrRegister.
    const manifest = getOrRegisterServiceApp("prefetch-memo-test", "Memo Test");
    manifest.component = thunk as typeof manifest.component;

    prefetchApp(manifest.id);
    prefetchApp(manifest.id);
    prefetchApp(manifest.id);

    expect(thunk).toHaveBeenCalledTimes(1);
  });

  it("is a no-op for unknown apps and never throws", () => {
    expect(() => prefetchApp("does-not-exist")).not.toThrow();
  });
});

describe("LoRA Studio launcher visibility", () => {
  // An `optional: true` app only renders if its id is in the backend
  // OPTIONAL_FRONTEND_APPS allowlist (routes/apps.py). LoRA Studio is not in it,
  // so marking it optional made the app invisible in the launcher while every
  // component test still passed. Lock it in as an always-on app.
  it("is launchable with no optional apps installed", () => {
    const ids = getLaunchableApps(new Set()).map((a) => a.id);
    expect(ids).toContain("lora-studio");
  });

  it("is not marked optional", () => {
    expect(getApp("lora-studio")?.optional).toBeFalsy();
  });
});

describe("file handler tiering", () => {
  const handlerIds = ["text-editor", "image-viewer", "media-player"];

  it("handler apps are absent from launcher listings", () => {
    const ids = getLaunchableApps(new Set()).map((a) => a.id);
    for (const id of handlerIds) {
      expect(ids, `handler app "${id}" should not be in launcher`).not.toContain(id);
    }
  });

  it("handler apps are still openable programmatically via getApp", () => {
    for (const id of handlerIds) {
      expect(getApp(id)?.id).toBe(id);
    }
  });

  it("handler apps are still openable programmatically via resolveApp", () => {
    for (const id of handlerIds) {
      expect(resolveApp(id)?.id).toBe(id);
    }
  });

  it("handler apps have tier 4 and handler:true in the manifest", () => {
    for (const id of handlerIds) {
      const app = getApp(id);
      expect(app?.tier).toBe(4);
      expect(app?.handler).toBe(true);
    }
  });
});

describe("isDefaultSurfaceApp (shared default-surface tier rule)", () => {
  it("includes tier 1 and tier 2 apps", () => {
    for (const id of ["messages", "models", "cluster", "secrets"]) {
      expect(isDefaultSurfaceApp(getApp(id)!), `app "${id}" should be a default-surface app`).toBe(true);
    }
  });

  it("excludes tier 3 apps (discoverable via Store/search)", () => {
    for (const id of ["providers", "mcp", "channels", "notification-archive"]) {
      expect(isDefaultSurfaceApp(getApp(id)!), `tier-3 app "${id}" should NOT be a default-surface app`).toBe(false);
    }
  });

  it("excludes tier 4 file handlers", () => {
    for (const id of ["media-player", "text-editor", "image-viewer"]) {
      const app = getApp(id)!;
      expect(app.tier).toBe(4);
      expect(app.handler).toBe(true);
      expect(isDefaultSurfaceApp(app), `tier-4 handler "${id}" should NOT be a default-surface app`).toBe(false);
    }
  });

  it("excludes optional (Store-installable) apps", () => {
    for (const id of ["reddit", "coding-studio"]) {
      expect(isDefaultSurfaceApp(getApp(id)!), `optional app "${id}" should NOT be a default-surface app`).toBe(false);
    }
  });
});

describe("getLaunchableApps tier filtering (S1 contract)", () => {
  const TIER3_ID = "test-tier3-app";
  const HANDLER_ID = "test-handler-app";
  const TIER2_ID = "test-tier2-app";
  const TIER1_ID = "test-tier1-app";

  function addFixture(app: AppManifest) {
    apps.push(app);
  }

  function removeFixture(id: string) {
    const idx = apps.findIndex((a) => a.id === id);
    if (idx !== -1) apps.splice(idx, 1);
  }

  beforeEach(() => {
    addFixture({
      id: TIER3_ID,
      name: "Tier 3",
      icon: "box",
      category: "platform",
      component: () => Promise.resolve({ default: () => null }),
      defaultSize: { w: 100, h: 100 },
      minSize: { w: 50, h: 50 },
      singleton: true,
      pinned: false,
      launchpadOrder: 999,
      tier: 3,
    });
    addFixture({
      id: HANDLER_ID,
      name: "Handler",
      icon: "box",
      category: "os",
      component: () => Promise.resolve({ default: () => null }),
      defaultSize: { w: 100, h: 100 },
      minSize: { w: 50, h: 50 },
      singleton: true,
      pinned: false,
      launchpadOrder: 999,
      handler: true,
    });
    addFixture({
      id: TIER2_ID,
      name: "Tier 2",
      icon: "box",
      category: "platform",
      component: () => Promise.resolve({ default: () => null }),
      defaultSize: { w: 100, h: 100 },
      minSize: { w: 50, h: 50 },
      singleton: true,
      pinned: false,
      launchpadOrder: 999,
      tier: 2,
      group: "TestGroup",
    });
    addFixture({
      id: TIER1_ID,
      name: "Tier 1",
      icon: "box",
      category: "platform",
      component: () => Promise.resolve({ default: () => null }),
      defaultSize: { w: 100, h: 100 },
      minSize: { w: 50, h: 50 },
      singleton: true,
      pinned: false,
      launchpadOrder: 999,
    });
  });

  afterEach(() => {
    removeFixture(TIER3_ID);
    removeFixture(HANDLER_ID);
    removeFixture(TIER2_ID);
    removeFixture(TIER1_ID);
  });

  it("excludes tier 3 apps", () => {
    const ids = getLaunchableApps(new Set()).map((a) => a.id);
    expect(ids).not.toContain(TIER3_ID);
  });

  it("excludes handler apps", () => {
    const ids = getLaunchableApps(new Set()).map((a) => a.id);
    expect(ids).not.toContain(HANDLER_ID);
  });

  it("includes tier 1 apps (apps without explicit tier)", () => {
    const ids = getLaunchableApps(new Set()).map((a) => a.id);
    expect(ids).toContain(TIER1_ID);
  });

  it("includes tier 2 apps and preserves their group", () => {
    const appsList = getLaunchableApps(new Set());
    const tier2 = appsList.find((a) => a.id === TIER2_ID);
    expect(tier2).toBeDefined();
    expect(tier2?.tier).toBe(2);
    expect(tier2?.group).toBe("TestGroup");
  });
});

describe("getLaunchableApps tier-5 optional app filtering", () => {
  it("includes installed tier-5 optional apps", () => {
    const installed = new Set(["coding-studio", "design-studio"]);
    const ids = getLaunchableApps(installed).map((a) => a.id);
    expect(ids).toContain("coding-studio");
    expect(ids).toContain("design-studio");
  });

  it("excludes non-installed tier-5 optional apps", () => {
    const installed = new Set();
    const ids = getLaunchableApps(installed).map((a) => a.id);
    expect(ids).not.toContain("coding-studio");
    expect(ids).not.toContain("design-studio");
  });
});

describe("APP_REDIRECTS", () => {
  it("is exported as a Record", () => {
    expect(APP_REDIRECTS).toBeDefined();
    expect(typeof APP_REDIRECTS).toBe("object");
  });

  it("redirects notification-archive to the notifications app", () => {
    expect(APP_REDIRECTS["notification-archive"]).toEqual({ appId: "notifications" });
  });
});

describe("resolvePinnedId", () => {
  it("returns the id for a valid app", () => {
    expect(resolvePinnedId("messages")).toBe("messages");
  });

  it("returns undefined for an unknown id", () => {
    expect(resolvePinnedId("does-not-exist")).toBeUndefined();
  });

  it("resolves a redirect to the target app id", () => {
    APP_REDIRECTS["legacy-id"] = { appId: "agents" };
    expect(resolvePinnedId("legacy-id")).toBe("agents");
    delete APP_REDIRECTS["legacy-id"];
  });

  it("returns undefined for a redirect to a non-existent app", () => {
    APP_REDIRECTS["legacy-id"] = { appId: "does-not-exist" };
    expect(resolvePinnedId("legacy-id")).toBeUndefined();
    delete APP_REDIRECTS["legacy-id"];
  });

  it("resolves notification-archive to notifications via APP_REDIRECTS", () => {
    expect(resolvePinnedId("notification-archive")).toBe("notifications");
  });
});

describe("notification-archive tier and launcher visibility", () => {
  it("has tier 3 in the manifest", () => {
    expect(getApp("notification-archive")?.tier).toBe(3);
  });

  it("is absent from launcher listings", () => {
    const ids = getLaunchableApps(new Set()).map((a) => a.id);
    expect(ids).not.toContain("notification-archive");
  });

  it("is still openable programmatically via getApp", () => {
    expect(getApp("notification-archive")?.id).toBe("notification-archive");
  });

  it("notifications app is present and launchable", () => {
    const ids = getLaunchableApps(new Set()).map((a) => a.id);
    expect(ids).toContain("notifications");
    expect(getApp("notifications")?.tier).toBeUndefined();
  });
});
