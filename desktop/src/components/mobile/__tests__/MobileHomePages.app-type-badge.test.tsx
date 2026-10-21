import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MobileHomePages } from "../MobileHomePages";
import { useMobileHomeStore } from "@/stores/mobile-home-store";
import { syncUserspaceApps, type AppManifest } from "@/registry/app-registry";
import { useThemeStore } from "@/stores/theme-store";

vi.mock("@/hooks/use-installed-optional-apps", () => ({
  useInstalledOptionalApps: () => new Set<string>(),
}));

const linuxApp: AppManifest = {
  id: "userspace:gimp",
  name: "GIMP",
  icon: "image",
  category: "platform",
  component: () => Promise.resolve({ default: () => null }),
  defaultSize: { w: 800, h: 600 },
  minSize: { w: 400, h: 300 },
  singleton: true,
  pinned: false,
  launchpadOrder: 999,
  runtime: "linux",
};

describe("MobileHomePages app type badge", () => {
  const initialPages = useMobileHomeStore.getState().pages;

  beforeEach(() => {
    localStorage.clear();
    useThemeStore.setState({ appTypeIndicator: "badge" });
    syncUserspaceApps([linuxApp]);
    useMobileHomeStore.setState({
      activePageIndex: 0,
      pages: [{ items: [{ type: "app", appId: "userspace:gimp" }, { type: "app", appId: "files" }] }],
    });
  });
  afterEach(() => {
    cleanup();
    syncUserspaceApps([]);
    useMobileHomeStore.setState({ pages: initialPages, activePageIndex: 0 });
  });

  it("badges a linux-runtime app with the Linux app label", () => {
    render(<MobileHomePages onOpenApp={() => {}} />);
    const gimp = screen.getByRole("button", { name: "GIMP" });
    expect(screen.getAllByRole("img", { name: "Linux app" })).toHaveLength(1);
    expect(gimp.querySelector('[role="img"][aria-label="Linux app"]')).not.toBeNull();
  });

  it("renders no badge on a built-in taOS app", () => {
    render(<MobileHomePages onOpenApp={() => {}} />);
    const files = screen.getByRole("button", { name: "Files" });
    expect(files.querySelector('[role="img"]')).toBeNull();
  });
});
