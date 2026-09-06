import { describe, it, expect, vi } from "vitest";
import { installAuthGuard } from "./auth-guard";

describe("installAuthGuard CSRF wiring", () => {
  it("attaches X-CSRF-Token to same-origin mutating requests only", async () => {
    document.cookie = "csrf_token=tok123";
    const spy = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
    // installAuthGuard captures window.fetch as the original at install time, so
    // set the spy first, then install: subsequent window.fetch calls go through
    // the patched wrapper into the spy.
    window.fetch = spy as unknown as typeof window.fetch;
    installAuthGuard();

    // Same-origin mutating request: the double-submit header is attached so the
    // backend's router-wide verify_csrf gate is satisfied.
    await window.fetch("/api/projects", { method: "POST", body: "{}" });
    const postInit = spy.mock.calls.at(-1)?.[1] as RequestInit;
    expect(new Headers(postInit.headers).get("X-CSRF-Token")).toBe("tok123");

    // Non-mutating request: no header (verify_csrf only gates mutations).
    await window.fetch("/api/projects");
    const getInit = spy.mock.calls.at(-1)?.[1] as RequestInit | undefined;
    expect(getInit ? new Headers(getInit.headers).get("X-CSRF-Token") : null).toBeNull();

    // External origin: the token is never sent off-site.
    await window.fetch("https://evil.example/x", { method: "POST" });
    const extInit = spy.mock.calls.at(-1)?.[1] as RequestInit;
    expect(new Headers(extInit.headers).get("X-CSRF-Token")).toBeNull();

    // Protocol-relative and lookalike-host URLs must NOT be treated as
    // same-origin (a prefix check would leak the token to these).
    await window.fetch("//evil.example/x", { method: "POST" });
    const protoRel = spy.mock.calls.at(-1)?.[1] as RequestInit;
    expect(new Headers(protoRel.headers).get("X-CSRF-Token")).toBeNull();

    await window.fetch(`${window.location.origin}.evil.com/x`, { method: "POST" });
    const lookalike = spy.mock.calls.at(-1)?.[1] as RequestInit;
    expect(new Headers(lookalike.headers).get("X-CSRF-Token")).toBeNull();

    // Request-object input for a same-origin mutating call: the wrapper merges
    // the CSRF token into effectiveInit so native fetch respects any
    // init-provided method/headers and preserves the original body stream.
    await window.fetch(new Request(`${window.location.origin}/api/x`, { method: "POST" }));
    const reqInit = spy.mock.calls.at(-1)?.[1] as RequestInit;
    expect(new Headers(reqInit.headers).get("X-CSRF-Token")).toBe("tok123");

    // Request headers AND init headers together: `init?.headers || input.headers`
    // discarded the Request's own headers whenever init carried any, so an
    // Authorization set on the Request went missing on every mutating call that
    // also passed init.headers. Both sources must survive, init winning ties.
    // (These assertions live in this block deliberately: installAuthGuard has a
    // module-level `installed` guard, so a second install in a new it() is a
    // no-op and window.fetch would be the bare spy.)
    await window.fetch(
      new Request(`${window.location.origin}/api/y`, {
        method: "POST",
        headers: { Authorization: "Bearer abc", "X-From-Request": "1" },
      }),
      { headers: { "X-From-Init": "2" } },
    );
    const merged = new Headers((spy.mock.calls.at(-1)?.[1] as RequestInit).headers);
    expect(merged.get("Authorization")).toBe("Bearer abc");
    expect(merged.get("X-From-Request")).toBe("1");
    expect(merged.get("X-From-Init")).toBe("2");
    expect(merged.get("X-CSRF-Token")).toBe("tok123");
  });

});
