import { describe, it, expect } from "vitest";
import { useMobileHomeStore } from "./mobile-home-store";
import { getAllApps, isDefaultSurfaceApp } from "@/registry/app-registry";

function defaultGridAppIds(): Set<string> {
  return new Set(
    useMobileHomeStore.getState().pages.flatMap((p) =>
      p.items
        .filter((i) => i.type === "app")
        .map((i) => (i as { type: "app"; appId: string }).appId),
    ),
  );
}

describe("mobile-home-store", () => {
  it("home grid includes every app on the default surface", () => {
    const allIdsInPages = defaultGridAppIds();
    // Optional apps (Reddit/YouTube/GitHub/X) ship uninstalled and are added
    // from the Store, so they are intentionally absent from the default grid.
    const defaultIds = getAllApps().filter(isDefaultSurfaceApp).map((a) => a.id);
    for (const id of defaultIds) {
      expect(allIdsInPages.has(id), `missing app "${id}" in home grid`).toBe(true);
    }
    const optionalIds = getAllApps().filter((a) => a.optional).map((a) => a.id);
    for (const id of optionalIds) {
      expect(allIdsInPages.has(id), `optional app "${id}" should NOT be in default grid`).toBe(false);
    }
  });

  it("excludes tier-3 registry apps from the default home grid (#2517)", () => {
    const ids = defaultGridAppIds();
    // Assert on REAL registry ids: these platform-plumbing apps are discoverable
    // via Store/search, not the default mobile home surface.
    const tier3Ids = getAllApps().filter((a) => a.tier === 3).map((a) => a.id);
    expect(tier3Ids).toEqual(
      expect.arrayContaining(["providers", "mcp", "channels", "notification-archive"]),
    );
    for (const id of tier3Ids) {
      expect(ids.has(id), `tier-3 app "${id}" should NOT be in default grid`).toBe(false);
    }
  });

  it("home grid contains only valid registry IDs", () => {
    const { pages } = useMobileHomeStore.getState();
    const registryIds = new Set(getAllApps().map((a) => a.id));
    const appIdsInPages = pages.flatMap((p) =>
      p.items
        .filter((i) => i.type === "app")
        .map((i) => (i as { type: "app"; appId: string }).appId),
    );
    for (const id of appIdsInPages) {
      expect(registryIds.has(id), `dead app ID "${id}" in home grid`).toBe(true);
    }
  });
});
