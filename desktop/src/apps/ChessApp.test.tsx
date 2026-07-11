import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { ChessApp } from "./ChessApp";

function mockAgentsFetch(agents: Array<{ name: string }> = []) {
  return vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/agents")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(agents),
      });
    }
    return Promise.resolve({
      ok: false,
      json: () => Promise.resolve({}),
    });
  });
}

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

function square(label: string | RegExp) {
  return screen.getByRole("button", { name: label });
}

describe("ChessApp", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", mockAgentsFetch());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders the board, status, and controls on mount", async () => {
    render(<ChessApp windowId="win-chess-1" />);
    await flush();

    expect(screen.getByRole("grid", { name: /chess board/i })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toMatch(/white to move/i);
    expect(screen.getByRole("log", { name: /move history/i })).toBeTruthy();
    expect(screen.getByText(/no moves yet/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /new game/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /undo move/i })).toBeTruthy();
    expect(screen.getByLabelText(/game mode/i)).toBeTruthy();
    // Starting pieces present
    expect(square(/e2 white p/i)).toBeTruthy();
    expect(square(/e8 black k/i)).toBeTruthy();
  });

  it("mocks the on-mount agents fetch", async () => {
    const fetchMock = mockAgentsFetch([{ name: "alpha-bot" }]);
    vi.stubGlobal("fetch", fetchMock);

    render(<ChessApp windowId="win-chess-2" />);
    await flush();

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    const agentCalls = fetchMock.mock.calls.filter((c) =>
      String(c[0]).includes("/api/agents"),
    );
    expect(agentCalls.length).toBeGreaterThanOrEqual(1);
  });

  it("updates board state after a legal pawn move (e2-e4)", async () => {
    render(<ChessApp windowId="win-chess-3" />);
    await flush();

    fireEvent.click(square(/e2 white p/i));
    fireEvent.click(square(/^e4$/i));

    // Turn flips and the pawn is on e4
    expect(screen.getByRole("status").textContent).toMatch(/black to move/i);
    expect(square(/e4 white p/i)).toBeTruthy();
    expect(square(/^e2$/i)).toBeTruthy();
  });

  it("switches turn after each legal move", async () => {
    render(<ChessApp windowId="win-chess-4" />);
    await flush();

    expect(screen.getByRole("status").textContent).toMatch(/white to move/i);

    fireEvent.click(square(/e2 white p/i));
    fireEvent.click(square(/^e4$/i));
    expect(screen.getByRole("status").textContent).toMatch(/black to move/i);
    expect(square(/e4 white p/i)).toBeTruthy();

    fireEvent.click(square(/e7 black p/i));
    fireEvent.click(square(/^e5$/i));
    expect(screen.getByRole("status").textContent).toMatch(/white to move/i);
    expect(square(/e5 black p/i)).toBeTruthy();
  });

  it("does not move when clicking an illegal target", async () => {
    render(<ChessApp windowId="win-chess-5" />);
    await flush();

    fireEvent.click(square(/e2 white p/i));
    // e5 is not a legal first move for the e2 pawn
    fireEvent.click(square(/^e5$/i));

    // Still white to move; pawn remains on e2
    expect(screen.getByRole("status").textContent).toMatch(/white to move/i);
    expect(square(/e2 white p/i)).toBeTruthy();
  });

  it("shows checkmate status after fool's mate", async () => {
    render(<ChessApp windowId="win-chess-6" />);
    await flush();

    // 1. f2-f3
    fireEvent.click(square(/f2 white p/i));
    fireEvent.click(square(/^f3$/i));
    // 1... e7-e5
    fireEvent.click(square(/e7 black p/i));
    fireEvent.click(square(/^e5$/i));
    // 2. g2-g4
    fireEvent.click(square(/g2 white p/i));
    fireEvent.click(square(/^g4$/i));
    // 2... d8-h4#
    fireEvent.click(square(/d8 black q/i));
    fireEvent.click(square(/^h4$/i));

    const status = screen.getByRole("status").textContent ?? "";
    expect(status).toMatch(/checkmate/i);
    expect(status).toMatch(/black wins/i);
  });

  it("resets the board with New Game", async () => {
    render(<ChessApp windowId="win-chess-7" />);
    await flush();

    fireEvent.click(square(/e2 white p/i));
    fireEvent.click(square(/^e4$/i));
    expect(screen.getByRole("status").textContent).toMatch(/black to move/i);

    fireEvent.click(screen.getByRole("button", { name: /new game/i }));

    expect(screen.getByRole("status").textContent).toMatch(/white to move/i);
    expect(square(/e2 white p/i)).toBeTruthy();
    // e4 should be empty again
    expect(square(/^e4$/i)).toBeTruthy();
  });

  it("shows agent selector when switching to vs-agent mode", async () => {
    const fetchMock = mockAgentsFetch([{ name: "alpha-bot" }, { name: "beta-bot" }]);
    vi.stubGlobal("fetch", fetchMock);

    render(<ChessApp windowId="win-chess-8" />);
    await flush();

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });

    fireEvent.change(screen.getByLabelText(/game mode/i), {
      target: { value: "vs-agent" },
    });

    // Mode change starts a fresh game
    expect(screen.getByRole("status").textContent).toMatch(/white to move/i);
    await waitFor(() => {
      expect(screen.getByLabelText(/select agent opponent/i)).toBeTruthy();
    });
    expect(screen.getByText("alpha-bot")).toBeTruthy();
  });
});
