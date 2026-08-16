/**
 * DeployWizard — memory mode + plugin pair coherence (tsk-m23asr).
 *
 * Verifies that skipping the taOSmd memory layer makes the "both" and
 * "taosmd" memory_mode buttons unselectable (disabled) and that the
 * wizard cannot produce the incoherent pair (null plugin + both/taosmd
 * mode) that the server rejects with a 400.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";

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
vi.mock("@/components/AgentShortcutRow", () => ({
  AgentShortcutRow: () => null,
}));
vi.mock("@/stores/notification-store", () => ({
  useNotificationStore: { getState: () => ({ addNotification: vi.fn() }) },
}));

// PersonaPicker that immediately auto-selects, advancing to step 1.
vi.mock("@/components/persona-picker/PersonaPicker", () => ({
  PersonaPicker: ({ onSelect }: { onSelect: (s: unknown) => void }) => {
    React.useEffect(() => {
      onSelect({ soul_md: "", agent_md: "", source_persona_id: null, save_to_library: null });
    }, [onSelect]);
    return null;
  },
}));

// ModelPickerFlow that auto-selects a model when it renders (step 3).
vi.mock("@/components/ModelPickerFlow", () => ({
  ModelPickerFlow: ({ onSelect }: { onSelect: (s: string) => void }) => {
    React.useEffect(() => {
      onSelect("gpt-4o");
    }, [onSelect]);
    return null;
  },
}));

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

vi.mock("@/stores/process-store", () => ({
  useProcessStore: (sel: (s: { openWindow: ReturnType<typeof vi.fn> }) => unknown) =>
    sel({ openWindow: vi.fn() }),
}));
vi.mock("@/registry/app-registry", () => ({ getApp: () => null }));

import { AgentsApp } from "../AgentsApp";

const MOCK_FRAMEWORK = {
  id: "openclaw",
  name: "OpenClaw",
  description: "General purpose agent",
  verification_status: "beta",
};

const AGENTS_RESP = [{ name: "test", display_name: "Test", host: "", color: "#888", status: "running", model: "phi3" }];

const makeFetch = vi.fn(async (url: string) => {
  const u = String(url);
  if (u.includes("/api/agents") && !u.includes("deploy")) {
    return { ok: true, headers: { get: () => "application/json" }, json: async () => AGENTS_RESP };
  }
  if (u.includes("/api/agents/archived")) {
    return { ok: true, headers: { get: () => "application/json" }, json: async () => [] };
  }
  if (u.includes("/api/frameworks")) {
    return { ok: true, headers: { get: () => "application/json" }, json: async () => [MOCK_FRAMEWORK] };
  }
  if (u.includes("/api/taosmd/default")) {
    return { ok: false, headers: { get: () => "application/json" }, json: async () => ({}) };
  }
  if (u.includes("/api/cluster/install-targets")) {
    return { ok: true, headers: { get: () => "application/json" }, json: async () => [{ name: "local", friendly_name: "Controller", tier_id: "arm-vulkan-8gb" }] };
  }
  if (u.includes("/api/models")) {
    return { ok: true, headers: { get: () => "application/json" }, json: async () => ({ models: [] }) };
  }
  if (u.includes("/api/providers")) {
    return { ok: true, headers: { get: () => "application/json" }, json: async () => [] };
  }
  if (u.includes("/api/cluster/kv-quant-options")) {
    return { ok: true, headers: { get: () => "application/json" }, json: async () => ({ k: ["fp16"], v: ["fp16"] }) };
  }
  return { ok: true, headers: { get: () => "application/json" }, json: async () => ({}) };
});

async function advanceToMemoryStep() {
  global.fetch = makeFetch as unknown as typeof fetch;

  render(
    <AgentsApp
      isActive={true}
      isMobile={false}
      onShortcutClick={() => {}}
      activeShortcuts={[]}
      onWindowOpen={() => {}}
    />,
  );

  // Open wizard
  const deployBtn = await screen.findByRole("button", { name: /new agent/i });
  fireEvent.click(deployBtn);

  // Step 0: PersonaPicker auto-advances to step 1
  await waitFor(() => screen.getByPlaceholderText("my-agent"));

  // Step 1: Name + Next
  fireEvent.change(screen.getByPlaceholderText("my-agent"), { target: { value: "test-agent" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));

  // Step 2: Framework — select the mocked framework, then Next
  await waitFor(() => screen.getByRole("button", { name: /openclaw/i }));
  fireEvent.click(screen.getByRole("button", { name: /openclaw/i }));
  await waitFor(() => screen.getByRole("button", { name: /next/i }));
  fireEvent.click(screen.getByRole("button", { name: /next/i }));

  // Step 3: ModelPickerFlow auto-selects "gpt-4o" → Next becomes enabled
  await waitFor(() => {
    const nextBtn = screen.getByRole("button", { name: /next/i });
    return expect(nextBtn).not.toBeDisabled();
  });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));

  // Step 4: Memory — wait for the Memory Mode heading
  await waitFor(() => screen.getByText("Memory Mode"));
}

describe("DeployWizard — memory mode/plugin pair coherence", () => {
  beforeEach(() => {
    makeFetch.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("enables both and taosmd mode buttons when memory layer is on", async () => {
    await advanceToMemoryStep();

    const bothBtn = screen.getByText("Both").closest("button")!;
    const taosmdBtn = screen.getByText("taOSmd only").closest("button")!;
    expect(bothBtn).not.toBeDisabled();
    expect(taosmdBtn).not.toBeDisabled();
  });

  it("disables both and taosmd mode buttons after clicking Skip memory", async () => {
    await advanceToMemoryStep();

    // Click "Skip memory for this agent" inside the MemoryWizardStep.
    const skipLink = await screen.findByText(/skip memory for this agent/i);
    fireEvent.click(skipLink);

    // After the useEffect snaps memoryMode to "framework", both and taosmd
    // buttons must be disabled, and "framework only" must be selected.
    const bothBtn = screen.getByText("Both").closest("button")!;
    const taosmdBtn = screen.getByText("taOSmd only").closest("button")!;
    const fwBtn = screen.getByText("Framework only").closest("button")!;

    await waitFor(() => {
      expect(bothBtn).toBeDisabled();
      expect(taosmdBtn).toBeDisabled();
    });
    expect(bothBtn).toHaveAttribute("title", "needs the taOSmd memory layer");
    expect(taosmdBtn).toHaveAttribute("title", "needs the taOSmd memory layer");
    expect(fwBtn).not.toBeDisabled();
  });

  it("does not allow selecting a disabled mode button via click", async () => {
    await advanceToMemoryStep();

    const skipLink = await screen.findByText(/skip memory for this agent/i);
    fireEvent.click(skipLink);

    const bothBtn = screen.getByText("Both").closest("button")!;
    await waitFor(() => expect(bothBtn).toBeDisabled());

    // Clicking a disabled button must not change the mode.
    fireEvent.click(bothBtn);
    await waitFor(() => {
      const fwBtn = screen.getByText("Framework only").closest("button")!;
      expect(fwBtn).toHaveClass("border-accent");
    });
  });

  it("snaps the selected mode to framework after clicking Skip memory", async () => {
    await advanceToMemoryStep();

    // Initially "Both" is selected (default mode).
    const bothBtn = screen.getByText("Both").closest("button")!;
    expect(bothBtn).toHaveClass("border-accent");

    // Click "Skip memory for this agent".
    const skipLink = await screen.findByText(/skip memory for this agent/i);
    fireEvent.click(skipLink);

    // After the useEffect snaps memoryMode to "framework", the
    // "Framework only" button must be the selected one.
    const fwBtn = screen.getByText("Framework only").closest("button")!;
    await waitFor(() => {
      expect(fwBtn).toHaveClass("border-accent");
    });
  });
});
