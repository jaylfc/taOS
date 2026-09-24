/**
 * DeployWizard — recommended_framework preselect + badge (tsk-5wvx3a).
 *
 * Verifies that when /api/hardware returns recommended_framework="picoclaw",
 * the picker preselects PicoClaw and renders the "Recommended for this device"
 * badge. When the host has >8 GB RAM, no badge is shown.
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
vi.mock("@/components/AgentShortcutRow", () => ({ AgentShortcutRow: () => null }));
vi.mock("@/registry/app-registry", () => ({ getApp: () => null }));
vi.mock("@/stores/process-store", () => ({
  useProcessStore: (sel: (s: { openWindow: ReturnType<typeof vi.fn> }) => unknown) =>
    sel({ openWindow: vi.fn() }),
}));
vi.mock("@/stores/notification-store", () => ({
  useNotificationStore: { getState: () => ({ addNotification: vi.fn() }) },
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

// PersonaPicker that auto-selects, advancing to step 1.
vi.mock("@/components/persona-picker/PersonaPicker", () => ({
  PersonaPicker: ({ onSelect }: { onSelect: (s: unknown) => void }) => {
    React.useEffect(() => {
      onSelect({ soul_md: "", agent_md: "", source_persona_id: null, save_to_library: null });
    }, [onSelect]);
    return null;
  },
}));

import { AgentsApp } from "../AgentsApp";

const MOCK_FRAMEWORKS = [
  { id: "picoclaw", name: "PicoClaw", description: "Tiny, fast, deployable anywhere", verification_status: "experimental" },
  { id: "openclaw", name: "OpenClaw", description: "Full-featured multi-channel agent", verification_status: "beta" },
];

function makeFetch(recommendedFramework: string, ramMb: number) {
  return vi.fn(async (url: string) => {
    const u = String(url);
    if (u.includes("/api/agents") && !u.includes("deploy")) {
      return { ok: true, headers: { get: () => "application/json" }, json: async () => [] };
    }
    if (u.includes("/api/agents/archived")) {
      return { ok: true, headers: { get: () => "application/json" }, json: async () => [] };
    }
    if (u.includes("/api/frameworks")) {
      return { ok: true, headers: { get: () => "application/json" }, json: async () => MOCK_FRAMEWORKS };
    }
    if (u.includes("/api/hardware")) {
      return { ok: true, headers: { get: () => "application/json" }, json: async () => ({ ram_mb: ramMb, recommended_framework: recommendedFramework }) };
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
}

describe("DeployWizard — recommended_framework preselect + badge", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", makeFetch("picoclaw", 7800));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("preselects picoclaw and renders the recommended badge when host has <=8 GB RAM", async () => {
    render(<AgentsApp windowId="test" />);

    const deployBtn = await screen.findByRole("button", { name: /deploy new agent/i });
    fireEvent.click(deployBtn);

    const nameInput = await screen.findByPlaceholderText("my-agent");
    fireEvent.change(nameInput, { target: { value: "my-agent" } });

    const nextBtn = screen.getByRole("button", { name: /next/i });
    fireEvent.click(nextBtn);

    // Step 2: Framework — PicoClaw should be preselected with recommended badge
    await waitFor(() => screen.getByText(/PicoClaw/i));
    const picoclawBtn = screen.getByText(/PicoClaw/i).closest("button")!;
    expect(picoclawBtn).toHaveClass("border-accent");
    expect(screen.getByText(/Recommended for this device/i)).toBeInTheDocument();
  });

  it("does not preselect picoclaw when host has >8 GB RAM", async () => {
    vi.stubGlobal("fetch", makeFetch("hermes", 16384));
    render(<AgentsApp windowId="test" />);

    const deployBtn = await screen.findByRole("button", { name: /deploy new agent/i });
    fireEvent.click(deployBtn);

    const nameInput = await screen.findByPlaceholderText("my-agent");
    fireEvent.change(nameInput, { target: { value: "my-agent" } });

    const nextBtn = screen.getByRole("button", { name: /next/i });
    fireEvent.click(nextBtn);

    await waitFor(() => screen.getByText(/PicoClaw/i));
    const picoclawBtn = screen.getByText(/PicoClaw/i).closest("button")!;
    expect(picoclawBtn).not.toHaveClass("border-accent");
    expect(screen.queryByText(/Recommended for this device/i)).not.toBeInTheDocument();
  });
});
