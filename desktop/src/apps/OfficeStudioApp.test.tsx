import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { OfficeStudioApp } from "./OfficeStudioApp";

function renderApp() {
  return render(<OfficeStudioApp windowId="test-window" />);
}

describe("OfficeStudioApp", () => {
  it("renders all rail items", () => {
    renderApp();
    const nav = screen.getByRole("navigation", { name: "Office Studio views" });
    expect(nav).toBeDefined();
    // query within nav to avoid ambiguity with toolbar buttons
    expect(nav.querySelector('[aria-label="Write"]')).toBeTruthy();
    expect(nav.querySelector('[aria-label="Calc"]')).toBeTruthy();
    expect(nav.querySelector('[aria-label="Slides"]')).toBeTruthy();
    expect(nav.querySelector('[aria-label="Assist"]')).toBeTruthy();
  });

  it("shows Write view by default with Write rail item active", () => {
    renderApp();
    const nav = screen.getByRole("navigation", { name: "Office Studio views" });
    const writeBtn = nav.querySelector('[aria-label="Write"]') as HTMLElement;
    expect(writeBtn).toBeTruthy();
    expect(writeBtn.getAttribute("aria-current")).toBe("page");
    expect(screen.getByLabelText("Document title")).toBeDefined();
    expect(screen.getByRole("button", { name: "Save" })).toBeDefined();
  });

  it("renders the Tiptap document body with an accessible label", () => {
    renderApp();
    const body = screen.getByRole("textbox", { name: "Document body" });
    expect(body).toBeDefined();
    expect(body.getAttribute("contenteditable")).toBe("true");
  });

  it("toggles Bold on and off from the toolbar", () => {
    renderApp();
    const boldBtn = screen.getByRole("button", { name: "Bold" });
    expect(boldBtn.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(boldBtn);
    expect(boldBtn.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(boldBtn);
    expect(boldBtn.getAttribute("aria-pressed")).toBe("false");
  });

  it("typing into the document body updates the editor content", () => {
    renderApp();
    const body = screen.getByRole("textbox", { name: "Document body" });
    body.focus();
    body.innerHTML = "<p>Hello world</p>";
    fireEvent.input(body);
    expect(body.textContent).toContain("Hello world");
  });

  it("switches to Calc view and shows the real spreadsheet chrome", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Calc" }));
    expect(screen.getByRole("button", { name: "Calc" }).getAttribute("aria-current")).toBe("page");
    // formula bar reflects the active cell (A1 on a fresh workbook)
    expect(screen.getByLabelText("Formula for cell A1")).toBeDefined();
    // default sheet tab
    expect(screen.getByRole("button", { name: "Sheet1" })).toBeDefined();
    expect(screen.getByLabelText("Add sheet")).toBeDefined();
    // workbook title + save/new actions
    expect(screen.getByLabelText("Workbook title")).toBeDefined();
    expect(screen.getByRole("button", { name: /Save/ })).toBeDefined();
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
