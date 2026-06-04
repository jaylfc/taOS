import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";

// Stub heavy deps first
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
  Button: ({ children, onClick, className, ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement> & { children?: React.ReactNode }) => (
    <button onClick={onClick} className={className} {...rest}>{children}</button>
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

// Mock AgentShortcutRow so we can verify it's rendered with the right agentId
vi.mock("@/components/AgentShortcutRow", () => ({
  AgentShortcutRow: ({ agentId, onLaunch }: { agentId: string; onLaunch: unknown }) => (
    <div data-testid={`shortcut-row-${agentId}`} data-has-launch={typeof onLaunch === "function" ? "true" : "false"} />
  ),
}));

import { AgentsApp } from "../AgentsApp";

const MOCK_AGENTS = [
  {
    name: "agent-alpha",
    display_name: "Agent Alpha",
    host: "localhost",
    color: "#3b82f6",
    status: "running",
    vectors: 10,
    framework: "openclaw",
    paused: false,
  },
  {
    name: "agent-beta",
    display_name: "Agent Beta",
    host: "localhost",
    color: "#8b5cf6",
    status: "stopped",
    vectors: 5,
    framework: "smolagents",
    paused: false,
  },
];

describe("AgentsApp — AgentShortcutRow wiring (Task 27)", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url === "/api/agents") {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve(MOCK_AGENTS),
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

  it("renders an AgentShortcutRow for each agent in the list", async () => {
    render(<AgentsApp windowId="test" />);

    const rowAlpha = await screen.findByTestId("shortcut-row-agent-alpha");
    expect(rowAlpha).toBeInTheDocument();

    const rowBeta = screen.getByTestId("shortcut-row-agent-beta");
    expect(rowBeta).toBeInTheDocument();
  });

  it("passes an onLaunch function to each AgentShortcutRow", async () => {
    render(<AgentsApp windowId="test" />);

    const rowAlpha = await screen.findByTestId("shortcut-row-agent-alpha");
    expect(rowAlpha.getAttribute("data-has-launch")).toBe("true");

    const rowBeta = screen.getByTestId("shortcut-row-agent-beta");
    expect(rowBeta.getAttribute("data-has-launch")).toBe("true");
  });

  it("does NOT render a Back button or full-screen dialog when the detail panel is opened on desktop", async () => {
    render(<AgentsApp windowId="test" />);

    // Open the detail panel via the logs button on the first agent
    const logsBtn = await screen.findByRole("button", { name: /view logs for agent-alpha/i });
    fireEvent.click(logsBtn);

    // No back button and no dialog wrapper on desktop
    expect(screen.queryByRole("button", { name: /back to agents/i })).toBeNull();
    expect(screen.queryByRole("dialog", { name: /agent details/i })).toBeNull();
  });
});

describe("AgentsApp — framework label", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url === "/api/agents") {
        return Promise.resolve({
          ok: true, headers: { get: () => "application/json" },
          json: () => Promise.resolve(MOCK_AGENTS),
        } as unknown as Response);
      }
      return Promise.resolve({
        ok: true, headers: { get: () => "application/json" },
        json: () => Promise.resolve([]),
      } as unknown as Response);
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("shows each agent's framework as a labelled pill", async () => {
    render(<AgentsApp windowId="test" />);
    // The row surfaces the framework name (not just the emoji) for clarity.
    expect(await screen.findByLabelText(/Framework: openclaw/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Framework: smolagents/i)).toBeInTheDocument();
  });
});
