import { describe, it, expect } from "vitest";
import pkg from "../../package.json";

const declared = new Set([
  ...Object.keys(pkg.dependencies ?? {}),
  ...Object.keys(pkg.devDependencies ?? {}),
]);

function isRelativeOrSpecial(spec: string): boolean {
  return (
    spec.startsWith(".") ||
    spec.startsWith("@/") ||
    spec.startsWith("node:") ||
    spec.startsWith("node ")
  );
}

function isTypeOnly(line: string): boolean {
  return /^\s*(?:import|export)\s+type\s/.test(line);
}

function extractImports(source: string): string[] {
  const imports: string[] = [];
  for (const rawLine of source.split("\n")) {
    const line = rawLine.trim();
    if (isTypeOnly(line)) continue;

    let match: RegExpMatchArray | null = null;
    if (line.startsWith("import ")) {
      match = line.match(/^import\s+(?:["']([^"']+)["']|.*?\s+from\s+["']([^"']+)["'])/);
    } else if (line.startsWith("export ")) {
      match = line.match(/^export\s+.*?\s+from\s+["']([^"']+)["']/);
    }

    if (!match) continue;
    const spec = match[1] ?? match[2];
    if (!spec) continue;
    if (isRelativeOrSpecial(spec)) continue;

    imports.push(spec);
  }
  return imports;
}

function isDeclared(spec: string): boolean {
  if (declared.has(spec)) return true;
  for (const dep of declared) {
    if (spec.startsWith(dep + "/")) return true;
  }
  return false;
}

describe("every directly-imported package is declared as a dependency", () => {
  it("every bare specifier imported by desktop/src/**/*.{ts,tsx} appears in dependencies or devDependencies", () => {
    const modules = import.meta.glob("/src/**/*.{ts,tsx}", {
      query: "?raw",
      import: "default",
      eager: true,
    });

    const violations: string[] = [];
    for (const source of Object.values(modules)) {
      for (const spec of extractImports(source)) {
        if (!isDeclared(spec)) {
          violations.push(spec);
        }
      }
    }

    const unique = [...new Set(violations)].sort();
    expect(unique).toEqual([]);
  });
});
