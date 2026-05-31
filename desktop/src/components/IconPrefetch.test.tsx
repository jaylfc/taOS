import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { AppManifest } from "@/registry/app-registry";

const prefetchApp = vi.fn();

vi.mock("@/registry/app-registry", () => ({
  prefetchApp: (id: string) => prefetchApp(id),
  getApp: (id: string) => ({ id, name: "Browser", icon: "globe" }),
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
