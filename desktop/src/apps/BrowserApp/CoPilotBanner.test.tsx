import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CoPilotBanner } from "./CoPilotBanner";
import { useBrowserAgentStore } from "@/stores/browser-agent-store";
import * as capabilityApi from "@/lib/browser-capability-api";
import * as agentApi from "@/lib/browser-agent-api";

const WINDOW_ID = "win-1";
const TAB_ID = "tab-1";
const PROFILE_ID = "personal";
const AGENT_ID = "agent-x";

// Mock the browser-store module (imported dynamically inside handleTakeBack)
vi.mock("@/stores/browser-store", () => ({
  useBrowserStore: {
    getState: vi.fn(() => ({
      removePinnedAgent: vi.fn(),
    })),
  },
}));

beforeEach(() => {
  useBrowserAgentStore.setState({
    panels: {},
    lastEventAt: {},
    messages: {},
    recentEvents: {},
    annotations: {},
    drivingState: {
      [`${WINDOW_ID}:${TAB_ID}:${AGENT_ID}`]: "driving",
    },
  });

  vi.spyOn(capabilityApi, "revokeCapability").mockResolvedValue(true);
  vi.spyOn(agentApi, "unpinAgent").mockResolvedValue(true);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("CoPilotBanner", () => {
  it("renders the agent name + co-piloting message", () => {
    render(
      <CoPilotBanner
        windowId={WINDOW_ID}
        tabId={TAB_ID}
        profileId={PROFILE_ID}
        agentId={AGENT_ID}
        agentName="Aria"
      />,
    );
    expect(screen.getByText(/co-piloting this tab/i)).toBeTruthy();
    expect(screen.getByText("Aria")).toBeTruthy();
  });

  it("agent name falls back to agentId when name not provided", () => {
    render(
      <CoPilotBanner
        windowId={WINDOW_ID}
        tabId={TAB_ID}
        profileId={PROFILE_ID}
        agentId={AGENT_ID}
      />,
    );
    expect(screen.getByText(AGENT_ID)).toBeTruthy();
  });

  it("Pause click flips drivingState to idle", () => {
    render(
      <CoPilotBanner
        windowId={WINDOW_ID}
        tabId={TAB_ID}
        profileId={PROFILE_ID}
        agentId={AGENT_ID}
        agentName="Aria"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /pause/i }));

    const state = useBrowserAgentStore.getState().drivingState[`${WINDOW_ID}:${TAB_ID}:${AGENT_ID}`];
    expect(state).toBe("idle");
  });

  it("Take back click calls revokeCapability + unpinAgent", async () => {
    render(
      <CoPilotBanner
        windowId={WINDOW_ID}
        tabId={TAB_ID}
        profileId={PROFILE_ID}
        agentId={AGENT_ID}
        agentName="Aria"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /take back/i }));

    await waitFor(() => {
      expect(capabilityApi.revokeCapability).toHaveBeenCalledWith(PROFILE_ID, AGENT_ID, "*");
      expect(agentApi.unpinAgent).toHaveBeenCalledWith(PROFILE_ID, TAB_ID, AGENT_ID);
    });
  });

  it("Take back click flips drivingState to idle", async () => {
    render(
      <CoPilotBanner
        windowId={WINDOW_ID}
        tabId={TAB_ID}
        profileId={PROFILE_ID}
        agentId={AGENT_ID}
        agentName="Aria"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /take back/i }));

    await waitFor(() => {
      const state = useBrowserAgentStore.getState().drivingState[`${WINDOW_ID}:${TAB_ID}:${AGENT_ID}`];
      expect(state).toBe("idle");
    });
  });

  it("Take back click calls onTakeBack callback if supplied", async () => {
    const onTakeBack = vi.fn();
    render(
      <CoPilotBanner
        windowId={WINDOW_ID}
        tabId={TAB_ID}
        profileId={PROFILE_ID}
        agentId={AGENT_ID}
        agentName="Aria"
        onTakeBack={onTakeBack}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /take back/i }));

    await waitFor(() => {
      expect(onTakeBack).toHaveBeenCalledTimes(1);
    });
  });

  it("aria-label on Pause button", () => {
    render(
      <CoPilotBanner
        windowId={WINDOW_ID}
        tabId={TAB_ID}
        profileId={PROFILE_ID}
        agentId={AGENT_ID}
      />,
    );
    const pauseBtn = screen.getByRole("button", { name: /pause/i });
    expect(pauseBtn.getAttribute("aria-label")).toBeTruthy();
  });

  it("aria-label on Take back button", () => {
    render(
      <CoPilotBanner
        windowId={WINDOW_ID}
        tabId={TAB_ID}
        profileId={PROFILE_ID}
        agentId={AGENT_ID}
      />,
    );
    const takeBackBtn = screen.getByRole("button", { name: /take back/i });
    expect(takeBackBtn.getAttribute("aria-label")).toBeTruthy();
  });
});
