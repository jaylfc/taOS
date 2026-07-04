import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { useSessionPersistence } from "../use-session-persistence";
import { useDockStore } from "@/stores/dock-store";
import { useThemeStore } from "@/stores/theme-store";
import { useAuthReadyStore } from "@/stores/auth-ready-store";

vi.mock("@/registry/app-registry", () => ({
  getApp: () => undefined,
  prefetchApp: () => {},
}));

vi.mock("@/lib/browser-windows-api", () => ({
  loadWindows: async () => [],
  saveWindows: async () => {},
}));

function mockFetchWith(responses: Record<string, unknown>) {
  vi.spyOn(globalThis, "fetch").mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    for (const [path, body] of Object.entries(responses)) {
      if (url.includes(path)) {
        return Promise.resolve(new Response(JSON.stringify(body)));
      }
    }
    return Promise.resolve(new Response(JSON.stringify({})));
  });
}

beforeEach(() => {
  useDockStore.setState({
    pinned: ["messages", "agents", "files", "store", "settings"],
    iconSize: "medium",
    position: "bottom",
  });
  useThemeStore.setState({ wallpaperId: "graphite" } as never);
  // Baseline: already authenticated, matching the pre-existing tests below
  // which exercise restore-on-mount in isolation. The auth-gating tests
  // further down explicitly manipulate this.
  useAuthReadyStore.setState({ ready: true });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useSessionPersistence — wallpaper restore (#1601)", () => {
  it("restores a saved wallpaper on mount, including one whose id is literally 'default'", async () => {
    // Regression test: the "Classic" wallpaper's catalog id is the string
    // "default", which collided with the old "nothing saved" sentinel check
    // (`data.wallpaper !== "default"`) and was silently skipped on restore.
    mockFetchWith({ "/api/desktop/settings": { wallpaper: "default" } });

    renderHook(() => useSessionPersistence());

    await waitFor(() => {
      expect(useThemeStore.getState().wallpaperId).toBe("default");
    });
  });

  it("restores an ordinary saved wallpaper choice on mount", async () => {
    mockFetchWith({ "/api/desktop/settings": { wallpaper: "midnight" } });

    renderHook(() => useSessionPersistence());

    await waitFor(() => {
      expect(useThemeStore.getState().wallpaperId).toBe("midnight");
    });
  });
});

describe("useSessionPersistence — dock settings restore (#1603)", () => {
  it("restores saved dock icon size and position on mount", async () => {
    mockFetchWith({
      "/api/desktop/dock": {
        pinned: ["messages", "files"],
        iconSize: "large",
        position: "left",
      },
    });

    renderHook(() => useSessionPersistence());

    await waitFor(() => {
      expect(useDockStore.getState().iconSize).toBe("large");
      expect(useDockStore.getState().position).toBe("left");
    });
    expect(useDockStore.getState().pinned).toEqual(["messages", "files"]);
  });

  it("ignores an invalid persisted icon size or position", async () => {
    mockFetchWith({
      "/api/desktop/dock": { iconSize: "huge", position: "top" },
    });

    renderHook(() => useSessionPersistence());

    // Give the restore effect's promise chain a tick to resolve.
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(useDockStore.getState().iconSize).toBe("medium");
    expect(useDockStore.getState().position).toBe("bottom");
  });
});

describe("useSessionPersistence — persistence survives a logout/login cycle (#1601, #1603)", () => {
  it("does not fetch per-user settings before the session is authenticated", async () => {
    // SystemShortcuts (which owns this hook) mounts before LoginGate confirms
    // auth. A mount-gated restore used to fire here regardless, 401 while
    // logged out, and — having already "run" — never retry after login.
    useAuthReadyStore.setState({ ready: false });
    mockFetchWith({
      "/api/desktop/settings": { wallpaper: "midnight" },
      "/api/desktop/dock": { iconSize: "large", position: "left" },
    });

    renderHook(() => useSessionPersistence());

    // Give any (wrongly) in-flight effect a tick, then assert nothing fired.
    await new Promise((r) => setTimeout(r, 0));
    expect(globalThis.fetch).not.toHaveBeenCalledWith(
      expect.stringContaining("/api/desktop/settings"),
    );
    expect(globalThis.fetch).not.toHaveBeenCalledWith(
      expect.stringContaining("/api/desktop/dock"),
    );
    expect(useThemeStore.getState().wallpaperId).toBe("graphite");
    expect(useDockStore.getState().iconSize).toBe("medium");
  });

  it("restores the saved theme, wallpaper and dock settings once auth flips ready, and re-restores on a later re-login", async () => {
    // Simulate a full app boot: SystemShortcuts mounts first (pre-auth), then
    // LoginGate resolves /auth/status and the user logs in.
    useAuthReadyStore.setState({ ready: false });
    mockFetchWith({
      "/api/desktop/settings": { wallpaper: "midnight" },
      "/api/desktop/dock": { iconSize: "large", position: "left" },
    });

    renderHook(() => useSessionPersistence());

    // Still logged out: the store must stay at its defaults, not midnight/large.
    expect(useThemeStore.getState().wallpaperId).toBe("graphite");

    // Login completes.
    act(() => {
      useAuthReadyStore.setState({ ready: true });
    });

    await waitFor(() => {
      expect(useThemeStore.getState().wallpaperId).toBe("midnight");
      expect(useDockStore.getState().iconSize).toBe("large");
      expect(useDockStore.getState().position).toBe("left");
    });

    // Logout, then a second login as a user with different saved settings —
    // the restore must run again, not stay stuck on the first session's values.
    act(() => {
      useAuthReadyStore.setState({ ready: false });
    });
    useDockStore.setState({ iconSize: "medium", position: "bottom" });
    useThemeStore.setState({ wallpaperId: "graphite" } as never);
    mockFetchWith({
      "/api/desktop/settings": { wallpaper: "aurora" },
      "/api/desktop/dock": { iconSize: "small", position: "bottom" },
    });

    act(() => {
      useAuthReadyStore.setState({ ready: true });
    });

    await waitFor(() => {
      expect(useThemeStore.getState().wallpaperId).toBe("aurora");
      expect(useDockStore.getState().iconSize).toBe("small");
    });
  });

  it("does not let a debounced auto-save clobber the backend with default values while restore is still in flight", async () => {
    // Regression for the residual race: the old code gated every auto-save
    // effect on the same flag the restore effect flipped the instant it
    // *started* (not once its fetch resolved), so a slow restore GET could
    // lose to a faster debounced auto-save PUT carrying the pre-restore
    // default value. Here the wallpaper GET is deliberately slower than the
    // 500ms auto-save debounce.
    useAuthReadyStore.setState({ ready: false });
    let resolveSettings: (body: unknown) => void = () => {};
    const slowSettings = new Promise<unknown>((resolve) => {
      resolveSettings = resolve;
    });
    vi.spyOn(globalThis, "fetch").mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/desktop/settings")) {
        return slowSettings.then((body) => new Response(JSON.stringify(body)));
      }
      return Promise.resolve(new Response(JSON.stringify({})));
    });

    renderHook(() => useSessionPersistence());
    act(() => {
      useAuthReadyStore.setState({ ready: true });
    });

    // Let time pass well beyond the 500ms wallpaper auto-save debounce while
    // the settings GET is still unresolved.
    await new Promise((r) => setTimeout(r, 700));
    const putCalls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.filter(
      ([input, init]) =>
        (typeof input === "string" ? input : input.toString()).includes("/api/desktop/settings") &&
        (init as RequestInit | undefined)?.method === "PUT",
    );
    expect(putCalls).toHaveLength(0);

    // Restore finally resolves with the real saved wallpaper.
    resolveSettings({ wallpaper: "ocean" });
    await waitFor(() => {
      expect(useThemeStore.getState().wallpaperId).toBe("ocean");
    });
  });
});
