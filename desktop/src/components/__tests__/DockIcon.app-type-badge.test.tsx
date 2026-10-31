import { render, screen, cleanup } from "@testing-library/react";
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { DockIcon } from "../DockIcon";
import { syncUserspaceApps, type AppManifest } from "@/registry/app-registry";
import { useThemeStore } from "@/stores/theme-store";

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

describe("DockIcon app type badge", () => {
  beforeEach(() => {
    useThemeStore.setState({ appTypeIndicator: "badge" });
    syncUserspaceApps([linuxApp]);
  });
  afterEach(() => {
    cleanup();
    syncUserspaceApps([]);
  });

  it("shows the Linux app badge for a linux-runtime manifest", () => {
    render(<DockIcon appId="userspace:gimp" isRunning={false} onClick={() => {}} />);
    expect(screen.getByRole("img", { name: "Linux app" })).toBeInTheDocument();
  });

  it("shows no badge for a built-in taOS app", () => {
    render(<DockIcon appId="files" isRunning={false} onClick={() => {}} />);
    expect(screen.getByRole("button", { name: "Open Files" })).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: / app$/ })).toBeNull();
  });
});
