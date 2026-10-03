/**
 * Behavioural guard for the SPA pre-paint script (#58, CSP hardening #655).
 *
 * The shell ships `desktop/public/boot.js` as a blocking, same-origin script so
 * the saved reduce-effects preference is applied to `<html data-perf>` before
 * the first paint under the app's strict `script-src 'self'` CSP. The Python
 * guard in tests/test_security_headers.py checks the wiring (the shell
 * references the file, and nothing in the shell is inline); this test executes
 * the real shipped file, so a commented-out or gutted implementation cannot
 * pass it.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { beforeEach, describe, expect, it } from "vitest";

const BOOT_PATH = path.resolve(__dirname, "..", "..", "public", "boot.js");

/**
 * Read the shipped script inside the test, not at module scope: a missing or
 * renamed asset then shows up as a normal test failure instead of an import
 * error that stops vitest from discovering the file's tests at all.
 */
function bootSource(): string {
  return readFileSync(BOOT_PATH, "utf-8");
}

/** Run the shipped script the way the browser does — an IIFE in global scope. */
function runBoot(saved: string | null): string | null {
  localStorage.clear();
  if (saved !== null) localStorage.setItem("taos-reduce-effects", saved);
  document.documentElement.removeAttribute("data-perf");
  new Function(bootSource())();
  return document.documentElement.getAttribute("data-perf");
}

describe("desktop/public/boot.js (pre-paint reduce-effects)", () => {
  beforeEach(() => {
    document.documentElement.removeAttribute("data-perf");
  });

  it("sets data-perf=reduced when the saved preference is on", () => {
    expect(runBoot("on")).toBe("reduced");
  });

  it("leaves data-perf unset when the saved preference is off", () => {
    expect(runBoot("off")).toBeNull();
  });

  it("leaves data-perf unset on a first run (no saved preference)", () => {
    expect(runBoot(null)).toBeNull();
  });
});