/**
 * Acceptance for the legacy `notification-archive` dock pin, driven through
 * the path a user actually takes: the saved payload from /api/desktop/dock ->
 * session restore -> a click on the dock icon -> the window that click opens
 * -> the app that window renders.
 *
 * Nothing between the payload and the app is stubbed: the app registry, the
 * dock store, the process store and the Dock component are all real. A
 * regression anywhere along the chain (a restore that normalises the pin id
 * away, a launch that lands on the wrong app) turns this red (#2677).
 *
 * The Notifications Archive tab is gone (tsk-i5gygp): the pin now opens the
 * standalone Notification History app, with no section.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import React from "react";
import { Dock } from "@/components/Dock";
import { NotificationHistoryApp } from "../NotificationHistoryApp";
import { useSessionPersistence } from "@/hooks/use-session-persistence";
import { useDockStore } from "@/stores/dock-store";
import { useProcessStore } from "@/stores/process-store";
import { useAuthReadyStore } from "@/stores/auth-ready-store";

vi.mock("@/lib/browser-windows-api", () => ({
  loadWindows: async () => [],
  saveWindows: async () => {},
}));

vi.mock("@/components/SetupChecklist", () => ({ SetupChecklist: () => null }));
vi.mock("@/components/ConsentActions", () => ({
  ConsentActions: () => null,
  consentPayload: () => undefined,
}));

const SAVED_DOCK = { pinned: ["notification-archive"], iconSize: "medium", position: "bottom" };

const MockEventSourceCtor = vi.fn().mockImplementation(function (this: any) {
  this.url = "";
  this.onopen = null;
  this.onmessage = null;
  this.onerror = null;
  this.close = vi.fn();
  this.readyState = 0;
});
Object.assign(MockEventSourceCtor, { CONNECTING: 0, OPEN: 1, CLOSED: 2 });

function mockFetch(dock: Record<string, unknown> = SAVED_DOCK) {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const body = url.includes("/api/desktop/dock")
      ? dock
      : url.includes("/api/notifications/history/sources")
        ? { sources: [] }
        : url.includes("/api/notifications/history")
          ? { items: [], next_before: null }
          : [];
    return Promise.resolve(
      new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } }),
    );
  });
}

/** The desktop shell as far as this path needs it: restore plus the dock. */
function DesktopShell() {
  useSessionPersistence();
  return <Dock onLaunchpadOpen={() => {}} />;
}

function putBodiesTo(path: string): Record<string, unknown>[] {
  return (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
    .filter(
      ([input, init]) =>
        (typeof input === "string" ? input : String(input)).includes(path) &&
        (init as RequestInit | undefined)?.method === "PUT",
    )
    .map(([, init]) => JSON.parse((init as RequestInit).body as string));
}

beforeEach(() => {
  useDockStore.setState({ pinned: ["messages"], iconSize: "medium", position: "bottom" });
  useProcessStore.setState({ windows: [] });
  useAuthReadyStore.setState({ ready: true });
  vi.stubGlobal("EventSource", MockEventSourceCtor);
  MockEventSourceCtor.mockClear();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("legacy notification-archive dock pin (#2677)", () => {
  it("opens the Notification History app when the restored pin is clicked", async () => {
    mockFetch();

    render(<DesktopShell />);

    const icon = await screen.findByLabelText("Open Notification History");
    fireEvent.click(icon);

    const win = useProcessStore.getState().windows.find((w) => w.appId === "notification-history");
    expect(win).toBeDefined();
    expect(useProcessStore.getState().windows.some((w) => w.appId === "notifications")).toBe(false);
    expect(win!.props ?? {}).not.toHaveProperty("section");

    render(<NotificationHistoryApp windowId={win!.id} />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Notification History" })).toBeInTheDocument();
    });
  });

  it("writes the legacy pin back to the server unchanged", async () => {
    mockFetch();

    render(<DesktopShell />);

    await screen.findByLabelText("Open Notification History");

    act(() => {
      useDockStore.getState().setIconSize("large");
    });

    await waitFor(
      () => {
        const bodies = putBodiesTo("/api/desktop/dock");
        expect(bodies.length).toBeGreaterThan(0);
        expect(bodies[bodies.length - 1]!.pinned).toEqual(["notification-archive"]);
      },
      { timeout: 2000 },
    );
  });

  it("shows one dock icon for the pin, not a second one once its window is open", async () => {
    mockFetch();

    render(<DesktopShell />);

    fireEvent.click(await screen.findByLabelText("Open Notification History"));

    await waitFor(() => {
      expect(screen.getAllByLabelText("Open Notification History")).toHaveLength(1);
    });
  });
});
