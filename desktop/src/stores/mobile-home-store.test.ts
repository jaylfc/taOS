import { describe, it, expect } from "vitest";
import { useMobileHomeStore } from "./mobile-home-store";
import { getAllApps } from "@/registry/app-registry";

describe("mobile-home-store", () => {
  it("home grid includes every registered app", () => {
    const { pages } = useMobileHomeStore.getState();
    const allIdsInPages = new Set(
      pages.flatMap((p) =>
        p.items
          .filter((i) => i.type === "app")
          .map((i) => (i as { type: "app"; appId: string }).appId),
      ),
    );
    const registryIds = getAllApps().map((a) => a.id);
    for (const id of registryIds) {
      expect(allIdsInPages.has(id), `missing app "${id}" in home grid`).toBe(true);
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
