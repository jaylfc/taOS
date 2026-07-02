import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { DesignStudioApp } from "./DesignStudioApp";

function renderApp() {
  return render(<DesignStudioApp windowId="test-window" />);
}

describe("DesignStudioApp", () => {
  it("renders all rail items", () => {
    renderApp();
    const nav = screen.getByRole("navigation", { name: "Design Studio views" });
    expect(nav).toBeDefined();
    expect(screen.getByRole("button", { name: "Design" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Templates" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Elements" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Magic" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Brand" })).toBeDefined();
  });

  it("shows Design view by default with Design rail item active", () => {
    renderApp();
    const nav = screen.getByRole("navigation", { name: "Design Studio views" });
    const designBtn = nav.querySelector('[aria-label="Design"]') as HTMLElement;
    expect(designBtn).toBeTruthy();
    expect(designBtn.getAttribute("aria-current")).toBe("page");
  });

  it("default Design view renders the canvas artboard", () => {
    renderApp();
    expect(screen.getByText("Untitled poster")).toBeDefined();
  });

  it("switches to Templates view and shows template cards", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Templates" }));
    expect(screen.getByRole("button", { name: "Templates" }).getAttribute("aria-current")).toBe(
      "page",
    );
    const expectedCards = [
      "Instagram Post",
      "Story",
      "Poster",
      "Presentation",
      "Logo",
      "Flyer",
      "Banner",
      "Business Card",
    ];
    for (const name of expectedCards) {
      expect(screen.getByText(name)).toBeDefined();
    }
  });

  it("switches to Magic view and shows prompt bar and style chips", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Magic" }));
    expect(screen.getByRole("button", { name: "Magic" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByText("Describe the design you need.")).toBeDefined();
    expect(screen.getByRole("button", { name: "Generate" })).toBeDefined();
    expect(screen.getByPlaceholderText(/launch poster/i)).toBeDefined();
    expect(screen.getAllByText("Editorial").length).toBeGreaterThan(0);
  });
});
