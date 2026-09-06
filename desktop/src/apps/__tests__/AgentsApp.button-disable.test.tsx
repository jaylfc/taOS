import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";

// Stub heavy deps (same pattern as AgentsApp.test.tsx)
vi.mock("@/hooks/use-is-mobile", () => ({ useIsMobile: () => false }));
vi.mock("@/lib/framework-api", () => ({ fetchLatestFrameworks: async () => ({}) }));
vi.mock("@/lib/models", () => ({
  fetchClusterWorkers: async () => [],
  workersToAggregated: () => [],
  HOST_BADGE_CLASS: "",
  CLOUD_PROVIDER_TYPES: [],
}));
vi.mock("@/lib/cluster", () => ({
  availableKvQuantOptions: () => ({ k: ["fp16"], v: ["fp16"], boundary: false, flat: ["fp16"] }),
}));
vi.mock("@/lib/agent-emoji", () => ({ resolveAgentEmoji: () => "🤖" }));
vi.mock("@/components/EmojiPicker", () => ({ EmojiPickerField: () => null }));
vi.mock("@/components/ModelPickerFlow", () => ({ ModelPickerFlow: () => null }));
vi.mock("@/components/ModelPickerModal", () => ({ ModelPickerModal: () => null }));
vi.mock("@/components/persona-picker/PersonaPicker", () => ({ PersonaPicker: () => null }));
vi.mock("@/lib/slug", () => ({
  slugifyClient: (s: string) => s,
  isValidSlug: () => true,
  SLUG_REGEX: /^[a-z0-9][a-z0-9-]{0,62}$/,
}));
vi.mock("@/components/MigrationBanner", () => ({ MigrationBanner: () => null }));
vi.mock("@/components/agent-settings/PersonaTab", () => ({ PersonaTab: () => null }));
vi.mock("@/components/agent-settings/MemoryTab", () => ({ MemoryTab: () => null }));
vi.mock("@/components/agent-settings/FrameworkTab", () => ({ FrameworkTab: () => null }));
vi.mock("../AgentSkillsPanel", () => ({ AgentSkillsPanel: () => null }));
vi.mock("../AgentMessagesPanel", () => ({ AgentMessagesPanel: () => null }));
vi.mock("@/components/ui", () => ({
  Button: ({ children, onClick, className, disabled, "aria-label": ariaLabel, title, ...rest }:
    React.ButtonHTMLAttributes<HTMLButtonElement> & { children?: React.ReactNode }) => (
    <button
      onClick={onClick}
      className={className}
      disabled={disabled}
      aria-label={ariaLabel}
      title={title}
      {...rest}
    >
      {children}
    </button>
  ),
  Card: ({ children, className }: { children: React.ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  ),
  Input: (props: React.InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
  Label: ({ children }: { children: React.ReactNode }) => <label>{children}</label>,
  Tabs: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsList: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsTrigger: ({ children }: { children: React.ReactNode }) => <button>{children}</button>,
}));
vi.mock("@/stores/process-store", () => ({
  useProcessStore: (sel: (s: { openWindow: ReturnType<typeof vi.fn> }) => unknown) =>
    sel({ openWindow: vi.fn() }),
}));

// Mock AgentShortcutRow
vi.mock("@/components/AgentShortcutRow", () => ({
  AgentShortcutRow: ({ agentId, onLaunch }: { agentId: string; onLaunch: unknown }) => (
    <div data-testid={`shortcut-row-${agentId}`} data-has-launch={typeof onLaunch === "function" ? "true" : "false"} />
  ),
}));

// Mock notification store so we can assert toast was shown
const mockAddNotification = vi.fn();
vi.mock("@/stores/notification-store", () => ({
  useNotificationStore: { getState: () => ({ addNotification: mockAddNotification }) },
}));

import { AgentsApp } from "../AgentsApp";

const STOPPED_AGENT = {
  name: "agent-stopped",
  display_name: "Stopped Agent",
  host: "localhost",
  color: "#ef4444",
  status: "stopped",
  vectors: 0,
  framework: "openclaw",
  paused: false,
};

const RUNNING_AGENT = {
  name: "agent-running",
  display_name: "Running Agent",
  host: "localhost",
  color: "#22c55e",
  status: "running",
  vectors: 10,
  framework: "openclaw",
  paused: false,
};

describe("AgentsApp — button disable for non-running agents (PR #469)", () => {
  beforeEach(() => {
    mockAddNotification.mockReset();
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url === "/api/agents") {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve([STOPPED_AGENT]),
        } as unknown as Response);
      }
      if (url === "/api/agents/archived") {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve([]),
        } as unknown as Response);
      }
      return Promise.resolve({
        ok: false,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve({}),
      } as unknown as Response);
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("disables logs/skills/messages buttons when agent is stopped", async () => {
    render(<AgentsApp windowId="test" />);

    const logsBtn = await screen.findByRole("button", { name: /view logs for agent-stopped/i });
    expect(logsBtn).toBeDisabled();
    expect(logsBtn.className).toContain("cursor-not-allowed");
    expect(logsBtn.getAttribute("aria-label")).toContain("Agent is not running");
    expect(logsBtn.getAttribute("title")).toBe("Agent must be running to view logs");

    const skillsBtn = screen.getByRole("button", { name: /manage skills for agent-stopped/i });
    expect(skillsBtn).toBeDisabled();
    expect(skillsBtn.getAttribute("title")).toBe("Agent must be running to manage skills");

    const msgsBtn = screen.getByRole("button", { name: /view messages for agent-stopped/i });
    expect(msgsBtn).toBeDisabled();
    expect(msgsBtn.getAttribute("title")).toBe("Agent must be running to view messages");
  });

  it("delete button stays enabled when agent is stopped", async () => {
    render(<AgentsApp windowId="test" />);

    const deleteBtn = await screen.findByRole("button", { name: /delete agent-stopped/i });
    expect(deleteBtn).not.toBeDisabled();
    expect(deleteBtn.className).not.toContain("cursor-not-allowed");
  });
});

describe("AgentsApp — buttons enabled for running agents", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url === "/api/agents") {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve([RUNNING_AGENT]),
        } as unknown as Response);
      }
      if (url === "/api/agents/archived") {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve([]),
        } as unknown as Response);
      }
      return Promise.resolve({
        ok: false,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve({}),
      } as unknown as Response);
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("enables logs/skills/messages buttons when agent is running", async () => {
    render(<AgentsApp windowId="test" />);

    const logsBtn = await screen.findByRole("button", { name: /view logs for agent-running/i });
    expect(logsBtn).not.toBeDisabled();
    expect(logsBtn.getAttribute("title")).toBe("View Logs");
    expect(logsBtn.getAttribute("aria-label")).not.toContain("Agent is not running");

    const skillsBtn = screen.getByRole("button", { name: /manage skills for agent-running/i });
    expect(skillsBtn).not.toBeDisabled();
    expect(skillsBtn.getAttribute("title")).toBe("Skills");

    const msgsBtn = screen.getByRole("button", { name: /view messages for agent-running/i });
    expect(msgsBtn).not.toBeDisabled();
    expect(msgsBtn.getAttribute("title")).toBe("Messages");
  });
});
