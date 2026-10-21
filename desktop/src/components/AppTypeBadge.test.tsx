import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { useThemeStore } from "@/stores/theme-store";
import { AppTypeBadge } from "./AppTypeBadge";

beforeEach(() => {
  useThemeStore.setState({ appTypeIndicator: "badge" });
});

describe("AppTypeBadge", () => {
  it("renders nothing for a taOS app", () => {
    const { container } = render(<AppTypeBadge runtime="taos" />);
    expect(container.innerHTML).toBe("");
  });

  it("renders nothing when indicators are off", () => {
    useThemeStore.setState({ appTypeIndicator: "off" });
    const { container } = render(<AppTypeBadge runtime="linux" />);
    expect(container.innerHTML).toBe("");
  });

  it("renders an accessible label in badge mode", () => {
    render(<AppTypeBadge runtime="linux" />);
    expect(screen.getByRole("img", { name: "Linux app" })).toBeInTheDocument();
  });

  it("renders an accessible label in outline mode", () => {
    useThemeStore.setState({ appTypeIndicator: "outline" });
    render(<AppTypeBadge runtime="agent" />);
    expect(screen.getByRole("img", { name: "Agent app" })).toBeInTheDocument();
  });

  it("renders nothing for taOS in outline mode", () => {
    useThemeStore.setState({ appTypeIndicator: "outline" });
    const { container } = render(<AppTypeBadge runtime="taos" />);
    expect(container.innerHTML).toBe("");
  });

  it("scales the badge chip with size", () => {
    const { container, rerender } = render(<AppTypeBadge runtime="web" size="sm" />);
    expect(container.querySelector("span")?.style.width).toBe("12px");
    rerender(<AppTypeBadge runtime="web" size="lg" />);
    expect(container.querySelector("span")?.style.width).toBe("20px");
  });
});