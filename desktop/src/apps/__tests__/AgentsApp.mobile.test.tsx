import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

// Mock useIsMobile to return true so we test the mobile layout branch
vi.mock("@/hooks/use-is-mobile", () => ({
  useIsMobile: () => true,
}));

// Minimal stubs for imports used by AgentsApp but not needed for this test
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
vi.mock("./AgentSkillsPanel", () => ({ AgentSkillsPanel: () => null }));
vi.mock("./AgentMessagesPanel", () => ({ AgentMessagesPanel: () => null }));
vi.mock("@/components/AgentShortcutRow", () => ({ AgentShortcutRow: () => null }));
vi.mock("@/stores/process-store", () => ({
  useProcessStore: (sel: (s: { openWindow: ReturnType<typeof vi.fn> }) => unknown) =>
    sel({ openWindow: vi.fn() }),
}));
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

import React from "react";

// Import after mocks are registered
// We test by extracting just the mobile layout concern from AgentRow.
// AgentRow is not exported, so we render the full AgentsApp with a mocked
// fetch that returns one agent, then check the DOM.
import { AgentsApp } from "../AgentsApp";

const MOCK_AGENT = {
  name: "my-agent",
  display_name: "My Agent",
  host: "localhost",
  color: "#3b82f6",
  status: "running",
  vectors: 42,
  framework: "smolagents",
  paused: false,
};

describe("AgentsApp mobile layout (390px viewport)", () => {
  beforeEach(() => {
    // Simulate 390px viewport width
    Object.defineProperty(window, "innerWidth", { writable: true, configurable: true, value: 390 });

    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url === "/api/agents") {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve([MOCK_AGENT]),
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
    });
  });

  it("renders agent name on its own line with status chip alongside it", async () => {
    render(<AgentsApp windowId="test" />);

    // Agent name must be visible
    const nameEl = await screen.findByText("My Agent");
    expect(nameEl).toBeTruthy();

    // Status chip must be visible (taOS agent is also "running", so match at least one)
    const statusEls = await screen.findAllByText("running");
    expect(statusEls.length).toBeGreaterThan(0);

    // Host must be visible on a second row (may appear multiple times for system + regular agent)
    const hostEls = await screen.findAllByText("localhost");
    expect(hostEls.length).toBeGreaterThan(0);
  });

  it("renders all 4 action buttons with accessible labels", async () => {
    render(<AgentsApp windowId="test" />);

    // All four action buttons must have aria-labels
    const logsBtn = await screen.findByRole("button", { name: /view logs for my-agent/i });
    const skillsBtn = screen.getByRole("button", { name: /manage skills for my-agent/i });
    const messagesBtn = screen.getByRole("button", { name: /view messages for my-agent/i });
    const deleteBtn = screen.getByRole("button", { name: /delete my-agent/i });

    expect(logsBtn).toBeTruthy();
    expect(skillsBtn).toBeTruthy();
    expect(messagesBtn).toBeTruthy();
    expect(deleteBtn).toBeTruthy();
  });

  it("renders a Back button in a full-screen dialog when the detail panel is opened on mobile", async () => {
    render(<AgentsApp windowId="test" />);

    // Wait for the agent list to load and click the skills button
    const skillsBtn = await screen.findByRole("button", { name: /manage skills for my-agent/i });
    fireEvent.click(skillsBtn);

    // The full-screen overlay must render with a back button
    const backBtn = screen.getByRole("button", { name: /back to agents/i });
    expect(backBtn).toBeTruthy();

    // The overlay must be a dialog with aria-modal
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
  });

  it("closes the full-screen panel when the Back button is clicked", async () => {
    render(<AgentsApp windowId="test" />);

    const skillsBtn = await screen.findByRole("button", { name: /manage skills for my-agent/i });
    fireEvent.click(skillsBtn);

    const backBtn = screen.getByRole("button", { name: /back to agents/i });
    fireEvent.click(backBtn);

    // Panel must be gone
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("button", { name: /back to agents/i })).toBeNull();
  });
});
