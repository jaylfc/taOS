import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { WallpaperTextOverlay } from "./WallpaperTextOverlay";

describe("WallpaperTextOverlay", () => {
  it("renders the provided text", () => {
    render(<WallpaperTextOverlay text="Hello World" />);
    expect(screen.getByText("Hello World")).toBeInTheDocument();
  });

  it("renders text in a centered overlay container", () => {
    render(<WallpaperTextOverlay text="taOS" />);
    const container = screen.getByText("taOS").parentElement;
    expect(container).toHaveClass("pointer-events-none", "absolute", "inset-0", "z-0", "grid", "place-items-center");
  });

  it("hides the overlay from assistive technology", () => {
    render(<WallpaperTextOverlay text="hidden" />);
    const container = screen.getByText("hidden").parentElement;
    expect(container).toHaveAttribute("aria-hidden", "true");
  });

  it("applies the expected text styling", () => {
    render(<WallpaperTextOverlay text="styled" />);
    const span = screen.getByText("styled");
    expect(span).toHaveClass("font-semibold", "tracking-tight");
    expect(span).toHaveStyle({ color: "rgba(236,236,238,0.96)" });
  });
});
