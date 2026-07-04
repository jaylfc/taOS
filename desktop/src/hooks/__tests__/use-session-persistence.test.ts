import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useSessionPersistence } from "../use-session-persistence";
import { useDockStore } from "@/stores/dock-store";
import { useThemeStore } from "@/stores/theme-store";

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
