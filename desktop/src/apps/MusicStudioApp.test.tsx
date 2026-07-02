import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { MusicStudioApp } from "./MusicStudioApp";

function renderApp() {
  return render(<MusicStudioApp windowId="test-window" />);
}

describe("MusicStudioApp", () => {
  it("renders all rail items", () => {
    renderApp();
    const nav = screen.getByRole("navigation", { name: "Music Studio views" });
    expect(nav).toBeDefined();
    expect(screen.getByRole("button", { name: "Studio" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Compose" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Sounds" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Mixer" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Export" })).toBeDefined();
  });

  it("shows Studio view by default with Studio rail item active", () => {
    renderApp();
    const nav = screen.getByRole("navigation", { name: "Music Studio views" });
    const studioBtn = nav.querySelector('[aria-label="Studio"]') as HTMLElement;
    expect(studioBtn).toBeTruthy();
    expect(studioBtn.getAttribute("aria-current")).toBe("page");
  });

  it("Studio view shows transport controls", () => {
    renderApp();
    expect(screen.getByRole("button", { name: "Stop" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Play" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Record" })).toBeDefined();
  });

  it("Studio view shows a track in the track list", () => {
    renderApp();
    expect(screen.getAllByText("Drums").length).toBeGreaterThan(0);
  });

  it("switches to Compose view on rail click", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Compose" }));
    const nav = screen.getByRole("navigation", { name: "Music Studio views" });
    const composeBtn = nav.querySelector('[aria-label="Compose"]') as HTMLElement;
    expect(composeBtn.getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("heading", { name: "Compose" })).toBeDefined();
  });

  it("Compose view shows Generate button and style chips", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Compose" }));
    expect(screen.getByRole("button", { name: "Generate" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Lo-fi" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Cinematic" })).toBeDefined();
  });

  it("switches to Sounds view on rail click", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Sounds" }));
    const nav = screen.getByRole("navigation", { name: "Music Studio views" });
    const soundsBtn = nav.querySelector('[aria-label="Sounds"]') as HTMLElement;
    expect(soundsBtn.getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("heading", { name: "Sounds" })).toBeDefined();
  });

  it("Sounds view shows filter pills and instrument cards", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Sounds" }));
    expect(screen.getByRole("button", { name: "All" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Drums" })).toBeDefined();
    expect(screen.getByText("Boom Bap Kit")).toBeDefined();
    expect(screen.getByText("Rhodes Mk I")).toBeDefined();
  });
});
