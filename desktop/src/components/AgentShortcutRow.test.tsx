import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { AgentShortcutRow } from "./AgentShortcutRow";
import * as useAgentShortcutsModule from "../hooks/use-agent-shortcuts";
import * as useIsMobileModule from "../hooks/use-is-mobile";

// AgentsApp is a single file (not a dir), so component is at
// desktop/src/components/AgentShortcutRow.tsx. Mock paths are one level up.
vi.mock("../hooks/use-agent-shortcuts");
vi.mock("../hooks/use-is-mobile");

describe("AgentShortcutRow", () => {
  beforeEach(() => {
    vi.mocked(useIsMobileModule.useIsMobile).mockReturnValue(false);
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders null when shortcuts list is empty", () => {
    vi.mocked(useAgentShortcutsModule.useAgentShortcuts).mockReturnValue({
      shortcuts: [],
      loading: false,
      error: null,
    });
    const { container } = render(<AgentShortcutRow agentId="abc" onLaunch={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders null while loading", () => {
    vi.mocked(useAgentShortcutsModule.useAgentShortcuts).mockReturnValue({
      shortcuts: [],
      loading: true,
      error: null,
    });
    const { container } = render(<AgentShortcutRow agentId="abc" onLaunch={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders N buttons with correct aria-labels on wide viewport", () => {
    vi.mocked(useIsMobileModule.useIsMobile).mockReturnValue(false);
    vi.mocked(useAgentShortcutsModule.useAgentShortcuts).mockReturnValue({
      shortcuts: [
        { idx: 0, label: "Container shell", icon: "terminal", kind: "container-terminal", requires_capability: "agent.shell" },
        { idx: 1, label: "OpenClaw agent", icon: "tui", kind: "tui", requires_capability: "agent.terminal" },
      ],
      loading: false,
      error: null,
    });
    render(<AgentShortcutRow agentId="abc" onLaunch={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Container shell" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "OpenClaw agent" })).toBeInTheDocument();
  });

  it("renders icon-only buttons on narrow (mobile) viewport", () => {
    vi.mocked(useIsMobileModule.useIsMobile).mockReturnValue(true);
    vi.mocked(useAgentShortcutsModule.useAgentShortcuts).mockReturnValue({
      shortcuts: [
        { idx: 0, label: "Container shell", icon: "terminal", kind: "container-terminal", requires_capability: "agent.shell" },
      ],
      loading: false,
      error: null,
    });
    render(<AgentShortcutRow agentId="abc" onLaunch={vi.fn()} />);
    const btn = screen.getByRole("button", { name: "Container shell" });
    expect(btn).toBeInTheDocument();
    expect(btn.querySelector("[data-label]")).toBeNull();
  });

  it("calls onLaunch with agentId and shortcut idx when button is clicked", async () => {
    const onLaunch = vi.fn();
    vi.mocked(useAgentShortcutsModule.useAgentShortcuts).mockReturnValue({
      shortcuts: [
        { idx: 2, label: "Web dashboard", icon: "dashboard", kind: "dashboard", requires_capability: "agent.dashboard" },
      ],
      loading: false,
      error: null,
    });
    const { getByRole } = render(<AgentShortcutRow agentId="xyz" onLaunch={onLaunch} />);
    getByRole("button", { name: "Web dashboard" }).click();
    expect(onLaunch).toHaveBeenCalledWith("xyz", 2);
  });
});
