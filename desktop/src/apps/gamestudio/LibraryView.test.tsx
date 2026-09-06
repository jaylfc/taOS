import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { LibraryView } from "./LibraryView";

const GAME = {
  id: "my-game",
  name: "My Game",
  prompt: "",
  template: "breakout",
  created: 1,
  updated: 2,
};

describe("LibraryView", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows an empty state with no games", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ games: [] }) } as Response)) as unknown as typeof fetch,
    );
    render(<LibraryView onOpenGame={() => {}} />);
    await waitFor(() => expect(screen.getByText(/No games yet/)).toBeDefined());
  });

  it("delete is backend-confirmed: the row stays until DELETE resolves, then the list is refetched", async () => {
    let deleted = false;
    let resolveDelete!: () => void;
    const deletePromise = new Promise<void>((resolve) => {
      resolveDelete = resolve;
    });

    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      if (url === "/api/games" && method === "GET") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ games: deleted ? [] : [GAME] }),
        } as Response);
      }
      if (url === "/api/games/my-game" && method === "DELETE") {
        return deletePromise.then(() => {
          deleted = true;
          return { ok: true, json: () => Promise.resolve({ status: "deleted", id: "my-game" }) } as Response;
        });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response);
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<LibraryView onOpenGame={() => {}} />);
    await waitFor(() => expect(screen.getByText("My Game")).toBeDefined());

    fireEvent.click(screen.getByLabelText("Delete My Game"));

    // The DELETE call is in flight but hasn't resolved yet -- the row must
    // still be present (no optimistic removal).
    expect(screen.getByText("My Game")).toBeDefined();

    resolveDelete();

    // Only after the DELETE resolves AND the list has been refetched from
    // the server does the row disappear.
    await waitFor(() => expect(screen.queryByText("My Game")).toBeNull());
    await waitFor(() => expect(screen.getByText(/No games yet/)).toBeDefined());
  });
});
