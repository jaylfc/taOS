import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { OtherSystemsWidget } from "@/components/mobile/OtherSystemsWidget";
import userEvent from "@testing-library/user-event";

const mockFetch = vi.fn();
global.fetch = mockFetch;

const defaultSystems = [
  { id: "omarchy", name: "Omarchy", ready: true, reason: "" },
  { id: "luma", name: "Luma", ready: false, reason: "guest kernel is stale" },
];

function renderWidget(override?: { available?: boolean; systems?: typeof defaultSystems }) {
  mockFetch.mockResolvedValue({
    ok: true,
    json: async () => ({
      available: override?.available ?? true,
      systems: override?.systems ?? defaultSystems,
    }),
  });
  return render(<OtherSystemsWidget />);
}

describe("OtherSystemsWidget", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test("hidden when unavailable", async () => {
    renderWidget({ available: false });
    await waitFor(() => {
      expect(screen.queryByText("Other Systems")).not.toBeInTheDocument();
    });
  });

  test("shows tile when available", async () => {
    renderWidget();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /other systems/i })).toBeInTheDocument();
    });
    expect(screen.getByText("1 ready, 1 not ready")).toBeInTheDocument();
  });

  test("shows not-ready system as disabled with reason", async () => {
    renderWidget();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /other systems/i })).toBeInTheDocument();
    });
    // Open the sheet to see the list
    fireEvent.click(screen.getByRole("button", { name: /other systems/i }));
    await waitFor(() => {
      expect(screen.getByText("Luma")).toBeInTheDocument();
    });
    const lumaButton = screen.getByText("Luma").closest("button");
    expect(lumaButton).toBeDisabled();
    expect(screen.getByText("guest kernel is stale")).toBeInTheDocument();
  });

  test("confirm flow calls API once", async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ available: true, systems: defaultSystems }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ok: true, id: "omarchy", in_seconds: 2 }),
      });

    renderWidget();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /other systems/i })).toBeInTheDocument();
    });

    // Open list sheet
    fireEvent.click(screen.getByRole("button", { name: /other systems/i }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /other systems/i })).toBeInTheDocument();
      expect(screen.getByText("Omarchy")).toBeInTheDocument();
    });

    // Click Omarchy to open confirm sheet
    fireEvent.click(screen.getByText("Omarchy"));
    await waitFor(() => {
      expect(screen.getByText("Switch to Omarchy?")).toBeInTheDocument();
    });

    // Click Switch
    fireEvent.click(screen.getByText("Switch"));

    await waitFor(() => {
      expect(screen.getByText("Switching to Omarchy...")).toBeInTheDocument();
    });

    // Verify API was called
    expect(mockFetch).toHaveBeenCalledTimes(2); // list + switch
    const switchCall = mockFetch.mock.calls[1];
    expect(switchCall[0]).toBe("/api/system/other-systems/omarchy/switch");
    expect(switchCall[1]).toEqual(
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ confirm: true }),
      })
    );
  });

  test("escape cancels sheet", async () => {
    renderWidget();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /other systems/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /other systems/i }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /other systems/i })).toBeInTheDocument();
    });

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByRole("heading", { name: /other systems/i })).not.toBeInTheDocument();
    });
  });
});