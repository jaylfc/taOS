import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, waitFor, act } from "@testing-library/react";
import { useSessionPersistence } from "../use-session-persistence";
import { useDockStore } from "@/stores/dock-store";
import { useThemeStore } from "@/stores/theme-store";
import { useAuthReadyStore } from "@/stores/auth-ready-store";
import { APP_REDIRECTS } from "@/registry/app-registry";

vi.mock("@/registry/app-registry", () => {
  const g = globalThis as Record<string, unknown>;
  if (!g.__mockApps) {
    g.__mockApps = new Map<string, { id: string }>([
      ["messages", { id: "messages" }],
      ["files", { id: "files" }],
      ["agents", { id: "agents" }],
      ["store", { id: "store" }],
      ["settings", { id: "settings" }],
    ]);
  }
  if (!g.__mockRedirects) {
    g.__mockRedirects = {} as Record<string, { appId: string }>;
  }
  const apps = g.__mockApps as Map<string, { id: string }>;
  const redirects = g.__mockRedirects as Record<string, { appId: string }>;

  return {
    getApp: (id: string) => apps.get(id),
    prefetchApp: () => {},
    resolvePinnedId: (id: string) => {
      const redirect = redirects[id];
      const targetId = redirect?.appId ?? id;
      return apps.has(targetId) ? targetId : undefined;
    },
    APP_REDIRECTS: redirects,
  };
});

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

function resetMockRegistry() {
  const apps = (globalThis as Record<string, Map<string, { id: string }>>).__mockApps;
  apps.clear();
  apps.set("messages", { id: "messages" });
  apps.set("files", { id: "files" });
  apps.set("agents", { id: "agents" });
  apps.set("store", { id: "store" });
  apps.set("settings", { id: "settings" });
  const redirects = (globalThis as Record<string, Record<string, { appId: string }>>).__mockRedirects;
  Object.keys(redirects).forEach((k) => delete redirects[k]);
}

beforeEach(() => {
  resetMockRegistry();
  useDockStore.setState({
    pinned: ["messages", "agents", "files", "store", "settings"],
    iconSize: "medium",
    position: "bottom",
  });
  useThemeStore.setState({ wallpaperId: "graphite" } as never);
  useAuthReadyStore.setState({ ready: true });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useSessionPersistence — wallpaper restore (#1601)", () => {
  it("restores a saved wallpaper on mount, including one whose id is literally 'default'", async () => {
    mockFetchWith({ "/api/desktop/settings": { wallpaper: "default" } });

    render(<TestPersistence />);

    await waitFor(() => {
      expect(useThemeStore.getState().wallpaperId).toBe("default");
    });
  });

  it("restores an ordinary saved wallpaper choice on mount", async () => {
    mockFetchWith({ "/api/desktop/settings": { wallpaper: "midnight" } });

    render(<TestPersistence />);

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

    render(<TestPersistence />);

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

    render(<TestPersistence />);

    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(useDockStore.getState().iconSize).toBe("medium");
    expect(useDockStore.getState().position).toBe("bottom");
  });

  it("resolves pinned ids through APP_REDIRECTS and keeps unresolvable ids", async () => {
    APP_REDIRECTS["legacy-pin"] = { appId: "agents" };

    mockFetchWith({
      "/api/desktop/dock": {
        pinned: ["legacy-pin", "nonexistent-app", "messages"],
        iconSize: "medium",
        position: "bottom",
      },
    });

    render(<TestPersistence />);

    await waitFor(() => {
      const pinned = useDockStore.getState().pinned;
      expect(pinned).toContain("agents");
      expect(pinned).toContain("messages");
      expect(pinned).toContain("nonexistent-app");
      expect(pinned).not.toContain("legacy-pin");
    });

    delete APP_REDIRECTS["legacy-pin"];
  });

  it("preserves pins for userspace apps that sync after dock restore (race condition)", async () => {
    const userspaceAppId = "userspace:test-app";

    mockFetchWith({
      "/api/desktop/dock": {
        pinned: ["messages", userspaceAppId],
        iconSize: "medium",
        position: "bottom",
      },
    });

    render(<TestPersistence />);

    await waitFor(() => {
      const pinned = useDockStore.getState().pinned;
      expect(pinned).toContain(userspaceAppId);
    });

    act(() => {
      (globalThis as Record<string, Map<string, { id: string }>>).__mockApps.set(userspaceAppId, { id: userspaceAppId });
    });

    expect(useDockStore.getState().pinned).toContain(userspaceAppId);

    const setTimeoutSpy = vi.spyOn(globalThis, "setTimeout");

    act(() => {
      useDockStore.getState().setIconSize("large");
    });

    await waitFor(
      () => {
        const putCalls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.filter(
          ([input, init]) =>
            (typeof input === "string" ? input : input.toString()).includes("/api/desktop/dock") &&
            (init as RequestInit | undefined)?.method === "PUT",
        );
        expect(setTimeoutSpy).toHaveBeenCalled();
        expect(putCalls.length).toBeGreaterThan(0);
        const lastBody = JSON.parse((putCalls[putCalls.length - 1]![1] as RequestInit).body as string);
        expect(lastBody.pinned).toContain(userspaceAppId);
      },
      { timeout: 2000 },
    );
  });
});

describe("useSessionPersistence — persistence survives a logout/login cycle (#1601, #1603)", () => {
  it("does not fetch per-user settings before the session is authenticated", async () => {
    useAuthReadyStore.setState({ ready: false });
    mockFetchWith({
      "/api/desktop/settings": { wallpaper: "midnight" },
      "/api/desktop/dock": { iconSize: "large", position: "left" },
    });

    render(<TestPersistence />);

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
    useAuthReadyStore.setState({ ready: false });
    mockFetchWith({
      "/api/desktop/settings": { wallpaper: "midnight" },
      "/api/desktop/dock": { iconSize: "large", position: "left" },
    });

    render(<TestPersistence />);

    expect(useThemeStore.getState().wallpaperId).toBe("graphite");

    act(() => {
      useAuthReadyStore.setState({ ready: true });
    });

    await waitFor(() => {
      expect(useThemeStore.getState().wallpaperId).toBe("midnight");
      expect(useDockStore.getState().iconSize).toBe("large");
      expect(useDockStore.getState().position).toBe("left");
    });

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

    render(<TestPersistence />);
    act(() => {
      useAuthReadyStore.setState({ ready: true });
    });

    await new Promise((r) => setTimeout(r, 700));
    const putCalls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.filter(
      ([input, init]) =>
        (typeof input === "string" ? input : input.toString()).includes("/api/desktop/settings") &&
        (init as RequestInit | undefined)?.method === "PUT",
    );
    expect(putCalls).toHaveLength(0);

    resolveSettings({ wallpaper: "ocean" });
    await waitFor(() => {
      expect(useThemeStore.getState().wallpaperId).toBe("ocean");
    });
  });
});

describe("useSessionPersistence — Dock and wallpaper auto-save write to separate endpoints (#1603, #1601)", () => {
  function putCallsTo(path: string) {
    return (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.filter(
      ([input, init]) =>
        (typeof input === "string" ? input : input.toString()).includes(path) &&
        (init as RequestInit | undefined)?.method === "PUT",
    );
  }

  it("a Dock-only change never PUTs to /api/desktop/settings and leaves the wallpaper out of its own payload", async () => {
    mockFetchWith({
      "/api/desktop/dock": { iconSize: "medium", position: "bottom" },
      "/api/desktop/settings": { wallpaper: "ocean" },
    });

    render(<TestPersistence />);

    await waitFor(() => {
      expect(useThemeStore.getState().wallpaperId).toBe("ocean");
    });

    act(() => {
      useDockStore.getState().setIconSize("large");
      useDockStore.getState().setPosition("left");
    });

    await waitFor(() => expect(putCallsTo("/api/desktop/dock")).not.toHaveLength(0), {
      timeout: 2000,
    });

    expect(putCallsTo("/api/desktop/settings")).toHaveLength(0);

    const dockPuts = putCallsTo("/api/desktop/dock");
    const lastBody = JSON.parse((dockPuts[dockPuts.length - 1]![1] as RequestInit).body as string);
    expect(lastBody).not.toHaveProperty("wallpaper");
  });

  it("a wallpaper-only change never PUTs to /api/desktop/dock and its payload carries only the wallpaper", async () => {
    mockFetchWith({
      "/api/desktop/dock": { iconSize: "large", position: "left" },
      "/api/desktop/settings": { wallpaper: "midnight" },
    });

    render(<TestPersistence />);

    await waitFor(() => {
      expect(useDockStore.getState().iconSize).toBe("large");
    });

    act(() => {
      useThemeStore.getState().setWallpaper("aurora");
    });

    await waitFor(() => expect(putCallsTo("/api/desktop/settings")).not.toHaveLength(0), {
      timeout: 2000,
    });

    expect(putCallsTo("/api/desktop/dock")).toHaveLength(0);

    const settingsPuts = putCallsTo("/api/desktop/settings");
    const lastBody = JSON.parse(
      (settingsPuts[settingsPuts.length - 1]![1] as RequestInit).body as string,
    );
    expect(lastBody).toEqual({ wallpaper: "aurora" });
  });
});

function TestPersistence() {
  useSessionPersistence();
  return null;
}
