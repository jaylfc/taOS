import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { OtherSystemsWidget } from "@/components/mobile/OtherSystemsWidget";

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
      expect(screen.getByText("Switching to Omarchy. To come back, open Return to taOS there.")).toBeInTheDocument();
    });
    expect(screen.queryByText(/Switching to Omarchy\.\.\./)).not.toBeInTheDocument();

    // Verify API was called
    expect(mockFetch).toHaveBeenCalledTimes(2); // list + switch
    const switchCall = mockFetch.mock.calls[1];
    expect(switchCall[0]).toBe("/api/system/other-systems/omarchy/switch");
    expect(switchCall[1]).toEqual(
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ confirm: true, force: false }),
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

  test("success text appears after switch", async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          available: true,
          systems: defaultSystems,
        }),
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
      expect(screen.getByText("Switching to Omarchy. To come back, open Return to taOS there.")).toBeInTheDocument();
    });
  });

  test("Tab from the last button lands on the first, Shift+Tab from the first lands on the last", async () => {
    renderWidget();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /other systems/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /other systems/i }));
    await waitFor(() => {
      expect(screen.getByText("Omarchy")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Omarchy"));
    await waitFor(() => {
      expect(screen.getByText("Switch to Omarchy?")).toBeInTheDocument();
    });

    const close = screen.getByRole("button", { name: "Close" });
    const cancel = screen.getByRole("button", { name: "Cancel" });
    // Let the sheet's own deferred focus() settle before moving focus.
    await waitFor(() => {
      expect(screen.getByRole("document")).toHaveFocus();
    });

    // Tab from the last button wraps to the first.
    cancel.focus();
    expect(cancel).toHaveFocus();
    const tab = fireEvent.keyDown(document.activeElement as Element, { key: "Tab" });
    expect(tab).toBe(false); // default prevented: the trap moved focus itself
    expect(close).toHaveFocus();

    // Shift+Tab from the first button wraps to the last.
    const shiftTab = fireEvent.keyDown(document.activeElement as Element, { key: "Tab", shiftKey: true });
    expect(shiftTab).toBe(false);
    expect(cancel).toHaveFocus();

    // Tab from a middle button is left to the browser (not trapped).
    const middle = screen.getByRole("button", { name: "Switch" });
    middle.focus();
    expect(fireEvent.keyDown(middle, { key: "Tab" })).toBe(true);
    expect(middle).toHaveFocus();
  });

  test("Switch anyway resends with force true when drain fails", async () => {
    // Mock list return
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          available: true,
          systems: defaultSystems,
        }),
      })
      // Mock switch returning 409 with agents not ready
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: async () => ({
          detail: "agents not ready; use force to override",
        }),
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

    // Click Switch - this time the helper returns exit code 3 (agents not ready)
    fireEvent.click(screen.getByText("Switch"));

    // Wait for the 409 error state to appear with drainFailed and "Switch anyway" button
    await waitFor(() => {
      // The switchError and drainFailed should now be set
      const errorDiv = screen.getByText("agents not ready; use force to override");
      expect(errorDiv).toBeInTheDocument();
    });

    // The "Switch anyway" button should be visible when drainFailed is true
    const switchAnywayBtn = screen.getByRole("button", { name: "Switch anyway" });
    expect(switchAnywayBtn).toBeInTheDocument();
    expect(switchAnywayBtn).toBeEnabled();

    // Click Switch anyway - this should resend with force=true
    fireEvent.click(switchAnywayBtn);

    // Verify the API was called with force: true on the second attempt
    expect(mockFetch).toHaveBeenCalledTimes(3); // list + first switch + second switch with force
    const secondSwitchCall = mockFetch.mock.calls[2];
    expect(secondSwitchCall[0]).toBe("/api/system/other-systems/omarchy/switch");
    expect(secondSwitchCall[1]).toEqual(
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ confirm: true, force: true }),
      })
    );
  });
});