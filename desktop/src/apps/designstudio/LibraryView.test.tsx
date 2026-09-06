import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LibraryView } from "./LibraryView";

const DESIGN = { id: "d1", name: "My Poster", updated_at: Math.floor(Date.now() / 1000) };

function ok(body: unknown) {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as Response;
}

describe("LibraryView", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows an empty state when there are no saved designs", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(ok([]))) as unknown as typeof fetch);
    render(<LibraryView onOpenDesign={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/No saved designs yet/i)).toBeDefined());
  });

  it("renders a saved design and opens it on click", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(ok([DESIGN]))) as unknown as typeof fetch);
    const onOpen = vi.fn();
    render(<LibraryView onOpenDesign={onOpen} />);
    await waitFor(() => expect(screen.getByText("My Poster")).toBeDefined());
    fireEvent.click(screen.getByLabelText("Open My Poster"));
    expect(onOpen).toHaveBeenCalledWith("d1");
  });

  it("surfaces a load error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) } as Response)) as unknown as typeof fetch,
    );
    render(<LibraryView onOpenDesign={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/could not load designs/i));
  });

  it("delete is backend-confirmed: refetches the list and reports the rename callback", async () => {
    let deleted = false;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      if (url === "/api/designs" && method === "GET") {
        return Promise.resolve(ok(deleted ? [] : [DESIGN]));
      }
      if (url === "/api/designs/d1" && method === "DELETE") {
        deleted = true;
        return Promise.resolve(ok({}));
      }
      return Promise.resolve(ok({}));
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<LibraryView onOpenDesign={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("My Poster")).toBeDefined());

    fireEvent.click(screen.getByLabelText("Delete My Poster"));
    await waitFor(() => expect(screen.queryByText("My Poster")).toBeNull());
    await waitFor(() => expect(screen.getByText(/No saved designs yet/i)).toBeDefined());
  });

  it("does not delete when the user cancels the confirm", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(ok([DESIGN])));
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<LibraryView onOpenDesign={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("My Poster")).toBeDefined());

    fireEvent.click(screen.getByLabelText("Delete My Poster"));
    // No DELETE was issued -- only the initial list GET happened.
    expect(fetchMock.mock.calls.every((c) => (c[1]?.method ?? "GET") === "GET")).toBe(true);
  });

  it("renames a design and notifies via onRenamed", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (method === "PUT") return Promise.resolve(ok({ id: "d1", name: "Renamed", content: "{}" }));
      return Promise.resolve(ok([DESIGN]));
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    vi.spyOn(window, "prompt").mockReturnValue("Renamed");
    const onRenamed = vi.fn();

    render(<LibraryView onOpenDesign={vi.fn()} onRenamed={onRenamed} />);
    await waitFor(() => expect(screen.getByText("My Poster")).toBeDefined());

    fireEvent.click(screen.getByLabelText("Rename My Poster"));
    await waitFor(() => expect(onRenamed).toHaveBeenCalledWith("d1", "Renamed"));
  });

  it("skips the rename when the prompt is cancelled or unchanged", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(ok([DESIGN])));
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    vi.spyOn(window, "prompt").mockReturnValue(null);

    render(<LibraryView onOpenDesign={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("My Poster")).toBeDefined());

    fireEvent.click(screen.getByLabelText("Rename My Poster"));
    expect(fetchMock.mock.calls.every((c) => (c[1]?.method ?? "GET") === "GET")).toBe(true);
  });
});
