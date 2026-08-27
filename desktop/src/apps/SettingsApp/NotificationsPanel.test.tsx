import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { NotificationsPanel } from "./NotificationsPanel";

function jsonResponse(obj: unknown) {
  return new Response(JSON.stringify(obj), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("NotificationsPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("announces the prefs-load error via role=alert", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 500 }),
    );
    render(<NotificationsPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load notification preferences.",
    );
  });

  it("announces the prefs-load rejection via role=alert", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network"));
    render(<NotificationsPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not reach backend.",
    );
  });

  it("renders the toggle list on successful fetch with no alert present", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse([
        { event_type: "worker.join", muted: false },
        { event_type: "backend.up", muted: true },
      ]),
    );
    render(<NotificationsPanel />);
    expect(await screen.findByText("Notifications")).toBeInTheDocument();
    expect(screen.getByText("Worker joined")).toBeInTheDocument();
    expect(screen.getByText("Backend up")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("announces a toggle-save failure via role=alert", async () => {
    let callCount = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(() => {
      callCount += 1;
      if (callCount === 1) {
        return Promise.resolve(jsonResponse([{ event_type: "worker.join", muted: false }]));
      }
      return Promise.resolve(new Response(null, { status: 500 }));
    });
    render(<NotificationsPanel />);
    expect(await screen.findByText("Worker joined")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Worker joined notifications"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Failed to save (500)",
    );
  });

  it("shows loading state while genuinely pending", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => {
      return new Promise(() => {}); // never resolves
    });
    render(<NotificationsPanel />);
    expect(await screen.findByText("Loading...")).toBeInTheDocument();
  });

  it("recovers to the toggle list when Retry succeeds after a rejected fetch", async () => {
    let callCount = 0;
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(() => {
      callCount += 1;
      if (callCount === 1) return Promise.reject(new Error("network"));
      return Promise.resolve(
        jsonResponse([
          { event_type: "worker.join", muted: false },
          { event_type: "backend.up", muted: true },
        ]),
      );
    });
    render(<NotificationsPanel />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach backend.");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    // loadPrefs resets state before re-fetching, so the loading state returns.
    expect(screen.getByText("Loading...")).toBeInTheDocument();

    expect(await screen.findByText("Worker joined")).toBeInTheDocument();
    expect(screen.getByText("Backend up")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("re-shows the error when Retry still fails (not a blank panel or spinner)", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network"));
    render(<NotificationsPanel />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach backend.");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    // The loading state confirms `loaded` was reset to false before the re-fetch.
    expect(screen.getByText("Loading...")).toBeInTheDocument();
    // The error returns once the re-fetch rejects, rather than a blank panel or a
    // permanent spinner (which a broken `loaded` reset would produce).
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach backend.");
    expect(screen.queryByText("Loading...")).not.toBeInTheDocument();
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });
});
