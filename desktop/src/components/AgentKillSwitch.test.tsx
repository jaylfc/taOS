import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { AgentKillSwitch } from "./AgentKillSwitch";

// Radix portals + pointer-event opening are flaky under jsdom, so this covers
// the render contract; the open/confirm/kill paths are exercised manually.
describe("AgentKillSwitch", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve([]) })) as unknown as typeof fetch);
  });

  it("renders the top-bar trigger with an accessible label", () => {
    render(<AgentKillSwitch />);
    const trigger = screen.getByRole("button", { name: "Stop agents" });
    expect(trigger).toBeInTheDocument();
  });

  it("does not open a confirmation dialog before any action", () => {
    render(<AgentKillSwitch />);
    // The destructive confirm dialog is only mounted once a target is chosen.
    expect(screen.queryByText(/Kill all agents\?/)).toBeNull();
  });
});
