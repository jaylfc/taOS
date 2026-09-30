import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { AgentRow } from "./AgentRow";
import * as useIsMobileModule from "@/hooks/use-is-mobile";
import { type Agent } from "./types";

vi.mock("@/hooks/use-is-mobile");

const baseAgent: Agent = {
  name: "scout",
  display_name: "Scout",
  host: "localhost",
  color: "#3b82f6",
  emoji: "🤖",
  status: "running",
  vectors: 1234,
  framework: "openclaw",
  paused: false,
};

function renderRow(overrides: Partial<Agent> = {}, props: Partial<Parameters<typeof AgentRow>[0]> = {}) {
  return render(
    <AgentRow
      agent={{ ...baseAgent, ...overrides }}
      diskState={null}
      latestByFramework={{}}
      onViewLogs={vi.fn()}
      onViewSkills={vi.fn()}
      onViewMessages={vi.fn()}
      onDelete={vi.fn()}
      onResume={vi.fn()}
      {...props}
    />,
  );
}

describe("AgentRow", () => {
  beforeEach(() => {
    vi.mocked(useIsMobileModule.useIsMobile).mockReturnValue(false);
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows the agent name and a running status", () => {
    renderRow();
    expect(screen.getByText("Scout")).toBeInTheDocument();
    expect(screen.getByLabelText("Status: Running")).toBeInTheDocument();
  });

  it("exposes management actions with aria-labels while running", () => {
    renderRow();
    expect(screen.getByRole("button", { name: "View logs for scout" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Manage skills for scout" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "View messages for scout" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Delete scout" })).toBeInTheDocument();
  });

  it("disables management actions when the agent is not running", () => {
    renderRow({ status: "stopped" });
    expect(
      screen.getByRole("button", { name: "View logs for scout (Agent is not running)" }),
    ).toBeDisabled();
    expect(screen.getByLabelText("Status: Stopped")).toBeInTheDocument();
  });

  it("hides destructive actions when protected", () => {
    renderRow({}, { protected: true });
    expect(screen.queryByRole("button", { name: "Delete scout" })).toBeNull();
  });

  it("shows a resume action and Paused status when paused", () => {
    renderRow({ paused: true });
    expect(screen.getByRole("button", { name: "Resume scout" })).toBeInTheDocument();
    expect(screen.getByLabelText("Status: Paused")).toBeInTheDocument();
  });

  it("surfaces the framework-update indicator", () => {
    render(
      <AgentRow
        agent={{ ...baseAgent, framework_version_sha: "old" }}
        diskState={null}
        latestByFramework={{ openclaw: { tag: "v2", sha: "new" } }}
        onViewLogs={vi.fn()}
        onViewSkills={vi.fn()}
        onViewMessages={vi.fn()}
        onDelete={vi.fn()}
        onResume={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("framework update available")).toBeInTheDocument();
  });
});

describe("AgentRow lifecycle controls", () => {
  const handlers = () => ({
    onStart: vi.fn(),
    onStop: vi.fn(),
    onRestart: vi.fn(),
    onPause: vi.fn(),
    onResume: vi.fn(),
  });

  beforeEach(() => {
    vi.mocked(useIsMobileModule.useIsMobile).mockReturnValue(false);
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("running agent: Stop, Restart and Pause, no Start or Resume", () => {
    renderRow({ status: "running" }, handlers());
    expect(screen.getByRole("button", { name: "Stop scout" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Restart scout" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Pause scout" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Start scout" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Resume scout" })).toBeNull();
  });

  it("stopped agent: only Start, even with a stale paused flag", () => {
    renderRow({ status: "stopped", paused: true }, handlers());
    expect(screen.getByRole("button", { name: "Start scout" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Stop scout" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Restart scout" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Pause scout" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Resume scout" })).toBeNull();
    expect(screen.getByLabelText("Status: Stopped")).toBeInTheDocument();
  });

  it("frozen agent: Resume only, reads as Paused", () => {
    renderRow({ status: "running", frozen: true }, handlers());
    expect(screen.getByRole("button", { name: "Resume scout" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Start scout" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Stop scout" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Pause scout" })).toBeNull();
    expect(screen.getByLabelText("Status: Paused")).toBeInTheDocument();
  });

  it("every lifecycle button has an aria-label and a title", () => {
    renderRow({ status: "running" }, handlers());
    for (const name of ["Stop scout", "Restart scout", "Pause scout"]) {
      expect(screen.getByRole("button", { name }).getAttribute("title")).toBeTruthy();
    }
  });

  it("each button calls its own handler", () => {
    const h = handlers();
    const { unmount } = renderRow({ status: "running" }, h);
    fireEvent.click(screen.getByRole("button", { name: "Stop scout" }));
    fireEvent.click(screen.getByRole("button", { name: "Restart scout" }));
    fireEvent.click(screen.getByRole("button", { name: "Pause scout" }));
    expect(h.onStop).toHaveBeenCalledWith("scout");
    expect(h.onRestart).toHaveBeenCalledWith("scout");
    expect(h.onPause).toHaveBeenCalledWith("scout");
    unmount();
    renderRow({ status: "stopped" }, h);
    fireEvent.click(screen.getByRole("button", { name: "Start scout" }));
    expect(h.onStart).toHaveBeenCalledWith("scout");
  });

  it("disables lifecycle buttons while a request is in flight", () => {
    renderRow({ status: "running" }, { ...handlers(), busy: true });
    expect(screen.getByRole("button", { name: "Stop scout" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Pause scout" })).toBeDisabled();
  });

  it("protected agents get no lifecycle controls", () => {
    renderRow({ status: "running" }, { ...handlers(), protected: true });
    expect(screen.queryByRole("button", { name: "Stop scout" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Pause scout" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Restart scout" })).toBeNull();
  });
});
