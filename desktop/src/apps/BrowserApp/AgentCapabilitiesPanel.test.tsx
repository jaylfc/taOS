import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { AgentCapabilitiesPanel } from "./AgentCapabilitiesPanel";
import * as capApi from "@/lib/browser-capability-api";
import * as agentApi from "@/lib/browser-agent-api";

vi.mock("@/lib/browser-capability-api");
vi.mock("@/lib/browser-agent-api");

const PROFILE_ID = "prof-1";

const makeGrant = (
  overrides: Partial<capApi.CapabilityGrant> = {},
): capApi.CapabilityGrant => ({
  agent_id: "agent-abc",
  host_pattern: "*.example.com",
  permissions: "read,navigate",
  granted_at: new Date(Date.now() - 60_000).toISOString(),
  expires_at: null,
  ...overrides,
});

beforeEach(() => {
  vi.mocked(capApi.listCapabilities).mockResolvedValue([]);
  vi.mocked(capApi.revokeCapability).mockResolvedValue(true);
  vi.mocked(agentApi.listAgents).mockResolvedValue([]);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("AgentCapabilitiesPanel", () => {
  it("shows loading state initially", async () => {
    // listCapabilities never resolves during this check
    let resolve!: (v: capApi.CapabilityGrant[]) => void;
    vi.mocked(capApi.listCapabilities).mockReturnValue(new Promise((r) => { resolve = r; }));

    render(<AgentCapabilitiesPanel profileId={PROFILE_ID} onClose={vi.fn()} />);
    expect(screen.getByText(/loading/i)).toBeTruthy();
    resolve([]);
  });

  it("renders table with one row per grant after load", async () => {
    vi.mocked(capApi.listCapabilities).mockResolvedValue([
      makeGrant({ agent_id: "a1", host_pattern: "foo.com" }),
      makeGrant({ agent_id: "a2", host_pattern: "bar.com" }),
    ]);

    render(<AgentCapabilitiesPanel profileId={PROFILE_ID} onClose={vi.fn()} />);
    await waitFor(() => screen.getByText("foo.com"));
    expect(screen.getByText("bar.com")).toBeTruthy();
  });

  it("empty state when no grants", async () => {
    vi.mocked(capApi.listCapabilities).mockResolvedValue([]);

    render(<AgentCapabilitiesPanel profileId={PROFILE_ID} onClose={vi.fn()} />);
    await waitFor(() => screen.getByText(/no agent capabilities granted yet/i));
  });

  it("uses agent name from listAgents when available", async () => {
    vi.mocked(capApi.listCapabilities).mockResolvedValue([
      makeGrant({ agent_id: "agent-abc" }),
    ]);
    vi.mocked(agentApi.listAgents).mockResolvedValue([
      { id: "agent-abc", name: "My Cool Agent" },
    ]);

    render(<AgentCapabilitiesPanel profileId={PROFILE_ID} onClose={vi.fn()} />);
    await waitFor(() => screen.getByText("My Cool Agent"));
  });

  it("falls back to agent_id when agent name not found", async () => {
    vi.mocked(capApi.listCapabilities).mockResolvedValue([
      makeGrant({ agent_id: "unknown-agent" }),
    ]);
    vi.mocked(agentApi.listAgents).mockResolvedValue([]);

    render(<AgentCapabilitiesPanel profileId={PROFILE_ID} onClose={vi.fn()} />);
    await waitFor(() => screen.getByText("unknown-agent"));
  });

  it("formats expires_at as human-readable (In Xh / In X days)", async () => {
    // Use 5 days (120h) so Math.floor(hours/24) is reliably >= 4 even with test overhead
    const inFiveDays = new Date(Date.now() + 5 * 24 * 60 * 60 * 1000).toISOString();
    vi.mocked(capApi.listCapabilities).mockResolvedValueOnce([
      makeGrant({ expires_at: inFiveDays }),
    ]);

    render(<AgentCapabilitiesPanel profileId={PROFILE_ID} onClose={vi.fn()} />);
    await waitFor(() => screen.getByText(/In [45] days/i));
  });

  it("expires_at null shows 'Never'", async () => {
    vi.mocked(capApi.listCapabilities).mockResolvedValue([
      makeGrant({ expires_at: null }),
    ]);

    render(<AgentCapabilitiesPanel profileId={PROFILE_ID} onClose={vi.fn()} />);
    await waitFor(() => screen.getByText("Never"));
  });

  it("revoke click calls revokeCapability and refreshes list", async () => {
    vi.mocked(capApi.listCapabilities).mockResolvedValue([
      makeGrant({ agent_id: "agent-abc", host_pattern: "foo.com" }),
    ]);
    vi.mocked(capApi.revokeCapability).mockResolvedValue(true);
    // After revoke, list returns empty
    vi.mocked(capApi.listCapabilities)
      .mockResolvedValueOnce([makeGrant({ agent_id: "agent-abc", host_pattern: "foo.com" })])
      .mockResolvedValueOnce([]);

    render(<AgentCapabilitiesPanel profileId={PROFILE_ID} onClose={vi.fn()} />);
    await waitFor(() => screen.getByText("foo.com"));

    const revokeBtn = screen.getByRole("button", { name: /revoke/i });
    await act(async () => {
      fireEvent.click(revokeBtn);
    });

    expect(capApi.revokeCapability).toHaveBeenCalledWith(PROFILE_ID, "agent-abc", "foo.com");
    await waitFor(() => screen.getByText(/no agent capabilities granted yet/i));
  });

  it("revoke failure shows inline error", async () => {
    vi.mocked(capApi.listCapabilities).mockResolvedValue([
      makeGrant({ agent_id: "agent-abc", host_pattern: "foo.com" }),
    ]);
    vi.mocked(capApi.revokeCapability).mockResolvedValue(false);

    render(<AgentCapabilitiesPanel profileId={PROFILE_ID} onClose={vi.fn()} />);
    await waitFor(() => screen.getByText("foo.com"));

    const revokeBtn = screen.getByRole("button", { name: /revoke/i });
    await act(async () => {
      fireEvent.click(revokeBtn);
    });

    await waitFor(() => screen.getByText(/failed to revoke/i));
  });

  it("Esc closes via onClose", async () => {
    vi.mocked(capApi.listCapabilities).mockResolvedValue([]);
    const onClose = vi.fn();

    render(<AgentCapabilitiesPanel profileId={PROFILE_ID} onClose={onClose} />);
    await waitFor(() => screen.getByRole("dialog"));

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("backdrop click closes via onClose", async () => {
    vi.mocked(capApi.listCapabilities).mockResolvedValue([]);
    const onClose = vi.fn();

    render(<AgentCapabilitiesPanel profileId={PROFILE_ID} onClose={onClose} />);
    const backdrop = await waitFor(() => screen.getByRole("dialog").parentElement!);

    fireEvent.click(backdrop);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("aria-modal + role=dialog present", async () => {
    vi.mocked(capApi.listCapabilities).mockResolvedValue([]);

    render(<AgentCapabilitiesPanel profileId={PROFILE_ID} onClose={vi.fn()} />);
    const dialog = await waitFor(() => screen.getByRole("dialog"));
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-label")).toMatch(/agent capabilities/i);
  });
});
