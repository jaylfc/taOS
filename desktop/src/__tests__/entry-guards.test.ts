import { describe, it, expect, vi, beforeEach } from "vitest";

// Use vi.hoisted so the mock reference is stable across vi.resetModules() calls.
// vi.mock factories only run once; vi.resetModules clears the module cache but
// does not re-invoke the factory, so the returned vi.fn() instance persists.
// mockClear() between tests resets the call count so each assertion sees only
// the calls from the current test's dynamic import().
const { installAuthGuard: mockGuard } = vi.hoisted(() => ({
  installAuthGuard: vi.fn(),
}));

vi.mock("../lib/auth-guard", () => ({
  installAuthGuard: mockGuard,
  SESSION_EXPIRED_EVENT: "taos-session-expired",
}));

// Entry modules call createRoot(document.getElementById("root")!).render(...).
// Stub createRoot so the render tree doesn't actually mount (components pull in
// real stores, contexts, and side-effects we don't need for this assertion).
vi.mock("react-dom/client", () => ({
  createRoot: vi.fn(() => ({ render: vi.fn() })),
}));

// Some entry modules import AppShell / AppStandalone / ChatStandalone which
// transitively pull in heavy dependency trees. Stub them as no-op components.
vi.mock("../components/AppShell", () => ({
  AppShell: ({ children }: { children?: React.ReactNode }) => children ?? null,
}));

vi.mock("../App", () => ({ App: () => null }));
vi.mock("../ChatStandalone", () => ({ ChatStandalone: () => null }));
vi.mock("../AppStandalone", () => ({ AppStandalone: () => null }));

vi.mock("../stores/theme-store", () => ({
  restoreActiveTheme: vi.fn(),
  installWebkitRepaintGuards: vi.fn(),
}));

vi.mock("../registry/app-registry", () => ({
  getApp: vi.fn(() => undefined),
}));

vi.mock("../lib/client-log", () => ({
  installGlobalErrorReporting: vi.fn(),
}));

describe("entry module auth guards", () => {
  beforeEach(() => {
    vi.resetModules();
    mockGuard.mockClear();
    // Every entry module calls createRoot(document.getElementById("root")!).
    // Provide the element so the non-null assertion (!) doesn't throw.
    document.body.innerHTML = '<div id="root"></div>';
  });

  it("desktop shell (main.tsx) installs the auth guard", async () => {
    await import("../main");
    expect(mockGuard).toHaveBeenCalledTimes(1);
  });

  it("chat PWA (chat-main.tsx) installs the auth guard", async () => {
    await import("../chat-main");
    expect(mockGuard).toHaveBeenCalledTimes(1);
  });

  it("standalone app PWA (app-standalone-main.tsx) installs the auth guard", async () => {
    await import("../app-standalone-main");
    expect(mockGuard).toHaveBeenCalledTimes(1);
  });
});
