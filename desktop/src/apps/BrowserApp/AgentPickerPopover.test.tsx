import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { AgentPickerPopover } from "./AgentPickerPopover";
import { useBrowserStore } from "@/stores/browser-store";
import { useBrowserAgentStore } from "@/stores/browser-agent-store";
import * as browserAgentApi from "@/lib/browser-agent-api";

const WINDOW_ID = "win-1";
const TAB_ID = "tab-1";
const PROFILE_ID = "personal";

const AGENTS = [
  { id: "agent-1", name: "Alpha", emoji: "🤖", framework: "openclaw" },
  { id: "agent-2", name: "Beta", emoji: "🧪", framework: "smolagents" },
  { id: "agent-3", name: "Gamma", emoji: "🔗", framework: "pocketflow" },
  { id: "agent-4", name: "Delta", emoji: "🌳", framework: "langroid" },
  { id: "agent-5", name: "Epsilon", emoji: "💬", framework: "openai-agents-sdk" },
];

function defaultProps(overrides?: Partial<Parameters<typeof AgentPickerPopover>[0]>) {
  return {
    windowId: WINDOW_ID,
    tabId: TAB_ID,
    profileId: PROFILE_ID,
    pinnedAgentIds: [],
    onClose: vi.fn(),
    ...overrides,
  };
}

beforeEach(() => {
  useBrowserStore.setState({ windows: {} });
  useBrowserStore.getState().createWindow(WINDOW_ID, PROFILE_ID);
  useBrowserAgentStore.setState({ panels: {}, lastEventAt: {} });

  vi.spyOn(browserAgentApi, "listAgents").mockResolvedValue(AGENTS);
  vi.spyOn(browserAgentApi, "pinAgent").mockResolvedValue({ pinned: true });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AgentPickerPopover", () => {
  it("renders the agent list from listAgents", async () => {
    render(<AgentPickerPopover {...defaultProps()} />);
    await waitFor(() => {
      expect(screen.getByText("Alpha")).toBeTruthy();
      expect(screen.getByText("Beta")).toBeTruthy();
    });
  });

  it("shows X / 4 pinned count", async () => {
    render(<AgentPickerPopover {...defaultProps({ pinnedAgentIds: ["agent-1"] })} />);
    await waitFor(() => {
      expect(screen.getByText(/1 \/ 4 pinned/i)).toBeTruthy();
    });
  });

  it("disables already-pinned agents with aria-disabled and tooltip", async () => {
    render(
      <AgentPickerPopover
        {...defaultProps({ pinnedAgentIds: ["agent-1"] })}
      />,
    );
    await waitFor(() => {
      const options = screen.getAllByRole("option");
      const alphaRow = options.find((el) => el.textContent?.includes("Alpha"));
      expect(alphaRow).toBeTruthy();
      expect(alphaRow?.getAttribute("aria-disabled")).toBe("true");
      expect(alphaRow?.getAttribute("title")).toBe("Already pinned");
    });
  });

  it("filters as user types in the search box", async () => {
    render(<AgentPickerPopover {...defaultProps()} />);
    await waitFor(() => {
      expect(screen.getByText("Alpha")).toBeTruthy();
    });
    const search = screen.getByRole("textbox");
    fireEvent.change(search, { target: { value: "bet" } });
    await waitFor(() => {
      expect(screen.queryByText("Alpha")).toBeNull();
      expect(screen.getByText("Beta")).toBeTruthy();
    });
  });

  it("clicking an enabled row pins via pinAgent and closes on success", async () => {
    const onClose = vi.fn();
    render(<AgentPickerPopover {...defaultProps({ onClose })} />);
    await waitFor(() => {
      expect(screen.getByText("Beta")).toBeTruthy();
    });
    fireEvent.click(screen.getByText("Beta"));
    await waitFor(() => {
      expect(browserAgentApi.pinAgent).toHaveBeenCalledWith(PROFILE_ID, TAB_ID, "agent-2");
      expect(onClose).toHaveBeenCalled();
    });
  });

  it("on { pinned: true }, addPinnedAgent is called with the agent id", async () => {
    vi.spyOn(browserAgentApi, "pinAgent").mockResolvedValue({ pinned: true });
    const addSpy = vi.spyOn(useBrowserStore.getState(), "addPinnedAgent");
    render(<AgentPickerPopover {...defaultProps()} />);
    await waitFor(() => {
      expect(screen.getByText("Alpha")).toBeTruthy();
    });
    fireEvent.click(screen.getByText("Alpha"));
    await waitFor(() => {
      expect(addSpy).toHaveBeenCalledWith(WINDOW_ID, TAB_ID, "agent-1");
    });
  });

  it("on { pinned: true }, openPanel is called with the agent id", async () => {
    vi.spyOn(browserAgentApi, "pinAgent").mockResolvedValue({ pinned: true });
    const openPanelSpy = vi.spyOn(useBrowserAgentStore.getState(), "openPanel");
    render(<AgentPickerPopover {...defaultProps()} />);
    await waitFor(() => {
      expect(screen.getByText("Alpha")).toBeTruthy();
    });
    fireEvent.click(screen.getByText("Alpha"));
    await waitFor(() => {
      expect(openPanelSpy).toHaveBeenCalledWith(WINDOW_ID, TAB_ID, "agent-1");
    });
  });

  it("on { error }, error banner is shown and popover stays open", async () => {
    vi.spyOn(browserAgentApi, "pinAgent").mockResolvedValue({ error: "agent not found" });
    const onClose = vi.fn();
    render(<AgentPickerPopover {...defaultProps({ onClose })} />);
    await waitFor(() => {
      expect(screen.getByText("Alpha")).toBeTruthy();
    });
    fireEvent.click(screen.getByText("Alpha"));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeTruthy();
      expect(screen.getByText("agent not found")).toBeTruthy();
      expect(onClose).not.toHaveBeenCalled();
    });
  });

  it("on null (network error), error banner is shown", async () => {
    vi.spyOn(browserAgentApi, "pinAgent").mockResolvedValue(null);
    const onClose = vi.fn();
    render(<AgentPickerPopover {...defaultProps({ onClose })} />);
    await waitFor(() => {
      expect(screen.getByText("Alpha")).toBeTruthy();
    });
    fireEvent.click(screen.getByText("Alpha"));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeTruthy();
      expect(screen.getByText(/network error/i)).toBeTruthy();
      expect(onClose).not.toHaveBeenCalled();
    });
  });

  it("on { pinned: false } (idempotent race), addPinnedAgent still called and popover closes", async () => {
    vi.spyOn(browserAgentApi, "pinAgent").mockResolvedValue({ pinned: false });
    const addSpy = vi.spyOn(useBrowserStore.getState(), "addPinnedAgent");
    const onClose = vi.fn();
    render(<AgentPickerPopover {...defaultProps({ onClose })} />);
    await waitFor(() => {
      expect(screen.getByText("Alpha")).toBeTruthy();
    });
    fireEvent.click(screen.getByText("Alpha"));
    await waitFor(() => {
      expect(addSpy).toHaveBeenCalledWith(WINDOW_ID, TAB_ID, "agent-1");
      expect(onClose).toHaveBeenCalled();
    });
  });

  it("4 already-pinned agents shows 'Maximum agents reached' message + all rows disabled", async () => {
    render(
      <AgentPickerPopover
        {...defaultProps({
          pinnedAgentIds: ["agent-1", "agent-2", "agent-3", "agent-4"],
        })}
      />,
    );
    await waitFor(() => {
      expect(screen.getByText(/maximum agents reached/i)).toBeTruthy();
      const options = screen.getAllByRole("option");
      for (const opt of options) {
        expect(opt.getAttribute("aria-disabled")).toBe("true");
      }
    });
  });

  it("Esc closes via onClose", async () => {
    const onClose = vi.fn();
    const { container } = render(<AgentPickerPopover {...defaultProps({ onClose })} />);
    await waitFor(() => {
      expect(screen.getByText("Alpha")).toBeTruthy();
    });
    fireEvent.keyDown(container.firstChild as HTMLElement, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("click outside closes via onClose", async () => {
    const onClose = vi.fn();
    render(<AgentPickerPopover {...defaultProps({ onClose })} />);
    await waitFor(() => {
      expect(screen.getByText("Alpha")).toBeTruthy();
    });
    // The deferred handler is set via setTimeout(0), so we need to advance timers
    await act(async () => {
      await new Promise((r) => setTimeout(r, 10));
    });
    fireEvent.mouseDown(document.body);
    expect(onClose).toHaveBeenCalled();
  });

  it("ArrowDown/Up navigate enabled rows only (skipping disabled)", async () => {
    // Pin agent-1 so it's disabled; ArrowDown should advance from Beta (first enabled) to Gamma
    const { container } = render(
      <AgentPickerPopover
        {...defaultProps({ pinnedAgentIds: ["agent-1"] })}
      />,
    );
    // Wait for agents to load AND for the initial focus to settle on Beta
    await waitFor(() => {
      const options = screen.getAllByRole("option");
      const focused = options.find((el) => el.getAttribute("aria-selected") === "true");
      expect(focused?.textContent).toContain("Beta");
    });
    // fire keyDown on the popover root div (that's where onKeyDown lives)
    const popover = container.firstChild as HTMLElement;
    // ArrowDown from Beta (first enabled, index 1) → Gamma (index 2)
    fireEvent.keyDown(popover, { key: "ArrowDown" });
    await waitFor(() => {
      const options = screen.getAllByRole("option");
      const focused = options.find((el) => el.getAttribute("aria-selected") === "true");
      expect(focused?.textContent).toContain("Gamma");
    });
  });

  it("Enter pins the currently-focused row", async () => {
    const onClose = vi.fn();
    const { container } = render(<AgentPickerPopover {...defaultProps({ onClose })} />);
    await waitFor(() => {
      expect(screen.getByText("Alpha")).toBeTruthy();
    });
    fireEvent.keyDown(container.firstChild as HTMLElement, { key: "Enter" });
    await waitFor(() => {
      expect(browserAgentApi.pinAgent).toHaveBeenCalled();
      expect(onClose).toHaveBeenCalled();
    });
  });

  it("listbox role + aria-label present", async () => {
    render(<AgentPickerPopover {...defaultProps()} />);
    await waitFor(() => {
      const listbox = screen.getByRole("listbox", { name: /pick an agent/i });
      expect(listbox).toBeTruthy();
    });
  });
});
