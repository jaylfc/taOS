import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { NotificationsPanel } from "./NotificationsPanel";

function jsonResponse(obj: unknown) {
  return new Response(JSON.stringify(obj), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("NotificationsPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows an error when the prefs request fails (non-ok)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 500 }),
    );
    render(<NotificationsPanel />);
    expect(await screen.findByText("Could not load notification preferences.")).toBeInTheDocument();
  });

  it("shows an error when the prefs request rejects", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network"));
    render(<NotificationsPanel />);
    expect(await screen.findByText("Could not reach backend.")).toBeInTheDocument();
  });

  it("renders the toggle list on successful fetch", async () => {
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
    expect(screen.queryByText("Could not reach backend.")).not.toBeInTheDocument();
    expect(screen.queryByText("Could not load notification preferences.")).not.toBeInTheDocument();
  });

  it("shows loading state while genuinely pending", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => {
      return new Promise(() => {}); // never resolves
    });
    render(<NotificationsPanel />);
    expect(await screen.findByText("Loading...")).toBeInTheDocument();
  });
});