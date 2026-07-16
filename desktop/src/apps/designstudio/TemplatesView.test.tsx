import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TemplatesView } from "./TemplatesView";

describe("TemplatesView", () => {
  it("renders the template grid and filter pills", () => {
    render(<TemplatesView onSelectTemplate={vi.fn()} />);
    expect(screen.getByText("Instagram Post")).toBeDefined();
    expect(screen.getByText("Presentation")).toBeDefined();
    expect(screen.getByRole("button", { name: "All" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Logos" })).toBeDefined();
  });

  it("selecting a template reports its name and dimensions", () => {
    const onSelect = vi.fn();
    render(<TemplatesView onSelectTemplate={onSelect} />);
    fireEvent.click(screen.getByText("Instagram Post"));
    expect(onSelect).toHaveBeenCalledWith({ name: "Instagram Post", width: 1080, height: 1080 });
  });

  it("reports the correct dimensions for a non-square template", () => {
    const onSelect = vi.fn();
    render(<TemplatesView onSelectTemplate={onSelect} />);
    fireEvent.click(screen.getByText("Presentation"));
    expect(onSelect).toHaveBeenCalledWith({ name: "Presentation", width: 1920, height: 1080 });
  });

  it("does not fire selection when only switching filter pills", () => {
    const onSelect = vi.fn();
    render(<TemplatesView onSelectTemplate={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: "Posters" }));
    expect(onSelect).not.toHaveBeenCalled();
  });
});
