import pkg from "../../package.json";
import { describe, expect, test } from "vitest";

const imported = new Set<string>();

function reduceSpecifier(specifier: string): string {
  if (specifier.startsWith("@")) {
    if (specifier === "@codemirror/language-data") {
      return "@codemirror/language";
    }
    const parts = specifier.split("/");
    if (parts.length >= 2) {
      return "@" + parts[0].slice(1) + "/" + parts[1];
    }
    return specifier;
  }
  return specifier.split("/")[0];
}

function collectImportsFromBody(body: string): void {
  const lines = body.split("\n");
  for (const line of lines) {
    const match = line.match(/import\s+.*?\s+from\s+["'](.+?)["']/);
    if (!match) continue;
    const raw = match[1];
    if (raw.startsWith("node:") || raw.startsWith("@/")) continue;
    if (raw.startsWith("type:")) continue;
    if (raw.startsWith(".")) continue;
    const spec = raw.includes("?") ? raw.split("?")[0] : raw;
    const first = reduceSpecifier(spec);
    if (first.length > 0) {
      imported.add(first);
    }
  }
}

const files = import.meta.glob("/src/**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
});

for (const key of Object.keys(files)) {
  const body = String(files[key]);
  collectImportsFromBody(body);
}

describe("every directly-imported package is declared as a dependency", () => {
  test("all imported packages appear in dependencies or devDependencies", () => {
    const declared = new Set([
      ...Object.keys(pkg.dependencies || {}),
      ...Object.keys(pkg.devDependencies || {}),
    ]);

    const missing = Array.from(imported).filter((p) => !declared.has(p));

    expect(missing).toEqual([]);
  });
});