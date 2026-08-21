import { describe, it, expect, vi } from "vitest";
import { getApp, getOrRegisterServiceApp, getAllApps, getLaunchableApps, prefetchApp, resolveApp } from "./app-registry";

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
