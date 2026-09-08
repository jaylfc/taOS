import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { AppManifest } from "@/registry/app-registry";

const prefetchApp = vi.fn();

vi.mock("@/registry/app-registry", () => ({
  prefetchApp: (id: string) => prefetchApp(id),
  getApp: (id: string) => ({ id, name: "Browser", icon: "globe" }),
  // No redirects in this fixture: a pin id is its own app id and carries no
  // section. Mirrors the real contract rather than stubbing it away (#2677).
  APP_REDIRECTS: {},
  resolvePinnedId: (id: string) => ({ id }),
  pinnedAppId: (id: string) => id,
  pinnedLaunchProps: () => undefined,
}));

import { DockIcon } from "./DockIcon";
import { LaunchpadIcon } from "./LaunchpadIcon";

const app = { id: "browser", name: "Browser", icon: "globe" } as AppManifest;

describe("icon prefetch wiring", () => {
  beforeEach(() => prefetchApp.mockClear());

  it("DockIcon prefetches on hover", () => {
    render(<DockIcon appId="browser" isRunning={false} onClick={() => {}} />);
    const btn = screen.getByRole("button", { name: "Open Browser" });

    fireEvent.mouseEnter(btn);
    expect(prefetchApp).toHaveBeenCalledWith("browser");
  });

  it("LaunchpadIcon prefetches on hover", () => {
    render(<LaunchpadIcon app={app} onClick={() => {}} />);
    const btn = screen.getByRole("button", { name: "Open Browser" });

    fireEvent.mouseEnter(btn);
    expect(prefetchApp).toHaveBeenCalledWith("browser");
  });

  it("does not change click behavior", () => {
    const onClick = vi.fn();
    render(<DockIcon appId="browser" isRunning={false} onClick={onClick} />);
    fireEvent.click(screen.getByRole("button", { name: "Open Browser" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
