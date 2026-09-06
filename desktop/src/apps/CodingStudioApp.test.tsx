import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { CodingStudioApp } from "./CodingStudioApp";

function renderApp() {
  return render(<CodingStudioApp windowId="test-window" />);
}

describe("CodingStudioApp", () => {
  it("renders all rail items", () => {
    renderApp();
    // Rail buttons use aria-label for exact matching via the nav element
    const nav = screen.getByRole("navigation", { name: "Coding Studio views" });
    expect(nav).toBeDefined();
    expect(screen.getByRole("button", { name: "Code" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Preview" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Templates" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Models" })).toBeDefined();
  });

  it("shows Build view by default with Build rail item active", () => {
    renderApp();
    // The rail Build button (inside nav) should be aria-current="page"
    const nav = screen.getByRole("navigation", { name: "Coding Studio views" });
    const railBuildBtn = nav.querySelector('[aria-label="Build"]') as HTMLElement;
    expect(railBuildBtn).toBeTruthy();
    expect(railBuildBtn.getAttribute("aria-current")).toBe("page");
  });

  it("switches to Templates view on rail click", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Templates" }));
    expect(screen.getByRole("button", { name: "Templates" }).getAttribute("aria-current")).toBe(
      "page",
    );
    expect(screen.getByText("Describe what you want to build.")).toBeDefined();
  });

  it("Templates view shows all 8 template cards", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Templates" }));
    const expectedNames = [
      "Web App",
      "REST API",
      "CLI Tool",
      "Discord Bot",
      "Static Site",
      "Data Pipeline",
      "Python Script",
      "Browser Extension",
    ];
    for (const name of expectedNames) {
      expect(screen.getByText(name)).toBeDefined();
    }
  });

  it("switches to Preview view on rail click and shows preview header", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(screen.getByRole("button", { name: "Preview" }).getAttribute("aria-current")).toBe(
      "page",
    );
    // Preview header h2
    expect(screen.getByRole("heading", { name: "Preview" })).toBeDefined();
  });
});
