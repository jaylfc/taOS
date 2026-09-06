/**
 * Tests for the taOS system agent rendered as a standard AgentRow
 * with destructive actions hidden and settings wired to /api/taos-agent/*.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import React from "react";

// Stub heavy deps (same pattern as other AgentsApp tests)
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
vi.mock("@/components/AgentShortcutRow", () => ({
  AgentShortcutRow: () => null,
}));
vi.mock("@/stores/process-store", () => ({
  useProcessStore: (sel: (s: { openWindow: ReturnType<typeof vi.fn> }) => unknown) =>
    sel({ openWindow: vi.fn() }),
}));
vi.mock("@/components/ui", () => ({
  Button: ({ children, onClick, className, disabled, "aria-label": ariaLabel, title, ...rest }:
    React.ButtonHTMLAttributes<HTMLButtonElement> & { children?: React.ReactNode }) => (
    <button onClick={onClick} className={className} disabled={disabled}
      aria-label={ariaLabel} title={title} {...rest}>
      {children}
    </button>
  ),
  Card: ({ children, className }: { children: React.ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  ),
  Input: (props: React.InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
  Label: ({ children }: { children: React.ReactNode }) => <label>{children}</label>,
  Tabs: ({ children, value, onValueChange }: { children: React.ReactNode; value?: string; onValueChange?: (v: string) => void }) => (
    <div data-value={value} onClick={() => {}}>{children}</div>
  ),
  TabsContent: ({ children, value }: { children: React.ReactNode; value?: string }) => (
    <div data-tab-content={value}>{children}</div>
  ),
  TabsList: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsTrigger: ({ children, value, onClick }: { children: React.ReactNode; value?: string; onClick?: () => void }) => (
    <button data-tab-trigger={value} onClick={onClick}>{children}</button>
  ),
}));

import { AgentsApp } from "../AgentsApp";

// Helper: build a minimal fetch mock that includes taos-agent/config
function makeFetch(agents: unknown[], taosConfig?: object) {
  return vi.fn().mockImplementation((url: string) => {
    if (url === "/api/agents") {
      return Promise.resolve({
        ok: true,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve(agents),
      } as unknown as Response);
    }
    if (url === "/api/agents/archived") {
      return Promise.resolve({
        ok: true,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve([]),
      } as unknown as Response);
    }
    if (url === "/api/taos-agent/config") {
      return Promise.resolve({
        ok: true,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve(taosConfig ?? {
          model: "ollama/llama3",
          permitted_models: ["ollama/llama3"],
          persona: "",
          key_masked: "sk-test…key",
          framework: "opencode",
          system: true,
        }),
      } as unknown as Response);
    }
    return Promise.resolve({
      ok: false,
      headers: { get: () => "application/json" },
      json: () => Promise.resolve({}),
    } as unknown as Response);
  });
}

const MOCK_AGENT = {
  name: "agent-alpha",
  display_name: "Agent Alpha",
  host: "localhost",
  color: "#3b82f6",
  status: "running",
  vectors: 10,
  framework: "openclaw",
  paused: false,
};

describe("AgentsApp — taOS system agent rendered as standard AgentRow", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", makeFetch([MOCK_AGENT]));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the 'System agent' section header", async () => {
    render(<AgentsApp windowId="test" />);
    await waitFor(() =>
      expect(screen.getByText(/system agent/i)).toBeInTheDocument()
    );
  });

  it("renders the taOS agent name in the same card structure as regular agents", async () => {
    render(<AgentsApp windowId="test" />);
    await waitFor(() =>
      expect(screen.getByText(/taOS agent/i)).toBeInTheDocument()
    );
  });

  it("does NOT render a Delete button for the taOS system agent", async () => {
    render(<AgentsApp windowId="test" />);
    // Wait for agents to load
    await screen.findByText(/taOS agent/i);
    // Delete button for the system agent must not exist
    expect(screen.queryByRole("button", { name: /delete taos-agent/i })).toBeNull();
  });

  it("DOES render a Delete button for a regular deployed agent", async () => {
    render(<AgentsApp windowId="test" />);
    const deleteBtn = await screen.findByRole("button", { name: /delete agent-alpha/i });
    expect(deleteBtn).toBeInTheDocument();
  });

  it("the taOS agent's logs button opens TaosAgentDetailPanel (not regular detail panel)", async () => {
    render(<AgentsApp windowId="test" />);
    // System agent is always running so the logs button is enabled
    const logsBtn = await screen.findByRole("button", {
      name: /view logs for taos-agent/i,
    });
    expect(logsBtn).not.toBeDisabled();
    fireEvent.click(logsBtn);
    // The taOS detail panel has a close button
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /close detail panel/i })).toBeInTheDocument()
    );
  });

  it("does NOT show a Resume button for the taOS system agent even when paused would normally show it", async () => {
    render(<AgentsApp windowId="test" />);
    await screen.findByText(/taOS agent/i);
    // No resume button scoped to the system agent
    expect(screen.queryByRole("button", { name: /resume taos-agent/i })).toBeNull();
  });
});

describe("AgentsApp — taOS agent when no deployed agents exist", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", makeFetch([]));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("still renders the taOS agent card in the empty-state view", async () => {
    render(<AgentsApp windowId="test" />);
    await waitFor(() =>
      expect(screen.getByText(/taOS agent/i)).toBeInTheDocument()
    );
    // Confirm no deployed count text says something useful
    expect(screen.getByText("0 deployed")).toBeInTheDocument();
  });

  it("delete is absent for taOS agent in empty-state view", async () => {
    render(<AgentsApp windowId="test" />);
    await screen.findByText(/taOS agent/i);
    expect(screen.queryByRole("button", { name: /delete taos-agent/i })).toBeNull();
  });
});
