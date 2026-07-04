import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";
import { AppStudioApp } from "./AppStudioApp";

describe("AppStudioApp", () => {
  it("renders all rail items", () => {
    const { container } = render(<AppStudioApp windowId="test" />);
    const nav = container.querySelector("nav[aria-label='App Studio views']");
    expect(nav).toBeInTheDocument();
    // Rail buttons are inside the nav
    const railBtns = nav!.querySelectorAll("button");
    const labels = Array.from(railBtns).map((b) => b.getAttribute("aria-label"));
    expect(labels).toContain("Build");
    expect(labels).toContain("Templates");
    expect(labels).toContain("Publish");
    expect(labels).toContain("SDK");
  });

  it("shows Build view by default", () => {
    render(<AppStudioApp windowId="test" />);
    expect(screen.getByRole("heading", { name: /^build$/i })).toBeInTheDocument();
    // checkerboard sandbox area has the live preview header text
    expect(screen.getByText("Build log")).toBeInTheDocument();
  });

  it("switches to Templates view and shows template cards", () => {
    const { container } = render(<AppStudioApp windowId="test" />);
    const nav = container.querySelector("nav[aria-label='App Studio views']")!;
    const templatesBtn = Array.from(nav.querySelectorAll("button")).find(
      (b) => b.getAttribute("aria-label") === "Templates"
    )!;
    fireEvent.click(templatesBtn);
    // hero heading
    expect(screen.getByText(/build a taOS app in plain words/i)).toBeInTheDocument();
    // template card labels
    expect(screen.getByText("Dashboard")).toBeInTheDocument();
    expect(screen.getByText("Tracker")).toBeInTheDocument();
    expect(screen.getByText("Kanban")).toBeInTheDocument();
    expect(screen.getByText("Blank")).toBeInTheDocument();
  });

  it("switches to Publish view and shows the empty state before an app is built", () => {
    const { container } = render(<AppStudioApp windowId="test" />);
    const nav = container.querySelector("nav[aria-label='App Studio views']")!;
    const publishBtn = Array.from(nav.querySelectorAll("button")).find(
      (b) => b.getAttribute("aria-label") === "Publish"
    )!;
    fireEvent.click(publishBtn);
    expect(screen.getByText(/generate an app in the build view first/i)).toBeInTheDocument();
  });
});
