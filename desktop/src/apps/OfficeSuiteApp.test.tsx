import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { OfficeSuiteApp } from "./OfficeSuiteApp";

function renderApp() {
  return render(<OfficeSuiteApp windowId="test-window" />);
}

describe("OfficeSuiteApp", () => {
  it("renders all rail items", () => {
    renderApp();
    const nav = screen.getByRole("navigation", { name: "Office Suite views" });
    expect(nav).toBeDefined();
    // query within nav to avoid ambiguity with toolbar buttons
    expect(nav.querySelector('[aria-label="Write"]')).toBeTruthy();
    expect(nav.querySelector('[aria-label="Calc"]')).toBeTruthy();
    expect(nav.querySelector('[aria-label="Slides"]')).toBeTruthy();
    expect(nav.querySelector('[aria-label="Data"]')).toBeTruthy();
    expect(nav.querySelector('[aria-label="Assist"]')).toBeTruthy();
  });

  it("shows Write view by default with Write rail item active", () => {
    renderApp();
    const nav = screen.getByRole("navigation", { name: "Office Suite views" });
    const writeBtn = nav.querySelector('[aria-label="Write"]') as HTMLElement;
    expect(writeBtn).toBeTruthy();
    expect(writeBtn.getAttribute("aria-current")).toBe("page");
    expect(screen.getByLabelText("Document title")).toBeDefined();
    expect(screen.getByRole("button", { name: "Save" })).toBeDefined();
  });

  it("switches to Calc view and shows spreadsheet grid with Total row", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Calc" }));
    expect(screen.getByRole("button", { name: "Calc" }).getAttribute("aria-current")).toBe("page");
    // spreadsheet table
    expect(screen.getByRole("table", { name: "Spreadsheet" })).toBeDefined();
    // total row value
    expect(screen.getByTestId("total-revenue")).toBeDefined();
    expect(screen.getByTestId("total-revenue").textContent).toBe("28,900");
    // Total label
    expect(screen.getByText("Total")).toBeDefined();
  });

  it("switches to Slides view and shows slide thumbnails", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Slides" }));
    expect(screen.getByRole("button", { name: "Slides" }).getAttribute("aria-current")).toBe(
      "page",
    );
    // thumbnail rail
    expect(screen.getByRole("complementary", { name: "Slide thumbnails" })).toBeDefined();
    // all 5 slide thumbnails
    expect(screen.getByLabelText("Slide 1: Build it your way")).toBeDefined();
    expect(screen.getByLabelText("Slide 2: Ready today")).toBeDefined();
    expect(screen.getByLabelText("Slide 3: On the way")).toBeDefined();
    expect(screen.getByLabelText("Slide 4: Your hardware")).toBeDefined();
    expect(screen.getByLabelText("Slide 5: Get started")).toBeDefined();
    // slide content
    expect(screen.getByText("Build it your way.")).toBeDefined();
  });
});
