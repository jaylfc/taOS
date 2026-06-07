/**
 * DeployWizard — empty model list banner (fix #618).
 *
 * Navigates the wizard to step 3 (Model) with no models loaded
 * and asserts the "You haven't added a provider yet" banner appears.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";

// --- Stubs (same pattern as AgentsApp.test.tsx) ---
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
  Button: ({
    children, onClick, className, disabled, variant, size, "aria-label": ariaLabel,
    ...rest
  }: React.ButtonHTMLAttributes<HTMLButtonElement> & {
    children?: React.ReactNode; variant?: string; size?: string;
  }) => (
    <button onClick={onClick} className={className} disabled={disabled} aria-label={ariaLabel} {...rest}>
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

const mockOpenWindow = vi.fn();
vi.mock("@/stores/process-store", () => ({
  useProcessStore: (sel: (s: { openWindow: typeof mockOpenWindow }) => unknown) =>
    sel({ openWindow: mockOpenWindow }),
}));
vi.mock("@/stores/notification-store", () => ({
  useNotificationStore: { getState: () => ({ addNotification: vi.fn() }) },
}));
vi.mock("@/components/AgentShortcutRow", () => ({
  AgentShortcutRow: () => null,
}));

// PersonaPicker that immediately calls onSelect so the wizard advances to step 1
vi.mock("@/components/persona-picker/PersonaPicker", () => ({
  PersonaPicker: ({ onSelect }: { onSelect: (s: unknown) => void }) => {
    React.useEffect(() => {
      onSelect({ soul_md: "", agent_md: "", save_to_library: false });
    }, [onSelect]);
    return null;
  },
}));

import { AgentsApp } from "../AgentsApp";

const MOCK_FRAMEWORK = {
  id: "openclaw",
  name: "OpenClaw",
  description: "General purpose agent",
  verification_status: "stable",
};

describe("DeployWizard — 'no provider' banner at model step (fix #618)", () => {
  beforeEach(() => {
    mockOpenWindow.mockReset();
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url === "/api/agents") {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve([]),
        } as unknown as Response);
      }
      if (url === "/api/agents/archived") {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve([]),
        } as unknown as Response);
      }
      if (url === "/api/frameworks") {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve([MOCK_FRAMEWORK]),
        } as unknown as Response);
      }
      // /api/models, /api/providers/*, /api/cluster/kv-quant-options — all fail
      return Promise.resolve({
        ok: false,
        headers: { get: () => "text/html" },
        json: () => Promise.resolve({}),
        text: () => Promise.resolve(""),
      } as unknown as Response);
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the 'no provider' banner when model list is empty", async () => {
    render(<AgentsApp windowId="test" />);

    // Open wizard
    const deployBtn = await screen.findByRole("button", { name: /deploy new agent/i });
    fireEvent.click(deployBtn);

    // PersonaPicker mock auto-advances to step 1. Fill name (match placeholder).
    const nameInput = await screen.findByPlaceholderText("my-agent");
    fireEvent.change(nameInput, { target: { value: "my-agent" } });

    // Next → step 2 (framework)
    const nextBtn = screen.getByRole("button", { name: /next/i });
    fireEvent.click(nextBtn);

    // Select framework → Next is enabled once one is selected
    const frameworkBtn = await screen.findByRole("button", { name: /openclaw/i });
    fireEvent.click(frameworkBtn);

    // Next → step 3 (model)
    const nextBtn2 = screen.getByRole("button", { name: /next/i });
    fireEvent.click(nextBtn2);

    // Banner should appear
    expect(
      await screen.findByText(/you haven't added a provider yet/i)
    ).toBeInTheDocument();
  });

  it("'Add Provider' button in banner calls openWindow with 'providers'", async () => {
    render(<AgentsApp windowId="test" />);

    const deployBtn = await screen.findByRole("button", { name: /deploy new agent/i });
    fireEvent.click(deployBtn);

    const nameInput = await screen.findByPlaceholderText("my-agent");
    fireEvent.change(nameInput, { target: { value: "my-agent" } });
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    const frameworkBtn = await screen.findByRole("button", { name: /openclaw/i });
    fireEvent.click(frameworkBtn);
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    const addProviderBtn = await screen.findByRole("button", { name: /add provider/i });
    fireEvent.click(addProviderBtn);

    expect(mockOpenWindow).toHaveBeenCalledWith("providers", expect.any(Object));
  });
});
