import { render, screen, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { ComponentType } from "react";

// The registry mock is defined per-test via vi.mock hoisting; we override
// getApp inside each test group instead.

const FakeApp = ({ windowId }: { windowId: string }) => (
  <div data-testid="fake-app" data-window={windowId}>
    app content
  </div>
);

// Default registry mock: messages is pwa-enabled, files is a registered app
// WITHOUT pwa, unknown is not present.
vi.mock("./registry/app-registry", () => ({
  getApp: vi.fn((id: string) => {
    if (id === "messages" || id === "files") {
      return {
        id,
        name: id === "messages" ? "Messages" : "Files",
        pwa: id === "messages" ? true : undefined,
        component: () => Promise.resolve({ default: FakeApp as ComponentType<{ windowId: string }> }),
      };
    }
    return undefined;
  }),
}));

// InstallPromptBanner has side-effects (window.matchMedia, beforeinstallprompt)
// that are not relevant to these tests; stub it with a spy so the pwa gate on
// the install surface is observable.
const { bannerSpy } = vi.hoisted(() => ({ bannerSpy: vi.fn(() => null) }));
vi.mock("./shell/InstallPromptBanner", () => ({
  InstallPromptBanner: bannerSpy,
}));

import { AppStandalone } from "./AppStandalone";

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("AppStandalone", () => {
  beforeEach(() => bannerSpy.mockClear());

  it("renders the app component for a known pwa-enabled app", async () => {
    render(<AppStandalone appId="messages" />);
    await flush();
    expect(screen.getByTestId("fake-app")).toBeTruthy();
    expect(screen.getByTestId("fake-app").getAttribute("data-window")).toBe("standalone-messages");
  });

  it("renders a registered app that is NOT pwa-enabled", async () => {
    render(<AppStandalone appId="files" />);
    await flush();
    expect(screen.getByTestId("fake-app").getAttribute("data-window")).toBe("standalone-files");
  });

  it("shows the install prompt only for a pwa-enabled app", async () => {
    render(<AppStandalone appId="files" />);
    await flush();
    expect(bannerSpy).not.toHaveBeenCalled();
    render(<AppStandalone appId="messages" />);
    await flush();
    expect(bannerSpy).toHaveBeenCalled();
  });

  it("renders nothing when the app id is not in the registry", () => {
    render(<AppStandalone appId="does-not-exist" />);
    expect(screen.queryByTestId("fake-app")).toBeNull();
  });
});
