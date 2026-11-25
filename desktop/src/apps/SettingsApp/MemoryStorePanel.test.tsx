import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryStorePanel } from "./MemoryStorePanel";

function json(obj: unknown, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const OLD = {
  state: "old",
  can_serve: true,
  message: "Your memory store was written by an older taOSmd. It works as it is and nothing is lost.",
  next_step: "convert_optional",
  convert_available: true,
};

describe("MemoryStorePanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the status and offers the conversion only after a confirmation", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/memory-store/convert") {
        expect(JSON.parse(String((init as RequestInit).body))).toEqual({ confirm: true });
        return json({ state: "started" }, 202);
      }
      return json({ status: OLD, converting: false, last_convert: null });
    });
    render(<MemoryStorePanel />);
    expect(await screen.findByText(/nothing is lost/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Convert vectors/ }));
    // Nothing is sent until the person confirms.
    expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith("/convert"))).toBe(false);
    expect(screen.getByText(/optional and one way/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Convert now" }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith("/convert"))).toBe(true),
    );
  });

  it("does not offer a conversion when there is nothing to convert", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      json({
        status: { ...OLD, state: "current", convert_available: false, message: "Your memory store is up to date." },
        converting: false,
        last_convert: null,
      }),
    );
    render(<MemoryStorePanel />);
    expect(await screen.findByText("Your memory store is up to date.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Convert/ })).toBeNull();
  });

  it("reports a refused conversion in plain words", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      json({
        status: OLD,
        converting: false,
        last_convert: { state: "refused", ok: false, message: "The conversion did not start because something else is using the memory store. Nothing was changed." },
      }),
    );
    render(<MemoryStorePanel />);
    expect(await screen.findByText(/Nothing was changed/)).toBeInTheDocument();
  });

  it("says plainly when the status cannot be loaded", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ error: "x" }, 500));
    render(<MemoryStorePanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load the memory store status.");
  });
});
