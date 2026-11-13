import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { SettingsApp } from "../SettingsApp";

function json(body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

function mockFetch(storage: Response | Promise<Response>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/auth/status")) {
      return json({ configured: true, authenticated: true, user: { username: "jay", is_admin: true } });
    }
    if (url.includes("/api/settings/storage")) return Promise.resolve(storage);
    return Promise.resolve(new Response(null, { status: 404 }));
  });
}

describe("Settings Storage section", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the sizes the backend reports", async () => {
    mockFetch(
      await json({
        storage: [
          { label: "Models", path: "/m", size: "12.0 GB", bytes: 12_000, total_bytes: 64_000 },
          { label: "Data", path: "/d", size: "250.0 MB", bytes: 250, total_bytes: 64_000 },
        ],
      }),
    );
    render(<SettingsApp windowId="w1" section="storage" />);

    await waitFor(() => expect(screen.getByText("12.0 GB")).toBeInTheDocument());
    expect(screen.getByText("250.0 MB")).toBeInTheDocument();
    // The invented fallback numbers must never appear next to real data.
    expect(screen.queryByText("4.2 GB")).not.toBeInTheDocument();
    const bars = screen.getAllByRole("progressbar");
    expect(bars[0]).toHaveAttribute("aria-valuenow", String((12_000 / 64_000) * 100));
    expect(bars).toHaveLength(2);
    expect(bars[1]).toHaveAttribute("aria-valuenow", String((250 / 64_000) * 100));
  });

  it("gives each usage bar an accessible name", async () => {
    mockFetch(
      await json({
        storage: [
          { label: "Models", path: "/m", size: "12.0 GB", bytes: 12_000, total_bytes: 64_000 },
          { label: "Data", path: "/d", size: "250.0 MB", bytes: 250, total_bytes: 64_000 },
        ],
      }),
    );
    render(<SettingsApp windowId="w1" section="storage" />);

    await waitFor(() => expect(screen.getByText("12.0 GB")).toBeInTheDocument());
    expect(screen.getByRole("progressbar", { name: "Models disk usage" })).toHaveAttribute(
      "aria-valuetext",
      "12.0 GB",
    );
    expect(screen.getByRole("progressbar", { name: "Data disk usage" })).toBeInTheDocument();
  });

  it("says the capacity is unavailable instead of drawing an empty bar", async () => {
    mockFetch(
      await json({
        storage: [{ label: "Models", path: "/m", size: "12.0 GB", bytes: 12_000, total_bytes: 0 }],
      }),
    );
    render(<SettingsApp windowId="w1" section="storage" />);

    await waitFor(() => expect(screen.getByText("12.0 GB")).toBeInTheDocument());
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.getByText(/capacity unavailable/i)).toBeInTheDocument();
  });

  it("says the numbers are unavailable instead of inventing them", async () => {
    mockFetch(new Response(null, { status: 500 }));
    render(<SettingsApp windowId="w1" section="storage" />);

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/unavailable/i));
    expect(screen.queryByText("4.2 GB")).not.toBeInTheDocument();
    expect(screen.queryByText("1.8 GB")).not.toBeInTheDocument();
  });
});
