// @vitest-environment node
/**
 * Build-artefact guard for the SPA shell's pre-paint script (#655, #58).
 *
 * `desktop/index.html` loads the reduce-effects boot script from a
 * root-absolute `/boot.js` — Vite's public-dir convention. That URL is only
 * correct because the build is configured with `base: "/desktop/"`, which
 * rewrites root-absolute references to `/desktop/...`; the shell the server
 * actually hands out is the built `static/desktop/index.html`, not the source
 * file. The Python guard in tests/test_security_headers.py asserts against the
 * *source* shell, so on its own it proves the base rewrite only by hand (jaylfc
 * review on #3226). This test runs a real `vite build` and inspects the emitted
 * shell, so dropping `base` or switching the reference to an absolute URL fails
 * here instead of silently shipping a pre-paint script that 404s.
 *
 * The same build's public-dir copy of `boot.js` is checked too: the rewritten
 * URL must resolve to a file that actually applies the saved preference.
 *
 * Follows the sw-bundle.test.ts pattern (build into a temp dir, inspect the
 * emitted artefact) — the bundler output, not the source, is the contract.
 */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const DESKTOP = path.resolve(__dirname, "..", "..");
const VITE_BIN = path.join(DESKTOP, "node_modules", "vite", "bin", "vite.js");

let outDir = "";
let shell = "";

beforeAll(() => {
  outDir = mkdtempSync(path.join(tmpdir(), "taos-shell-build-"));
  // Drop the vitest-injected mode so the child is a normal production build.
  const env = { ...process.env };
  delete env.NODE_ENV;
  delete env.VITEST;
  delete env.VITEST_MODE;
  execFileSync(
    process.execPath,
    [VITE_BIN, "build", "--outDir", outDir, "--emptyOutDir", "--logLevel", "error"],
    { cwd: DESKTOP, env, stdio: ["ignore", "pipe", "pipe"] },
  );
  shell = readFileSync(path.join(outDir, "index.html"), "utf8");
}, 600_000);

afterAll(() => {
  if (outDir) rmSync(outDir, { recursive: true, force: true });
});

interface ScriptTag {
  /** Lower-cased attribute names → values (empty string for bare attributes). */
  attrs: Record<string, string>;
  /** Offset of the `<script` in the source string. */
  start: number;
  /** The raw tag text, `<script` through its closing `>`. */
  raw: string;
}

/**
 * Parse every `<script>` start tag with a quote-aware scanner.
 *
 * A regex such as `<script\b[^>]*>` stops at the first `>` even inside a
 * quoted attribute value (e.g. `<script data-x="a>b">`), which mis-splits the
 * tag; the Python guard in tests/test_security_headers.py switched to a real
 * parser for exactly this reason, and HTMLParser is not available in a node
 * vitest environment, so this does the equivalent scan by hand. Comments and
 * script bodies are the caller's concern (the emitted shell has none).
 */
function scriptStartTags(html: string): ScriptTag[] {
  const isSpace = (c: string) => c === " " || c === "\t" || c === "\n" || c === "\r" || c === "\f";
  const tags: ScriptTag[] = [];
  const opener = /<script\b/gi;
  let match: RegExpExecArray | null;
  while ((match = opener.exec(html))) {
    const start = match.index;
    let i = opener.lastIndex;
    const attrs: Record<string, string> = {};
    while (i < html.length) {
      while (i < html.length && isSpace(html[i])) i++;
      if (html[i] === ">") {
        i++;
        break;
      }
      if (html[i] === "/" && html[i + 1] === ">") {
        i += 2;
        break;
      }
      let name = "";
      while (i < html.length && !isSpace(html[i]) && !"=/>".includes(html[i])) name += html[i++];
      while (i < html.length && isSpace(html[i])) i++;
      let value = "";
      if (html[i] === "=") {
        i++;
        while (i < html.length && isSpace(html[i])) i++;
        const quote = html[i];
        if (quote === '"' || quote === "'") {
          i++;
          while (i < html.length && html[i] !== quote) value += html[i++];
          i++; // closing quote
        } else {
          while (i < html.length && !isSpace(html[i]) && html[i] !== ">") value += html[i++];
        }
      }
      if (name) attrs[name.toLowerCase()] = value;
    }
    opener.lastIndex = i;
    tags.push({ attrs, start, raw: html.slice(start, i) });
  }
  return tags;
}

/** The `src` of every real <script> start tag (a `data-src` does not count). */
function scriptSrcs(html: string): string[] {
  return scriptStartTags(html)
    .map((tag) => tag.attrs.src)
    .filter((src): src is string => src !== undefined);
}

describe("built SPA shell keeps the pre-paint script working", () => {
  it("rewrites the source /boot.js reference to /desktop/boot.js", () => {
    expect(scriptSrcs(shell)).toContain("/desktop/boot.js");
  });

  it("leaves no reference at the un-rewritten root path", () => {
    // If `base: "/desktop/"` is dropped (or the tag is hard-coded absolute),
    // the served shell would request /boot.js, which the desktop route does not
    // serve — the saved reduce-effects preference would never apply pre-paint.
    expect(scriptSrcs(shell)).not.toContain("/boot.js");
  });

  it("ships boot.js at the build root so the rewritten URL resolves", () => {
    const boot = readFileSync(path.join(outDir, "boot.js"), "utf8");
    expect(boot).toContain("taos-reduce-effects");
    expect(boot).toContain('setAttribute("data-perf", "reduced")');
  });

  it("emits the pre-paint script as a blocking tag in <head>", () => {
    const headEnd = shell.toLowerCase().indexOf("</head>");
    expect(headEnd, "the built shell has no </head>").toBeGreaterThan(-1);
    const boot = scriptStartTags(shell).find(
      (tag) => tag.attrs.src === "/desktop/boot.js",
    );
    expect(boot, "no <script src=/desktop/boot.js> in the built shell").toBeDefined();
    expect(boot!.start).toBeLessThan(headEnd);
    expect(boot!.raw).not.toMatch(/\b(?:defer|async)\b/i);
  });

  it("keeps every emitted script external (CSP script-src 'self')", () => {
    // Inline scripts are blocked by the app's CSP, so the built shell must have
    // a src on every <script>. Comments are stripped first: the shell's own
    // explanation of the change mentions script markup in prose.
    const withoutComments = shell.replace(/<!--[\s\S]*?-->/g, "");
    const tags = scriptStartTags(withoutComments);
    expect(tags.length).toBeGreaterThan(0);
    for (const tag of tags) {
      expect(tag.attrs.src, `inline <script> in the built shell: ${tag.raw}`).toBeTruthy();
    }
  });
});
