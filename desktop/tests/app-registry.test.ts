import { describe, it, expect } from "vitest";
import { getApp, getAppsByCategory, getAllApps } from "../src/registry/app-registry";

describe("app registry", () => {
  it("returns a known app by id", () => {
    const app = getApp("messages");
    expect(app).toBeDefined();
    expect(app!.name).toBe("Messages");
    expect(app!.category).toBe("platform");
  });

  it("returns undefined for unknown app", () => {
    expect(getApp("nonexistent")).toBeUndefined();
  });

  it("filters apps by category", () => {
    const osApps = getAppsByCategory("os");
    expect(osApps.length).toBeGreaterThan(0);
    expect(osApps.every((a) => a.category === "os")).toBe(true);
  });

  it("returns all apps", () => {
    const all = getAllApps();
    expect(all.length).toBeGreaterThan(10);
  });

  it("every app has required fields", () => {
    for (const app of getAllApps()) {
      expect(app.id).toBeTruthy();
      expect(app.name).toBeTruthy();
      expect(app.icon).toBeTruthy();
      expect(app.category).toBeTruthy();
      expect(app.defaultSize).toBeDefined();
      expect(app.minSize).toBeDefined();
    }
  });
});
