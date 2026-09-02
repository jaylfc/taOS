import { describe, it, expect } from "vitest";
import { getLaunchableApps, getSearchableApps } from "@/registry/app-registry";

describe("tier-3 search discoverability", () => {
  const TIER3_IDS = ["providers", "mcp", "channels", "notification-archive"];

  it("includes tier-3 apps in search results", () => {
    const ids = getSearchableApps(new Set()).map((a) => a.id);
    for (const id of TIER3_IDS) {
      expect(ids, `tier-3 app "${id}" should be searchable`).toContain(id);
    }
  });

  it("does not reintroduce tier-3 apps into the launcher", () => {
    const ids = getLaunchableApps(new Set()).map((a) => a.id);
    for (const id of TIER3_IDS) {
      expect(ids, `tier-3 app "${id}" should NOT be in the launcher`).not.toContain(id);
    }
  });

  // Regression for the #2680 fix-forward: installed optional apps must stay
  // searchable (SearchPalette regressed here) as well as launchable. The buggy
  // predicate `(isDefaultSurfaceApp(a) || a.tier === 3) && (a.optional ? has(id) : true)`
  // dropped them because `isDefaultSurfaceApp` is false for optional apps and
  // studios are tier 5, so the leading clause was false before the optional
  // install check was ever evaluated.
  const OPTIONAL_ID = "coding-studio";

  it("surface a REAL installed optional app in both search and launcher", () => {
    const installed = new Set([OPTIONAL_ID]);
    const searchable = getSearchableApps(installed).map((a) => a.id);
    const launchable = getLaunchableApps(installed).map((a) => a.id);
    expect(searchable, "installed optional app should be searchable").toContain(OPTIONAL_ID);
    expect(launchable, "installed optional app should be launchable").toContain(OPTIONAL_ID);
  });

  it("hide an UNinstalled optional app from both search and launcher", () => {
    const installed = new Set<string>();
    const searchable = getSearchableApps(installed).map((a) => a.id);
    const launchable = getLaunchableApps(installed).map((a) => a.id);
    expect(searchable, "uninstalled optional app should NOT be searchable").not.toContain(OPTIONAL_ID);
    expect(launchable, "uninstalled optional app should NOT be launchable").not.toContain(OPTIONAL_ID);
  });
});
