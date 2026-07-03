import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { StudiosView } from "./StudiosView";

afterEach(() => { cleanup(); });

describe("StudiosView", () => {
  it("renders the three section headings", () => {
    render(<StudiosView />);
    expect(screen.getByText("taOS Studios")).toBeInTheDocument();
    expect(screen.getByText("Community Studios")).toBeInTheDocument();
    expect(screen.getByText("Studio layouts")).toBeInTheDocument();
  });

  it("renders all taOS studio card names", () => {
    render(<StudiosView />);
    const expected = [
      "Images Studio",
      "Game Studio",
      "Coding Studio",
      "Design Studio",
      "Music Studio",
      "App Studio",
      "Office Studio",
      "Web Studio",
    ];
    for (const name of expected) {
      expect(screen.getAllByText(name).length).toBeGreaterThan(0);
    }
  });

  it("shows a Soon badge on the unreleased studio", () => {
    render(<StudiosView />);
    // Only Web Studio is still "soon"; Coding/Design/Music/App/Office are in beta.
    const soonBadges = screen.getAllByText("Soon");
    expect(soonBadges.length).toBe(1);
  });

  it("shows Coding Studio in the hero section with the featured eyebrow", () => {
    render(<StudiosView />);
    // The eyebrow text calls it out as featured
    expect(screen.getByText(/Featured/i)).toBeInTheDocument();
    // Coding Studio name appears at least twice (hero heading + grid card)
    expect(screen.getAllByText("Coding Studio").length).toBeGreaterThanOrEqual(2);
  });

  it("renders community studio names", () => {
    render(<StudiosView />);
    expect(screen.getByText("Pixel Art Studio")).toBeInTheDocument();
    expect(screen.getByText("Lo-fi Beats Kit")).toBeInTheDocument();
    expect(screen.getByText("API Forge")).toBeInTheDocument();
    expect(screen.getByText("Retro FPS Kit")).toBeInTheDocument();
  });

  it("renders layout chip names", () => {
    render(<StudiosView />);
    expect(screen.getByText("Photo Retoucher")).toBeInTheDocument();
    expect(screen.getByText("Chiptune")).toBeInTheDocument();
    expect(screen.getByText("Static Site Kit")).toBeInTheDocument();
  });
});
