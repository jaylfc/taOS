import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import React from "react";

// Stub heavy deps (same pattern as AgentsApp.button-disable.test.tsx)
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
  Button: ({ children, ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement> & { children?: React.ReactNode }) => (
    <button {...rest}>{children}</button>
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
vi.mock("@/components/AgentShortcutRow", () => ({ AgentShortcutRow: () => null }));

import { AgentsApp } from "../AgentsApp";
import { useNotificationStore } from "@/stores/notification-store";

function agent(name: string, status: string, paused = false) {
  return { name, display_name: name, host: "localhost", color: "#3b82f6", status, vectors: 0, framework: "hermes", paused };
}

const json = (body: unknown, ok = true, status = 200) =>
  Promise.resolve({
    ok,
    status,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
  } as unknown as Response);

/** Install a fetch mock. `stored` is GET /api/agents, `live` is
 *  GET /api/agents/containers (incus status per agent). POSTs answer with
 *  `postResponse`. */
function mockFetch(
  stored: unknown[],
  live: Record<string, string>,
  postResponse: () => Promise<Response> = () => json({ success: true }),
) {
  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (init?.method === "POST") return postResponse();
    if (url === "/api/agents") return json(stored);
    if (url === "/api/agents/containers") {
      return json(
        Object.entries(live).map(([n, status]) => ({
          name: `taos-agent-${n}`, agent_name: n, status,
        })),
      );
    }
    return json([]);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function posts(fetchMock: ReturnType<typeof vi.fn>): string[] {
  return fetchMock.mock.calls
    .filter(([, init]) => (init as RequestInit | undefined)?.method === "POST")
    .map(([url]) => String(url));
}

describe("AgentsApp lifecycle controls", () => {
  beforeEach(() => {
    useNotificationStore.setState({ notifications: [] });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("follows the live container state, not the stored status", async () => {
    // Stored says running, incus says Stopped: the row must offer Start.
    mockFetch([agent("alpha", "running")], { alpha: "Stopped" });
    render(<AgentsApp windowId="test" />);
    expect(await screen.findByRole("button", { name: "Start alpha" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop alpha" })).toBeNull();
  });

  it("shows Resume for a frozen container", async () => {
    mockFetch([agent("alpha", "running")], { alpha: "Frozen" });
    render(<AgentsApp windowId="test" />);
    expect(await screen.findByRole("button", { name: "Resume alpha" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause alpha" })).toBeNull();
    expect(screen.getByLabelText("Status: Paused")).toBeInTheDocument();
  });

  it("Stop asks for confirmation before it POSTs", async () => {
    const fetchMock = mockFetch([agent("alpha", "running")], { alpha: "Running" });
    render(<AgentsApp windowId="test" />);
    fireEvent.click(await screen.findByRole("button", { name: "Stop alpha" }));

    const dialog = screen.getByRole("dialog", { name: "Stop alpha?" });
    expect(posts(fetchMock)).toEqual([]);

    fireEvent.click(within(dialog).getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(posts(fetchMock)).toEqual(["/api/agents/alpha/stop"]));
  });

  it("cancelling the Stop confirmation sends nothing", async () => {
    const fetchMock = mockFetch([agent("alpha", "running")], { alpha: "Running" });
    render(<AgentsApp windowId="test" />);
    fireEvent.click(await screen.findByRole("button", { name: "Stop alpha" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(posts(fetchMock)).toEqual([]);
  });

  it("Restart asks for confirmation, then POSTs to /restart", async () => {
    const fetchMock = mockFetch([agent("alpha", "running")], { alpha: "Running" });
    render(<AgentsApp windowId="test" />);
    fireEvent.click(await screen.findByRole("button", { name: "Restart alpha" }));
    expect(posts(fetchMock)).toEqual([]);
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "Restart alpha?" })).getByRole("button", { name: "Restart" }),
    );
    await waitFor(() => expect(posts(fetchMock)).toEqual(["/api/agents/alpha/restart"]));
  });

  it("Pause POSTs to /pause", async () => {
    const fetchMock = mockFetch([agent("alpha", "running")], { alpha: "Running" });
    render(<AgentsApp windowId="test" />);
    fireEvent.click(await screen.findByRole("button", { name: "Pause alpha" }));
    await waitFor(() => expect(posts(fetchMock)).toEqual(["/api/agents/alpha/pause"]));
  });

  it("Start POSTs to /start", async () => {
    const fetchMock = mockFetch([agent("alpha", "stopped")], { alpha: "Stopped" });
    render(<AgentsApp windowId="test" />);
    fireEvent.click(await screen.findByRole("button", { name: "Start alpha" }));
    await waitFor(() => expect(posts(fetchMock)).toEqual(["/api/agents/alpha/start"]));
  });

  it("Resume POSTs to /resume", async () => {
    const fetchMock = mockFetch([agent("alpha", "running", true)], { alpha: "Frozen" });
    render(<AgentsApp windowId="test" />);
    fireEvent.click(await screen.findByRole("button", { name: "Resume alpha" }));
    await waitFor(() => expect(posts(fetchMock)).toEqual(["/api/agents/alpha/resume"]));
  });

  it("keeps the row's controls disabled until the refreshed list arrives", async () => {
    let getCount = 0;
    let releaseRefresh: () => void = () => {};
    const refreshGate = new Promise<void>((r) => { releaseRefresh = r; });
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === "POST") return json({ status: "paused" });
      if (url === "/api/agents") {
        getCount += 1;
        // The first load answers at once; the post-action refresh waits.
        if (getCount === 1) return json([agent("alpha", "running")]);
        return refreshGate.then(() => json([agent("alpha", "running", true)]));
      }
      if (url === "/api/agents/containers") {
        return json([{ name: "taos-agent-alpha", agent_name: "alpha", status: getCount > 1 ? "Frozen" : "Running" }]);
      }
      return json([]);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AgentsApp windowId="test" />);
    fireEvent.click(await screen.findByRole("button", { name: "Pause alpha" }));
    await waitFor(() => expect(posts(fetchMock)).toEqual(["/api/agents/alpha/pause"]));
    await waitFor(() => expect(getCount).toBe(2));
    // POST is done but the refresh is still in flight: still disabled.
    expect(screen.getByRole("button", { name: "Pause alpha" })).toBeDisabled();
    releaseRefresh();
    const resume = await screen.findByRole("button", { name: "Resume alpha" });
    await waitFor(() => expect(resume).toBeEnabled());
  });

  it("shows the server error when an action fails and refreshes the list", async () => {
    const fetchMock = mockFetch(
      [agent("alpha", "running")],
      { alpha: "Running" },
      () => json({ error: "Could not freeze agent 'alpha': boom" }, false, 500),
    );
    render(<AgentsApp windowId="test" />);
    fireEvent.click(await screen.findByRole("button", { name: "Pause alpha" }));
    await waitFor(() => {
      const n = useNotificationStore.getState().notifications;
      expect(n.some((x) => x.title === "Pause failed" && x.body.includes("boom"))).toBe(true);
    });
    // The list is re-fetched after the action.
    await waitFor(() =>
      expect(fetchMock.mock.calls.filter(([u]) => u === "/api/agents").length).toBeGreaterThan(1),
    );
  });
});
