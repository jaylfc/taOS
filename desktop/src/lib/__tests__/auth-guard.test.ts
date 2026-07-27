import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { installAuthGuard, SESSION_EXPIRED_EVENT } from "../auth-guard";

describe("auth-guard", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = window.fetch;
    // Reset the module-level `installed` flag by re-importing -- Vitest
    // caches the module, so each test starts with the wrapper already
    // potentially installed from a previous test. We work around that
    // by resetting the modules. Cheaper alternative for this single
    // test file: run the test cases serially and accept that
    // installAuthGuard is idempotent (assertion below verifies that).
  });

  afterEach(() => {
    window.fetch = originalFetch;
    vi.resetModules();
  });

  it("dispatches taos-session-expired on 401 from /api/* paths", async () => {
    vi.resetModules();
    const { installAuthGuard: install, SESSION_EXPIRED_EVENT: evt } = await import("../auth-guard");
    window.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
    install();

    const handler = vi.fn();
    window.addEventListener(evt, handler);
    await window.fetch("/api/store/catalog");
    window.removeEventListener(evt, handler);

    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("does not dispatch on 401 from /auth/* paths", async () => {
    vi.resetModules();
    const { installAuthGuard: install } = await import("../auth-guard");
    window.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
    install();

    const handler = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, handler);
    await window.fetch("/auth/login");
    window.removeEventListener(SESSION_EXPIRED_EVENT, handler);

    expect(handler).not.toHaveBeenCalled();
  });

  it("does not dispatch on 401 from /api/account/* paths", async () => {
    // The account proxy (taos.my cloud account) is a separate auth boundary
    // and returns 401 when the user simply is not signed into the cloud --
    // account-client maps that to a signed-out state. Treating it as a
    // controller-session expiry flashed the login gate and bounced the user
    // out of the Account pane. Reported by jay 2026-07-01.
    vi.resetModules();
    const { installAuthGuard: install } = await import("../auth-guard");
    window.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
    install();

    const handler = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, handler);
    await window.fetch("/api/account/me");
    window.removeEventListener(SESSION_EXPIRED_EVENT, handler);

    expect(handler).not.toHaveBeenCalled();
  });

  it("does not dispatch on 200", async () => {
    vi.resetModules();
    const { installAuthGuard: install } = await import("../auth-guard");
    window.fetch = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    install();

    const handler = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, handler);
    await window.fetch("/api/health");
    window.removeEventListener(SESSION_EXPIRED_EVENT, handler);

    expect(handler).not.toHaveBeenCalled();
  });

  it("throttles bursts to one event per 2s", async () => {
    vi.resetModules();
    const { installAuthGuard: install } = await import("../auth-guard");
    window.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
    install();

    const handler = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, handler);
    await Promise.all([
      window.fetch("/api/a"),
      window.fetch("/api/b"),
      window.fetch("/api/c"),
    ]);
    window.removeEventListener(SESSION_EXPIRED_EVENT, handler);

    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("every SPA entry module installs the auth guard", () => {
    const viteConfig = readFileSync(join(process.cwd(), "vite.config.ts"), "utf-8");
    const htmlPaths = Array.from(viteConfig.matchAll(/path\.resolve\(__dirname,\s*"([^"]+\.html)"\)/g)).map(
      (m) => join(process.cwd(), m[1]),
    );

    // The list is derived by regex over vite.config.ts, so a reformat, a
    // variable, or different quoting there yields an EMPTY array -- the loop
    // below would never run and this test would PASS while checking nothing.
    // That is precisely the failure it exists to catch (the gap it guards
    // existed because the suite was green with the guard in 1 of 3 entries).
    // Assert the discovery worked before asserting anything about what it found.
    expect(
      htmlPaths.length,
      "no HTML entrypoints parsed out of vite.config.ts - the discovery regex has drifted, so this test would pass vacuously",
    ).toBeGreaterThanOrEqual(3);

    const scriptRegex = /<script[^>]*type="module"[^>]*src="([^"]+)"/g;

    for (const htmlPath of htmlPaths) {
      const html = readFileSync(htmlPath, "utf-8");
      const match = html.match(scriptRegex);
      expect(match, `no module script found in ${htmlPath}`).toBeTruthy();

      const scriptSrc = match![0].match(/src="([^"]+)"/)![1];
      const entryModule = join(dirname(htmlPath), scriptSrc.replace(/^\//, ""));
      const entrySource = readFileSync(entryModule, "utf-8");

      expect(entrySource, `installAuthGuard missing in ${entryModule}`).toContain("installAuthGuard()");
    }
  });
});
