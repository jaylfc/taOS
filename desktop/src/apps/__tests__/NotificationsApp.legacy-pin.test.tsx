/**
 * Acceptance for the legacy `notification-archive` dock pin, driven through
 * the path a user actually takes: the saved payload from /api/desktop/dock ->
 * session restore -> a click on the dock icon -> the window that click opens
 * -> the Notifications app that window renders.
 *
 * Nothing between the payload and the app is stubbed: the app registry, the
 * dock store, the process store and the Dock component are all real. A
 * regression anywhere along the chain — a restore that normalises the pin id
 * away, a launch that drops the section, an app that ignores it — turns this
 * red. Asserting on the rendered Archive tab alone would not: that already
 * worked before the pin ever reached it (#2677).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import React from "react";
import { Dock } from "@/components/Dock";
import { NotificationsApp } from "../NotificationsApp";
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

function mockFetch(dock: Record<string, unknown> = SAVED_DOCK) {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const body = url.includes("/api/desktop/dock") ? dock : [];
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
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("legacy notification-archive dock pin (#2677)", () => {
  it("opens the Notifications Archive tab when the restored pin is clicked", async () => {
    mockFetch();

    render(<DesktopShell />);

    const icon = await screen.findByLabelText("Open Notifications");
    fireEvent.click(icon);

    const win = useProcessStore.getState().windows.find((w) => w.appId === "notifications");
    expect(win).toBeDefined();
    expect(win!.props).toEqual({ section: "archive" });

    // The window's props are what the shell hands the app, so render the app
    // exactly as Window.tsx does — the section is only fixed if it lands on
    // the Archive tab, not merely if it survives to the window.
    render(<NotificationsApp windowId={win!.id} {...(win!.props as { section?: string })} />);

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /archive/i })).toHaveAttribute("data-state", "active");
    });
  });

  it("writes the legacy pin back to the server unchanged", async () => {
    mockFetch();

    render(<DesktopShell />);

    await screen.findByLabelText("Open Notifications");

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

    fireEvent.click(await screen.findByLabelText("Open Notifications"));

    await waitFor(() => {
      expect(screen.getAllByLabelText("Open Notifications")).toHaveLength(1);
    });
  });
});
