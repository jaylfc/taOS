import { existsSync } from "node:fs";
import path from "node:path";
import { describe, it, expect } from "vitest";
import { DEFAULT_TEMPLATE, TEMPLATES, findTemplate } from "./templates";

// Vitest's root is the `desktop/` package, so this resolves relative to cwd.
const SEEDS_DIR = path.resolve(process.cwd(), "public/gamestudio-seeds");

describe("TEMPLATES", () => {
  it("has no duplicate ids", () => {
    const ids = TEMPLATES.map((t) => t.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it.each(TEMPLATES)("$id has valid metadata and includes index.html", (t) => {
    expect(t.id).toMatch(/^[a-z0-9-]+$/);
    expect(t.title.length).toBeGreaterThan(0);
    expect(t.genre.length).toBeGreaterThan(0);
    expect(t.desc.length).toBeGreaterThan(0);
    expect(t.cover.length).toBeGreaterThan(0);
    expect(t.files).toContain("index.html");
  });

  it.each(TEMPLATES)("$id's seed files exist on disk under gamestudio-seeds/", (t) => {
    for (const file of t.files) {
      const filePath = path.join(SEEDS_DIR, t.id, file);
      expect(existsSync(filePath), `expected ${filePath} to exist`).toBe(true);
    }
  });

  it("findTemplate resolves every registered id", () => {
    for (const t of TEMPLATES) {
      expect(findTemplate(t.id)).toBe(t);
    }
  });

  it("findTemplate returns undefined for an unknown id", () => {
    expect(findTemplate("does-not-exist")).toBeUndefined();
  });

  it("DEFAULT_TEMPLATE is the first template", () => {
    expect(DEFAULT_TEMPLATE).toBe(TEMPLATES[0]);
  });
});
