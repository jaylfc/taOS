import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ConnectWizard } from "../ConnectWizard";

function agentsMock(agentsList: Record<string, unknown>[]) {
  return vi.fn().mockResolvedValue({
    ok: true,
    headers: { get: () => "application/json" },
    json: async () => agentsList,
  }) as unknown as typeof fetch;
}

describe("ConnectWizard", () => {
  it("renders nothing when open is false", () => {
    const { container } = render(<ConnectWizard open={false} onClose={() => {}} />);
    expect(container.querySelector('[role="dialog"]')).toBeNull();
  });

  it("renders the dialog when open is true", () => {
    render(<ConnectWizard open={true} onClose={() => {}} />);
    expect(screen.getByRole("dialog", { name: /connect session/i })).toBeInTheDocument();
    expect(screen.getByText("Connect session")).toBeInTheDocument();
  });

  it("starts on step 1 (Pick agent)", () => {
    render(<ConnectWizard open={true} onClose={() => {}} />);
    expect(screen.getByText("Pick agent")).toBeInTheDocument();
    expect(screen.getByText("Select agent")).toBeInTheDocument();
  });

  it("shows loading state while fetching agents", () => {
    render(<ConnectWizard open={true} onClose={() => {}} />);
    expect(screen.getByText("Loading agents...")).toBeInTheDocument();
  });

  it("shows agents after fetch resolves", async () => {
    global.fetch = agentsMock([
      { name: "agent-one", status: "running", display_name: "Agent One" },
      { name: "agent-two", status: "running", display_name: "Agent Two" },
    ]);

    render(<ConnectWizard open={true} onClose={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText("Agent One")).toBeInTheDocument();
    });
    expect(screen.getByText("Agent Two")).toBeInTheDocument();
  });

  it("filters agents to only running ones", async () => {
    global.fetch = agentsMock([
      { name: "running-agent", status: "running", display_name: "Running" },
      { name: "stopped-agent", status: "stopped", display_name: "Stopped" },
    ]);

    render(<ConnectWizard open={true} onClose={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText("Running")).toBeInTheDocument();
    });
    expect(screen.queryByText("Stopped")).not.toBeInTheDocument();
  });

  it("advances to step 2 after selecting an agent", async () => {
    global.fetch = agentsMock([
      { name: "my-agent", status: "running", display_name: "My Agent" },
    ]);

    render(<ConnectWizard open={true} onClose={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText("My Agent")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("My Agent"));
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    expect(screen.getByText("Create surface")).toBeInTheDocument();
    expect(screen.getByLabelText(/channel name/i)).toBeInTheDocument();
  });

  it("does not advance without selecting an agent", () => {
    render(<ConnectWizard open={true} onClose={() => {}} />);
    const nextBtn = screen.getByRole("button", { name: /next/i });
    expect(nextBtn).toBeDisabled();
  });

  it("shows error state when agents fetch fails", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      headers: { get: () => "application/json" },
    }) as unknown as typeof fetch;

    render(<ConnectWizard open={true} onClose={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText(/could not load agents/i)).toBeInTheDocument();
    });
  });

  it("closes the dialog on Escape key", () => {
    const onClose = vi.fn();
    render(<ConnectWizard open={true} onClose={onClose} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("closes the dialog on Cancel button in step 1", () => {
    const onClose = vi.fn();
    render(<ConnectWizard open={true} onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it("renders the snippet panel in step 3", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        headers: { get: () => "application/json" },
        json: async () => [{ name: "my-agent", status: "running", display_name: "My Agent" }],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ id: "ch-1", name: "#session-my-agent" }),
      });
    global.fetch = fetchMock as unknown as typeof fetch;

    render(<ConnectWizard open={true} onClose={() => {}} />);

    await waitFor(() => expect(screen.getByText("My Agent")).toBeInTheDocument());
    fireEvent.click(screen.getByText("My Agent"));
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    const channelInput = screen.getByLabelText(/channel name/i);
    fireEvent.change(channelInput, { target: { value: "#session-my-agent" } });
    fireEvent.click(screen.getByRole("button", { name: /create channel/i }));

    await waitFor(() => {
      expect(screen.getByText("Connect snippet")).toBeInTheDocument();
    });

    expect(screen.getByText("bash")).toBeInTheDocument();
    expect(screen.getByText("PowerShell")).toBeInTheDocument();
  });

  it("switches between bash and PowerShell snippets", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        headers: { get: () => "application/json" },
        json: async () => [{ name: "my-agent", status: "running" }],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ id: "ch-1" }),
      });
    global.fetch = fetchMock as unknown as typeof fetch;

    render(<ConnectWizard open={true} onClose={() => {}} />);

    await waitFor(() => expect(screen.getByText("my-agent")).toBeInTheDocument());
    fireEvent.click(screen.getByText("my-agent"));
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    const channelInput = screen.getByLabelText(/channel name/i);
    fireEvent.change(channelInput, { target: { value: "#session-my-agent" } });
    fireEvent.click(screen.getByRole("button", { name: /create channel/i }));

    await waitFor(() => expect(screen.getByText("Connect snippet")).toBeInTheDocument());

    expect(screen.getByText(/taOStalk connect snippet — emits session events/)).toBeInTheDocument();
    expect(screen.getByText(/taOS A2A bus/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "PowerShell" }));
    expect(screen.getByText(/Invoke-RestMethod/)).toBeInTheDocument();
  });

  it("has proper ARIA attributes on the dialog", () => {
    render(<ConnectWizard open={true} onClose={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: /connect session/i });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAttribute("aria-label", "Connect session");
  });

  it("has ARIA step navigation", () => {
    render(<ConnectWizard open={true} onClose={() => {}} />);
    expect(screen.getByRole("navigation", { name: /wizard steps/i })).toBeInTheDocument();
  });

  it("has accessible agent listbox in step 1", async () => {
    global.fetch = agentsMock([
      { name: "agent-1", status: "running", display_name: "Agent 1" },
    ]);

    render(<ConnectWizard open={true} onClose={() => {}} />);

    await waitFor(() => {
      expect(screen.getByRole("listbox", { name: /running agents/i })).toBeInTheDocument();
    });

    const option = screen.getByRole("option", { name: /agent 1/i });
    expect(option).toHaveAttribute("aria-selected", "false");
  });

  it("closes on backdrop click", () => {
    const onClose = vi.fn();
    render(<ConnectWizard open={true} onClose={onClose} />);
    fireEvent.click(screen.getByRole("dialog"));
    expect(onClose).toHaveBeenCalled();
  });
});
