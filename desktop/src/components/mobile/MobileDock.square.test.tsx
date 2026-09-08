import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MobileDock } from "./MobileDock";

/**
 * Drive `window.matchMedia` the way a real device would answer it: report the
 * aspect-ratio query as matching only when the device really is square-ish.
 * The component is rendered unmodified and reached through its own hook, so
 * these assert the shipped behaviour rather than a re-implementation of it.
 */
function mockAspectRatio({ square }: { square: boolean }) {
  window.matchMedia = vi.fn().mockImplementation((q: string) => ({
    matches: q.includes("min-aspect-ratio") ? square : false,
    media: q,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

function renderDock(isBrowserMobile: boolean) {
  render(
    <MobileDock
      onOpenApp={() => {}}
      onToggleSwitcher={() => {}}
      onOpenLaunchpad={() => {}}
      activeAppId={null}
      isBrowserMobile={isBrowserMobile}
    />,
  );
  return screen.getByRole("toolbar", { name: "Dock" });
}

const originalMatchMedia = window.matchMedia;
afterEach(() => {
  window.matchMedia = originalMatchMedia;
});
beforeEach(() => {
  vi.restoreAllMocks();
});

describe("MobileDock bottom clearance", () => {
  it("drops the browser-chrome reserve to the small gap on a square screen", () => {
    // A square display (Titan 2 and similar) has scarce vertical space, so the
    // fixed 54px browser reserve strands the dock above the bottom edge.
    mockAspectRatio({ square: true });
    const dock = renderDock(true);
    expect(dock).toHaveStyle({
      paddingBottom: "calc(env(safe-area-inset-bottom, 0px) + 12px)",
    });
  });

  it("keeps the full browser-chrome reserve on a tall phone", () => {
    // The control. This is the case the 54px was measured for, and it must not
    // move — a test that only checked the square case would pass just as well
    // if the reserve had been deleted for every device.
    mockAspectRatio({ square: false });
    const dock = renderDock(true);
    expect(dock).toHaveStyle({
      paddingBottom: "calc(env(safe-area-inset-bottom, 0px) + 54px)",
    });
  });

  it("uses the small gap in PWA mode regardless of shape", () => {
    // Installed PWAs have no browser chrome to clear, so the reserve never
    // applied there. Pinned so the square-screen branch cannot leak into it.
    for (const square of [true, false]) {
      mockAspectRatio({ square });
      const dock = renderDock(false);
      expect(dock).toHaveStyle({
        paddingBottom: "calc(env(safe-area-inset-bottom, 0px) + 12px)",
      });
      cleanup();
    }
  });
});
