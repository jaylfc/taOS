import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import type { InstalledService } from "@/hooks/use-installed-services";
import { emitAppEvent, APP_INSTALLED } from "@/lib/app-event-bus";

// Mockable list of installed services returned by the hook.
let mockServices: InstalledService[] = [];

vi.mock("@/hooks/use-installed-services", () => ({
  useInstalledServices: () => mockServices,
}));

// Shortcut registry is a no-op in tests.
vi.mock("@/hooks/use-shortcut-registry", () => ({
  useShortcut: () => {},
}));

// Capture window-open calls so we can assert the launch URL.
const openWindow = vi.fn(() => "wid-1");
vi.mock("@/stores/process-store", () => ({
  useProcessStore: () => ({ openWindow }),
}));

// Registry: getAllApps returns no core apps so the test isolates the Services
// section; getApp/getOrRegisterServiceApp echo a minimal manifest.
vi.mock("@/registry/app-registry", () => ({
  getAllApps: () => [],
  getApp: (id: string) => ({ id, defaultSize: { w: 100, h: 100 } }),
  getOrRegisterServiceApp: (appId: string, displayName: string) => ({
    id: `service:${appId}`,
    name: displayName,
    defaultSize: { w: 1100, h: 750 },
  }),
}));

import { Launchpad } from "../Launchpad";

const searxng: InstalledService = {
  app_id: "searxng",
  display_name: "SearXNG",
  icon: null,
  url: "/apps/searxng/",
  category: "infrastructure",
  backend: "docker",
  status: "running",
};

const gitea: InstalledService = {
  app_id: "gitea-lxc",
  display_name: "Gitea",
  icon: "/static/app-icons/gitea.svg",
  url: "/apps/gitea-lxc/",
  category: "dev-tool",
  backend: "lxc",
  status: "running",
};

describe("Launchpad Services section", () => {
  beforeEach(() => {
    mockServices = [];
    openWindow.mockClear();
  });

  it("does not render a Services section when no apps are installed", () => {
    mockServices = [];
    render(<Launchpad open onClose={() => {}} />);
    expect(screen.queryByText("Services")).toBeNull();
  });

  it("renders a Services section with a shortcut per installed app/service", () => {
    mockServices = [searxng, gitea];
    render(<Launchpad open onClose={() => {}} />);

    expect(screen.getByText("Services")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Open SearXNG" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Open Gitea" })).toBeTruthy();
  });

  it("opens the proxied service URL when an app shortcut is launched", () => {
    mockServices = [searxng];
    render(<Launchpad open onClose={() => {}} />);

    fireEvent.click(screen.getByRole("button", { name: "Open SearXNG" }));

    // ServiceAppWindow receives the proxied URL so SearXNG renders its search page.
    expect(openWindow).toHaveBeenCalledWith(
      "service:searxng",
      { w: 1100, h: 750 },
      { url: "/apps/searxng/", displayName: "SearXNG" },
    );
  });

  it("app-event-bus APP_INSTALLED event fires without error", () => {
    // Verify the EventBus module works correctly in isolation:
    // emitting an event should invoke any registered listener.
    const listener = vi.fn();
    const { onAppEvent } = require("@/lib/app-event-bus");
    const unsub = onAppEvent(APP_INSTALLED, listener);
    act(() => { emitAppEvent(APP_INSTALLED, "searxng"); });
    expect(listener).toHaveBeenCalledWith("searxng");
    unsub();
  });
});
