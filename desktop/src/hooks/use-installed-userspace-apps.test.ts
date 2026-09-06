import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useInstalledUserspaceApps } from "./use-installed-userspace-apps";

vi.mock("@/lib/userspace-apps", () => ({
  fetchUserspaceApps: vi.fn(),
  USERSPACE_APPS_CHANGED: "taos:userspace-apps-changed",
}));

vi.mock("@/registry/app-registry", () => ({
  syncUserspaceApps: vi.fn(),
}));

vi.mock("@/lib/app-event-bus", () => ({
  onAppEvent: vi.fn().mockReturnValue(() => {}),
}));

import { fetchUserspaceApps } from "@/lib/userspace-apps";
import { syncUserspaceApps } from "@/registry/app-registry";

const mockedFetchUserspaceApps = vi.mocked(fetchUserspaceApps);
const mockedSyncUserspaceApps = vi.mocked(syncUserspaceApps);

describe("useInstalledUserspaceApps", () => {
  beforeEach(() => {
    mockedFetchUserspaceApps.mockClear();
    mockedSyncUserspaceApps.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns an empty array on initial render", () => {
    mockedFetchUserspaceApps.mockResolvedValueOnce([]);
    const { result } = renderHook(() => useInstalledUserspaceApps());
    expect(result.current).toEqual([]);
  });

  it("populates apps after a successful fetch", async () => {
    const manifests = [
      {
        id: "userspace:app-1",
        name: "App One",
        icon: "layout-grid",
        category: "userspace" as const,
        component: () => Promise.resolve({ default: () => null }),
        defaultSize: { w: 900, h: 600 },
        minSize: { w: 360, h: 280 },
        singleton: true,
        pinned: false,
        launchpadOrder: 100,
      },
      {
        id: "userspace:app-2",
        name: "App Two",
        icon: "layout-grid",
        category: "userspace" as const,
        component: () => Promise.resolve({ default: () => null }),
        defaultSize: { w: 900, h: 600 },
        minSize: { w: 360, h: 280 },
        singleton: true,
        pinned: false,
        launchpadOrder: 100,
      },
    ];
    mockedFetchUserspaceApps.mockResolvedValueOnce(manifests);

    const { result } = renderHook(() => useInstalledUserspaceApps());

    await waitFor(() => {
      expect(result.current).toHaveLength(2);
    });

    expect(mockedSyncUserspaceApps).toHaveBeenCalledWith(manifests);
    expect(result.current.map((m) => m.id)).toEqual([
      "userspace:app-1",
      "userspace:app-2",
    ]);
  });

  it("returns an empty array when fetch rejects", async () => {
    mockedFetchUserspaceApps.mockRejectedValueOnce(new Error("network"));

    const { result } = renderHook(() => useInstalledUserspaceApps());

    await waitFor(() => {
      expect(mockedFetchUserspaceApps).toHaveBeenCalled();
    });

    expect(result.current).toEqual([]);
  });

  it("re-fetches when USERSPACE_APPS_CHANGED event fires", async () => {
    const firstBatch = [
      {
        id: "userspace:app-1",
        name: "App One",
        icon: "layout-grid",
        category: "userspace" as const,
        component: () => Promise.resolve({ default: () => null }),
        defaultSize: { w: 900, h: 600 },
        minSize: { w: 360, h: 280 },
        singleton: true,
        pinned: false,
        launchpadOrder: 100,
      },
    ];
    const secondBatch = [
      {
        id: "userspace:app-1",
        name: "App One",
        icon: "layout-grid",
        category: "userspace" as const,
        component: () => Promise.resolve({ default: () => null }),
        defaultSize: { w: 900, h: 600 },
        minSize: { w: 360, h: 280 },
        singleton: true,
        pinned: false,
        launchpadOrder: 100,
      },
      {
        id: "userspace:app-3",
        name: "App Three",
        icon: "layout-grid",
        category: "userspace" as const,
        component: () => Promise.resolve({ default: () => null }),
        defaultSize: { w: 900, h: 600 },
        minSize: { w: 360, h: 280 },
        singleton: true,
        pinned: false,
        launchpadOrder: 100,
      },
    ];

    mockedFetchUserspaceApps.mockResolvedValueOnce(firstBatch);

    let eventHandler: (() => void) | undefined;
    const { onAppEvent } = await import("@/lib/app-event-bus");
    (onAppEvent as ReturnType<typeof vi.fn>).mockImplementation(
      (_name: string, handler: () => void) => {
        eventHandler = handler;
        return () => {};
      },
    );

    const { result } = renderHook(() => useInstalledUserspaceApps());

    await waitFor(() => {
      expect(result.current).toHaveLength(1);
    });

    mockedFetchUserspaceApps.mockResolvedValueOnce(secondBatch);

    eventHandler!();

    await waitFor(() => {
      expect(result.current).toHaveLength(2);
    });

    expect(mockedSyncUserspaceApps).toHaveBeenCalledTimes(2);
    expect(result.current.map((m) => m.id)).toEqual([
      "userspace:app-1",
      "userspace:app-3",
    ]);
  });
});
