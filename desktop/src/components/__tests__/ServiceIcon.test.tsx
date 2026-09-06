import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ServiceIcon } from "../ServiceIcon";
import type { InstalledService } from "@/hooks/use-installed-services";

const baseService: InstalledService = {
  app_id: "gitea-lxc",
  display_name: "Gitea",
  icon: "/static/app-icons/gitea.svg",
  url: "/apps/gitea-lxc/",
  category: "dev-tool",
  backend: "lxc",
  status: "running",
};

describe("ServiceIcon", () => {
  it("renders the display name", () => {
    render(<ServiceIcon service={baseService} onClick={() => {}} />);
    expect(screen.getByText("Gitea")).toBeTruthy();
  });

  it("renders an img element when icon is provided", () => {
    render(<ServiceIcon service={baseService} onClick={() => {}} />);
    const img = screen.getByRole("img");
    expect(img).toHaveAttribute("src", "/static/app-icons/gitea.svg");
    expect(img).toHaveAttribute("alt", "Gitea");
  });

  it("falls back to generic icon on image load error", () => {
    render(<ServiceIcon service={baseService} onClick={() => {}} />);
    const img = screen.getByRole("img");
    fireEvent.error(img);

    // After error, img should be gone and fallback lucide icon rendered instead
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("renders generic fallback icon when no icon is provided", () => {
    const noIcon: InstalledService = { ...baseService, icon: null };
    render(<ServiceIcon service={noIcon} onClick={() => {}} />);
    // No <img> element — icon fallback (svg) renders instead
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("calls onClick when clicked", () => {
    const handler = vi.fn();
    render(<ServiceIcon service={baseService} onClick={handler} />);
    fireEvent.click(screen.getByRole("button"));
    expect(handler).toHaveBeenCalledOnce();
  });

  it("has a descriptive aria-label", () => {
    render(<ServiceIcon service={baseService} onClick={() => {}} />);
    const btn = screen.getByRole("button");
    expect(btn).toHaveAttribute("aria-label", "Open Gitea");
  });
});
