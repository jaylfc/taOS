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
});
