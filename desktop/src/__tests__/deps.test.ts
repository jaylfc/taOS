import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const SRC_DIR = join(__dirname, "..");
const PACKAGE_JSON = join(SRC_DIR, "..", "package.json");

function walk(dir: string): string[] {
  const entries: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (entry === "__tests__" || entry === "node_modules") continue;
      entries.push(...walk(full));
    } else if (/\.(ts|tsx)$/.test(entry)) {
      entries.push(full);
    }
  }
  return entries;
}

function getPackageName(spec: string): string {
  if (spec.startsWith("@")) {
    const parts = spec.split("/");
    return parts.length >= 2 ? `${parts[0]}/${parts[1]}` : spec;
  }
  return spec.split("/")[0];
}

function stripComments(content: string): string {
  let result = "";
  let i = 0;
  while (i < content.length) {
    if (content[i] === "/" && content[i + 1] === "/") {
      while (i < content.length && content[i] !== "\n") i++;
    } else if (content[i] === "/" && content[i + 1] === "*") {
      i += 2;
      while (i < content.length - 1 && !(content[i] === "*" && content[i + 1] === "/")) i++;
      i += 2;
    } else {
      result += content[i];
      i++;
    }
  }
  return result;
}

function getImportedPackages(files: string[]): string[] {
  const pkgs = new Set<string>();
  const fromRe = /from\s+["']([^"']+)["']/g;
  const bareRe = /^import\s+["']([^"']+)["']/gm;

  for (const file of files) {
    const content = stripComments(readFileSync(file, "utf-8"));

    let m: RegExpExecArray | null;
    while ((m = fromRe.exec(content)) !== null) {
      const spec = m[1];
      if (spec.startsWith(".") || spec.startsWith("/") || spec.startsWith("@/") || spec.startsWith("node:")) continue;
      pkgs.add(getPackageName(spec));
    }

    while ((m = bareRe.exec(content)) !== null) {
      const spec = m[1];
      if (spec.startsWith(".") || spec.startsWith("/") || spec.startsWith("@/") || spec.startsWith("node:")) continue;
      pkgs.add(getPackageName(spec));
    }
  }

  return Array.from(pkgs);
}

describe("dependency declaration audit", () => {
  it("every directly-imported package is declared as a dependency", () => {
    const pkg = JSON.parse(readFileSync(PACKAGE_JSON, "utf-8"));
    const declared = new Set([
      ...Object.keys(pkg.dependencies ?? {}),
      ...Object.keys(pkg.devDependencies ?? {}),
    ]);
    const imported = getImportedPackages(walk(SRC_DIR));
    const violations = imported.filter((p) => !declared.has(p));
    expect(violations).toEqual([]);
  });
});
