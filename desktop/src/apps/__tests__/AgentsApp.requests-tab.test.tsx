import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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
vi.mock("@/components/AgentShortcutRow", () => ({
  AgentShortcutRow: ({ agentId, onLaunch }: { agentId: string; onLaunch: unknown }) => (
    <div data-testid={`shortcut-row-${agentId}`} data-has-launch={typeof onLaunch === "function" ? "true" : "false"} />
  ),
}));

import { AgentsApp } from "../AgentsApp";

describe("AgentsApp — requests tab gating", () => {
  beforeEach(() => {
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
          json: () => Promise.resolve([{ id: "arch-1", name: "old-agent" }]),
        } as unknown as Response);
      }
      if (url === "/auth/status") {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ user: { is_admin: false, id: "user-1" } }),
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

  it("non-admin with no owned agents does not see the Requests tab even when archived agents exist", async () => {
    render(<AgentsApp windowId="test" />);
    await waitFor(() => {
      expect(screen.getByText(/taos agent/i)).toBeInTheDocument();
    });
    const requestsTab = screen.queryByRole("tab", { name: /requests/i });
    expect(requestsTab).not.toBeInTheDocument();
  });

  it("non-admin who owns no agents does not see Requests tab when other users have agents", async () => {
    (global.fetch as any).mockImplementation((url: string) => {
      if (url === "/api/agents") {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve([{ name: "other-user-agent" }]),
        } as unknown as Response);
      }
      if (url === "/api/agents/registry") {
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
          json: () => Promise.resolve([{ id: "arch-1", name: "old-agent" }]),
        } as unknown as Response);
      }
      if (url === "/auth/status") {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ user: { is_admin: false, id: "user-1" } }),
        } as unknown as Response);
      }
      return Promise.resolve({
        ok: false,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve({}),
      } as unknown as Response);
    });

    render(<AgentsApp windowId="test" />);
    await waitFor(() => {
      expect(screen.getByText(/taos agent/i)).toBeInTheDocument();
    });
    const requestsTab = screen.queryByRole("tab", { name: /requests/i });
    expect(requestsTab).not.toBeInTheDocument();
  });

  it("non-admin who owns an agent sees Requests tab", async () => {
    (global.fetch as any).mockImplementation((url: string) => {
      if (url === "/api/agents") {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve([{ name: "other-user-agent" }]),
        } as unknown as Response);
      }
      if (url === "/api/agents/registry") {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve([{ name: "my-agent" }]),
        } as unknown as Response);
      }
      if (url === "/api/agents/archived") {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve([{ id: "arch-1", name: "old-agent" }]),
        } as unknown as Response);
      }
      if (url === "/auth/status") {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ user: { is_admin: false, id: "user-1" } }),
        } as unknown as Response);
      }
      return Promise.resolve({
        ok: false,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve({}),
      } as unknown as Response);
    });

    render(<AgentsApp windowId="test" />);
    await waitFor(() => {
      expect(screen.getByText(/taos agent/i)).toBeInTheDocument();
    });
    const requestsTab = screen.queryByRole("tab", { name: /requests/i });
    expect(requestsTab).toBeInTheDocument();
  });

  // tsk-zfdagx: agent-filed pending scope requests must be visible to the
  // admin WITHOUT first opening the Requests tab. The app opens on the
  // Registry tab, so the badge is the only on-screen sign that an agent is
  // waiting; it must be loaded by AgentsApp itself, not only by RequestsPanel
  // (which is not mounted until the tab is clicked).
  it("admin sees the pending scope-request count on the Requests tab from the default tab", async () => {
    (global.fetch as any).mockImplementation((url: string) => {
      if (url === "/api/agents") {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve([{ name: "local-agent" }]),
        } as unknown as Response);
      }
      if (url === "/api/agents/archived") {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve([]),
        } as unknown as Response);
      }
      if (url === "/api/agents/registry") {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve([{ canonical_id: "devbot-1", status: "active" }]),
        } as unknown as Response);
      }
      if (url === "/auth/status") {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ user: { is_admin: true, id: "admin-1" } }),
        } as unknown as Response);
      }
      if (url === "/api/agents/scope-requests?status=pending") {
        const rows = ["a818d008", "5bcb992b", "94fd1743"].map((id) => ({
          id,
          canonical_id: "devbot-1",
          agent_display_name: "devbot",
          requested_scopes: ["decisions_read"],
          project_id: null,
          reason: "",
          status: "pending",
          created_ts: "2026-09-30T14:31:57+00:00",
        }));
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ requests: rows }),
        } as unknown as Response);
      }
      return Promise.resolve({
        ok: false,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve({}),
      } as unknown as Response);
    });

    render(<AgentsApp windowId="test" />);
    const requestsTab = await screen.findByRole("tab", { name: /requests/i });
    expect(requestsTab).toHaveAttribute("aria-selected", "false");
    await waitFor(() => {
      expect(requestsTab.textContent).toContain("3");
    });
  });
});
