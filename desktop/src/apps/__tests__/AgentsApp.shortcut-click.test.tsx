import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
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

const mockOpenWindow = vi.fn();
vi.mock("@/stores/process-store", () => ({
  useProcessStore: (sel: (s: { openWindow: ReturnType<typeof vi.fn> }) => unknown) =>
    sel({ openWindow: mockOpenWindow }),
}));

// Mock AgentShortcutRow to expose an onLaunch capture mechanism
type LaunchCaptor = { onLaunch?: (agentId: string, shortcut: unknown) => void };
const captors: Record<string, LaunchCaptor> = {};

vi.mock("@/components/AgentShortcutRow", () => ({
  AgentShortcutRow: ({ agentId, onLaunch }: { agentId: string; onLaunch: (agentId: string, shortcut: unknown) => void }) => {
    captors[agentId] = { onLaunch };
    return <div data-testid={`shortcut-row-${agentId}`} />;
  },
}));

import { AgentsApp } from "../AgentsApp";
import { useNotificationStore } from "@/stores/notification-store";

const MOCK_AGENT = {
  name: "test-agent",
  display_name: "Test Agent",
  host: "localhost",
  color: "#3b82f6",
  status: "running",
  vectors: 1,
  framework: "openclaw",
  paused: false,
};

function setupFetch(launchResponse: { redirect_url: string; expires_in: number }) {
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
    if (url === "/api/agents" && !opts?.method) {
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
    if (url.includes("/shortcuts/") && url.includes("/launch")) {
      return Promise.resolve({
        ok: true,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve(launchResponse),
      } as unknown as Response);
    }
    return Promise.resolve({
      ok: false,
      headers: { get: () => "application/json" },
      json: () => Promise.resolve({}),
    } as unknown as Response);
  }));
}

describe("AgentsApp — handleShortcutLaunch routing (Task 28)", () => {
  beforeEach(() => {
    mockOpenWindow.mockClear();
    Object.keys(captors).forEach((k) => delete captors[k]);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("dashboard kind opens BrowserApp with initialUrl from redirect_url", async () => {
    const redirectUrl = "http://worker.local/redeem?t=abc123";
    setupFetch({ redirect_url: redirectUrl, expires_in: 30 });

    render(<AgentsApp windowId="test" />);
    await screen.findByTestId("shortcut-row-test-agent");

    const { onLaunch } = captors["test-agent"]!;
    await act(async () => {
      await onLaunch!("test-agent", { idx: 0, label: "Dashboard", icon: "dashboard", kind: "dashboard", requires_capability: "agent.dashboard" });
    });

    expect(mockOpenWindow).toHaveBeenCalledWith(
      "browser",
      expect.objectContaining({ w: expect.any(Number), h: expect.any(Number) }),
      expect.objectContaining({ initialUrl: redirectUrl })
    );
  });

  it("tui kind opens TerminalApp pointed at the PTY endpoint (not /redeem) and redeems the cookie first", async () => {
    const redirectUrl = "http://worker.local/redeem?t=myticket42";
    setupFetch({ redirect_url: redirectUrl, expires_in: 30 });

    render(<AgentsApp windowId="test" />);
    await screen.findByTestId("shortcut-row-test-agent");

    const { onLaunch } = captors["test-agent"]!;
    await act(async () => {
      await onLaunch!("test-agent", { idx: 1, label: "TUI", icon: "tui", kind: "tui", requires_capability: "agent.terminal" });
    });

    // The cookie-establishing GET /redeem must happen before the socket opens.
    expect(fetch).toHaveBeenCalledWith(
      redirectUrl,
      expect.objectContaining({ credentials: "include" }),
    );
    expect(mockOpenWindow).toHaveBeenCalledWith(
      "terminal",
      expect.objectContaining({ w: expect.any(Number), h: expect.any(Number) }),
      expect.objectContaining({
        shortcut: expect.objectContaining({
          wsUrl: "ws://worker.local/shortcut/terminal/test-agent/1",
          ticket: "myticket42",
        }),
      })
    );
  });

  it("https redirect_url yields a wss:// PTY endpoint", async () => {
    const redirectUrl = "https://worker.secure/redeem?t=secureticket";
    setupFetch({ redirect_url: redirectUrl, expires_in: 30 });

    render(<AgentsApp windowId="test" />);
    await screen.findByTestId("shortcut-row-test-agent");

    const { onLaunch } = captors["test-agent"]!;
    await act(async () => {
      await onLaunch!("test-agent", { idx: 1, label: "TUI", icon: "tui", kind: "tui", requires_capability: "agent.terminal" });
    });

    expect(mockOpenWindow).toHaveBeenCalledWith(
      "terminal",
      expect.objectContaining({ w: expect.any(Number), h: expect.any(Number) }),
      expect.objectContaining({
        shortcut: expect.objectContaining({
          wsUrl: "wss://worker.secure/shortcut/terminal/test-agent/1",
          ticket: "secureticket",
        }),
      })
    );
  });

  it("container-terminal kind opens TerminalApp pointed at the PTY endpoint", async () => {
    const redirectUrl = "http://worker.local/redeem?t=containerticket";
    setupFetch({ redirect_url: redirectUrl, expires_in: 30 });

    render(<AgentsApp windowId="test" />);
    await screen.findByTestId("shortcut-row-test-agent");

    const { onLaunch } = captors["test-agent"]!;
    await act(async () => {
      await onLaunch!("test-agent", { idx: 2, label: "Shell", icon: "terminal", kind: "container-terminal", requires_capability: "agent.shell" });
    });

    expect(mockOpenWindow).toHaveBeenCalledWith(
      "terminal",
      expect.objectContaining({ w: expect.any(Number), h: expect.any(Number) }),
      expect.objectContaining({
        shortcut: expect.objectContaining({
          wsUrl: "ws://worker.local/shortcut/terminal/test-agent/2",
          ticket: "containerticket",
        }),
      })
    );
  });

  it("surfaces an error notification when the launch request fails (no silent no-op)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (url === "/api/agents" && !opts?.method) {
        return Promise.resolve({ ok: true, headers: { get: () => "application/json" }, json: () => Promise.resolve([MOCK_AGENT]) } as unknown as Response);
      }
      if (url === "/api/agents/archived") {
        return Promise.resolve({ ok: true, headers: { get: () => "application/json" }, json: () => Promise.resolve([]) } as unknown as Response);
      }
      if (url.includes("/shortcuts/") && url.includes("/launch")) {
        return Promise.resolve({ ok: false, status: 403, headers: { get: () => "application/json" }, json: () => Promise.resolve({ detail: "Capability 'agent.shell' required" }) } as unknown as Response);
      }
      return Promise.resolve({ ok: false, headers: { get: () => "application/json" }, json: () => Promise.resolve({}) } as unknown as Response);
    }));

    const before = useNotificationStore.getState().notifications.length;
    render(<AgentsApp windowId="test" />);
    await screen.findByTestId("shortcut-row-test-agent");

    const { onLaunch } = captors["test-agent"]!;
    await act(async () => {
      await onLaunch!("test-agent", { idx: 2, label: "Shell", icon: "terminal", kind: "container-terminal", requires_capability: "agent.shell" });
    });

    expect(mockOpenWindow).not.toHaveBeenCalled();
    const notifs = useNotificationStore.getState().notifications;
    expect(notifs.length).toBe(before + 1);
    expect(notifs[0].level).toBe("error");
    expect(notifs[0].body).toContain("agent.shell");
  });
});
