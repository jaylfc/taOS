import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ServiceIcon } from "./ServiceIcon";
import type { InstalledService } from "@/hooks/use-installed-services";

const baseService: InstalledService = {
  app_id: "test-app",
  display_name: "Test App",
  icon: null,
  url: "http://localhost:3000",
  category: "productivity",
  backend: "docker",
  status: "running",
};

describe("ServiceIcon", () => {
  it("renders the display name and a button with the correct aria-label", () => {
    render(<ServiceIcon service={baseService} onClick={vi.fn()} />);
    expect(screen.getByText("Test App")).toBeInTheDocument();
    const button = screen.getByRole("button", { name: /open test app/i });
    expect(button).toBeInTheDocument();
  });

  it("calls onClick when the button is clicked", () => {
    const onClick = vi.fn();
    render(<ServiceIcon service={baseService} onClick={onClick} />);
    fireEvent.click(screen.getByRole("button", { name: /open test app/i }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("renders the service icon image when icon is provided", () => {
    const serviceWithIcon = { ...baseService, icon: "https://example.com/icon.png" };
    render(<ServiceIcon service={serviceWithIcon} onClick={vi.fn()} />);
    const img = screen.getByRole("img", { name: "Test App" });
    expect(img).toHaveAttribute("src", "https://example.com/icon.png");
  });
});
