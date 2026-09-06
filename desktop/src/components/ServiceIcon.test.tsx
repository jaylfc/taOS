import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

const mockOnClick = vi.fn();

const baseService = {
  app_id: "test-app",
  display_name: "Test App",
  icon: "https://example.com/icon.png",
  url: "https://example.com",
  category: "productivity",
  backend: "docker",
  status: "running" as const,
};

import { ServiceIcon } from "./ServiceIcon";

describe("ServiceIcon", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the display name as visible text", () => {
    render(<ServiceIcon service={baseService} onClick={mockOnClick} />);
    expect(screen.getByText("Test App")).toBeInTheDocument();
  });

  it("renders a button with an accessible label containing the display name", () => {
    render(<ServiceIcon service={baseService} onClick={mockOnClick} />);
    const button = screen.getByRole("button", { name: /open test app/i });
    expect(button).toBeInTheDocument();
  });

  it("calls onClick when the button is clicked", () => {
    render(<ServiceIcon service={baseService} onClick={mockOnClick} />);
    fireEvent.click(screen.getByRole("button", { name: /open test app/i }));
    expect(mockOnClick).toHaveBeenCalledTimes(1);
  });

  it("renders the service icon image when icon is provided", () => {
    render(<ServiceIcon service={baseService} onClick={mockOnClick} />);
    const img = screen.getByRole("img", { name: /test app/i });
    expect(img).toBeInTheDocument();
    expect(img).toHaveAttribute("src", "https://example.com/icon.png");
  });

  it("falls back to the generic grid icon when icon is null", () => {
    const noIconService = { ...baseService, icon: null };
    const { container } = render(
      <ServiceIcon service={noIconService} onClick={mockOnClick} />
    );
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(container.querySelector("svg")).toBeInTheDocument();
  });

  it("falls back to the generic grid icon when the image fails to load", () => {
    const { container } = render(
      <ServiceIcon service={baseService} onClick={mockOnClick} />
    );
    const img = screen.getByRole("img", { name: /test app/i });
    fireEvent.error(img);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(container.querySelector("svg")).toBeInTheDocument();
  });

  it("truncates long display names with ellipsis", () => {
    const longNameService = {
      ...baseService,
      display_name: "A Very Long Service Name That Exceeds The Max Width",
    };
    render(<ServiceIcon service={longNameService} onClick={mockOnClick} />);
    const span = screen.getByText(
      "A Very Long Service Name That Exceeds The Max Width"
    );
    expect(span).toHaveClass("truncate");
  });
});
