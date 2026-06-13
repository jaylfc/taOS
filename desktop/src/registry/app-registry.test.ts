import { describe, it, expect, vi } from "vitest";
import { getOrRegisterServiceApp, prefetchApp, resolveApp } from "./app-registry";

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
