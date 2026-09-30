import { describe, it, expect } from "vitest";
import pkg from "../../package.json" with { type: "json" };

const sourceFiles = import.meta.glob("/src/**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const declaredDeps = new Set([
  ...Object.keys(pkg.dependencies ?? {}),
  ...Object.keys(pkg.devDependencies ?? {}),
]);

function extractBareSpecifiers(content: string): string[] {
  const specifiers = new Set<string>();

  const importRegex =
    /^\s*(?:import(?:\s+(?:[^'"\s{]+|\{[^}]*\}|\*\s+as\s+\w+)\s+from)?\s*|export\s+(?:\{[^}]*\}\s+from\s+)?)\s*["']([^"']+)["']/gm;

  let match: RegExpExecArray | null;
  while ((match = importRegex.exec(content)) !== null) {
    const raw = match[1];
    if (isBareSpecifier(raw)) {
      const normalized = normalizeSpecifier(raw);
      specifiers.add(normalized);
    }
  }

  return Array.from(specifiers);
}

function isBareSpecifier(specifier: string): boolean {
  return (
    !specifier.startsWith(".") &&
    !specifier.startsWith("@/") &&
    !specifier.startsWith("node:") &&
    !specifier.startsWith("node:") &&
    specifier.length > 0
  );
}

function normalizeSpecifier(specifier: string): string {
  if (specifier.startsWith("@")) {
    const parts = specifier.split("/");
    return parts.length >= 2 ? `${parts[0]}/${parts[1]}` : specifier;
  }
  return specifier.split("/")[0];
}

const missing: string[] = [];

for (const [filepath, content] of Object.entries(sourceFiles)) {
  if (filepath.endsWith(".test.ts") || filepath.endsWith(".test.tsx")) continue;
  if (filepath.includes("__tests__")) continue;

  const specifiers = extractBareSpecifiers(content);
  for (const spec of specifiers) {
    if (!declaredDeps.has(spec)) {
      missing.push(`${spec} (from ${filepath})`);
    }
  }
}

describe("dependency declarations", () => {
  it("every directly-imported package is declared as a dependency", () => {
    const uniqueMissing = [...new Set(missing.map((m) => m.split(" ")[0]))].sort();
    expect(uniqueMissing).toEqual([]);
  });
});