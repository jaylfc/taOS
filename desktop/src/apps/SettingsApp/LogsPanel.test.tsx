import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LogsSection } from "./LogsPanel";

function jsonResponse(obj: unknown) {
  return new Response(JSON.stringify(obj), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("LogsSection", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders captured logs", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({
        items: [
          {
            id: "1", level: "fatal", message: "Messages crashed",
            source: "AppErrorBoundary", url: "/desktop", stack: "at x",
            created_at: "2026-06-25T12:00:00Z",
          },
        ],
      }),
    );
    render(<LogsSection />);
    expect(await screen.findByText("Messages crashed")).toBeInTheDocument();
    // "fatal" appears twice: the filter button and the log's level badge.
    expect(screen.getAllByText("fatal").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/AppErrorBoundary/)).toBeInTheDocument();
  });

  it("shows the empty state when there are no logs", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ items: [] }));
    render(<LogsSection />);
    expect(await screen.findByText("No logs captured yet.")).toBeInTheDocument();
  });

  it("re-fetches with the level query when a filter is chosen", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ items: [] }));
    render(<LogsSection />);
    await screen.findByText("No logs captured yet.");
    fireEvent.click(screen.getByRole("button", { name: "error" }));
    await waitFor(() => {
      const urls = fetchSpy.mock.calls.map((c) => String(c[0]));
      expect(urls.some((u) => u.includes("/api/client-logs?level=error"))).toBe(true);
    });
  });
});
