import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { CrosswordsApp } from "./CrosswordsApp";

function flush() {
  return act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

describe("CrosswordsApp", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders the grid, clues, and toolbar", async () => {
    render(<CrosswordsApp windowId="win-cw-1" />);
    await flush();

    expect(screen.getByText(/crossword #1/i)).toBeTruthy();
    expect(screen.getByRole("grid", { name: /crossword grid/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /check answers/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /new puzzle/i })).toBeTruthy();
    expect(screen.getAllByText("Celestial objects").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Relating to the sun").length).toBeGreaterThanOrEqual(1);
  });

  it("allows entering letters into an open cell", async () => {
    render(<CrosswordsApp windowId="win-cw-2" />);
    await flush();

    const grid = screen.getByRole("grid", { name: /crossword grid/i });
    grid.focus();

    const firstCell = screen.getAllByRole("gridcell")[0];
    fireEvent.click(firstCell);

    fireEvent.keyDown(grid, { key: "A" });
    await flush();

    expect(within(firstCell).getByText("A")).toBeTruthy();
  });

  it("clears a filled cell with backspace", async () => {
    render(<CrosswordsApp windowId="win-cw-3" />);
    await flush();

    const grid = screen.getByRole("grid", { name: /crossword grid/i });
    grid.focus();

    const firstCell = screen.getAllByRole("gridcell")[0];
    fireEvent.click(firstCell);

    fireEvent.keyDown(grid, { key: "S" });
    await flush();
    expect(within(firstCell).getByText("S")).toBeTruthy();

    fireEvent.click(firstCell);
    fireEvent.keyDown(grid, { key: "Backspace" });
    await flush();
    expect(within(firstCell).queryByText("S")).toBeNull();
  });

  it("navigates to a clue's starting cell and direction when the clue is clicked", async () => {
    render(<CrosswordsApp windowId="win-cw-4" />);
    await flush();

    const clue = screen.getByRole("button", { name: /1 across: celestial objects/i });
    fireEvent.click(clue);
    await flush();

    const cells = screen.getAllByRole("gridcell");
    const firstCell = cells[0];

    expect(firstCell).toHaveStyle({ background: "#fbbf24" });

    const activeClueItem = screen.getByRole("button", { name: /1 across: celestial objects/i });
    expect(activeClueItem).toHaveStyle({ background: "rgba(251, 191, 36, 0.25)" });
  });
});
