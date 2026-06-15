import { describe, it, expect } from "vitest";
import { useMobileHomeStore } from "./mobile-home-store";
import { getAllApps } from "@/registry/app-registry";

describe("mobile-home-store", () => {
  it("home grid includes every non-optional registered app", () => {
    const { pages } = useMobileHomeStore.getState();
    const allIdsInPages = new Set(
      pages.flatMap((p) =>
        p.items
          .filter((i) => i.type === "app")
          .map((i) => (i as { type: "app"; appId: string }).appId),
      ),
    );
    // Optional apps (Reddit/YouTube/GitHub/X) ship uninstalled and are added
    // from the Store, so they are intentionally absent from the default grid.
    const defaultIds = getAllApps().filter((a) => !a.optional).map((a) => a.id);
    for (const id of defaultIds) {
      expect(allIdsInPages.has(id), `missing app "${id}" in home grid`).toBe(true);
    }
    const optionalIds = getAllApps().filter((a) => a.optional).map((a) => a.id);
    for (const id of optionalIds) {
      expect(allIdsInPages.has(id), `optional app "${id}" should NOT be in default grid`).toBe(false);
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
