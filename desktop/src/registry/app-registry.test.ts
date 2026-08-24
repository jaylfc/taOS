import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { getApp, getOrRegisterServiceApp, getAllApps, getLaunchableApps, prefetchApp, resolveApp, addApp, removeApp, APP_REDIRECTS, resolveAppRedirect } from "./app-registry";
import type { AppManifest } from "./app-registry";

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

const S1_FIXTURE_IDS = ["s1-test-tier-3", "s1-test-handler", "s1-test-tier-5"];

function makeFixture(overrides: Partial<AppManifest>): AppManifest {
  return {
    id: "fixture-default",
    name: "Fixture App",
    icon: "test-tube",
    category: "platform",
    component: (() => Promise.resolve({ default: (() => null) as never })) as never,
    defaultSize: { w: 400, h: 300 },
    minSize: { w: 300, h: 200 },
    singleton: false,
    pinned: false,
    launchpadOrder: 99,
    ...overrides,
  };
}

describe("launcher tier filtering (S1 contract #2143)", () => {
  beforeEach(() => {
    addApp(makeFixture({ id: "s1-test-tier-3", tier: 3 }));
    addApp(makeFixture({ id: "s1-test-handler", handler: true }));
    addApp(makeFixture({ id: "s1-test-tier-5", tier: 5, optional: true }));
  });

  afterEach(() => {
    for (const id of S1_FIXTURE_IDS) removeApp(id);
  });

  it("excludes tier 3 apps from the launcher", () => {
    const ids = getLaunchableApps(new Set()).map((a) => a.id);
    expect(ids, "tier 3 apps must not appear in the launcher").not.toContain("s1-test-tier-3");
  });

  it("excludes handler apps from the launcher", () => {
    const ids = getLaunchableApps(new Set()).map((a) => a.id);
    expect(ids, "handler apps must not appear in the launcher").not.toContain("s1-test-handler");
  });

  it("excludes tier 5 (Store-optional) apps even when marked installed", () => {
    const ids = getLaunchableApps(new Set(["s1-test-tier-5"])).map((a) => a.id);
    expect(ids).not.toContain("s1-test-tier-5");
  });

  it("includes tier 1 (default) and tier 2 apps", () => {
    const ids = getLaunchableApps(new Set()).map((a) => a.id);
    expect(ids).toContain("messages");
    expect(ids).toContain("models");
    expect(ids).toContain("cluster");
  });

  it("apps without an explicit tier default to tier 1 and remain launchable", () => {
    const app = getApp("messages");
    expect(app?.tier).toBeUndefined();
    const ids = getLaunchableApps(new Set()).map((a) => a.id);
    expect(ids).toContain("messages");
  });

  it("tier 2 entries carry their group", () => {
    const launchable = getLaunchableApps(new Set());
    const tier2 = launchable.find((a) => a.id === "models");
    expect(tier2).toBeDefined();
    expect(tier2?.tier).toBe(2);
    expect(tier2?.group).toBe("System");
  });

  it("existing tier 3 registry apps are also excluded after the fix", () => {
    const ids = getLaunchableApps(new Set()).map((a) => a.id);
    expect(ids).not.toContain("providers");
    expect(ids).not.toContain("mcp");
    expect(ids).not.toContain("channels");
  });
});

describe("APP_REDIRECTS and pin-restore resolution", () => {
  beforeEach(() => {
    APP_REDIRECTS["s1-retired-app"] = { appId: "messages" };
    APP_REDIRECTS["s1-orphan-redirect"] = { appId: "s1-ghost-target" };
  });

  afterEach(() => {
    delete APP_REDIRECTS["s1-retired-app"];
    delete APP_REDIRECTS["s1-orphan-redirect"];
  });

  it("resolves a redirected pin to the target app id", () => {
    expect(resolveAppRedirect("s1-retired-app")).toBe("messages");
  });

  it("keeps a pin for a registered app with no redirect entry", () => {
    expect(resolveAppRedirect("messages")).toBe("messages");
    expect(resolveAppRedirect("settings")).toBe("settings");
  });

  it("drops a pin whose redirect target is unknown without throwing", () => {
    expect(resolveAppRedirect("s1-orphan-redirect")).toBeUndefined();
  });

  it("drops a pin that is neither a redirect nor a registered app", () => {
    expect(resolveAppRedirect("s1-ghost-id")).toBeUndefined();
  });
});
