/**
 * Unit tests for TaosAgentShellTab: the taOS Agent's shell allow/deny
 * profile, viewable and revokable from the agent's settings.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import React from "react";

vi.mock("@/lib/taos-agent-shell-api", () => ({
  fetchShellProfile: vi.fn(),
  setShellRule: vi.fn(),
  revokeShellRule: vi.fn(),
}));

import { fetchShellProfile, revokeShellRule, setShellRule } from "@/lib/taos-agent-shell-api";
import { TaosAgentShellTab } from "../agents/TaosAgentShellTab";

const PROFILE = { allow: ["ls", "npm test"], deny: ["curl"], asked: { "npm run": "dec-1" } };

describe("<TaosAgentShellTab />", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchShellProfile).mockResolvedValue({ ...PROFILE });
  });

  it("lists allowed, denied and waiting commands", async () => {
    render(<TaosAgentShellTab />);
    await waitFor(() => expect(screen.getByLabelText("Allowed commands")).toBeInTheDocument());
    expect(screen.getByLabelText("Allowed commands")).toHaveTextContent("npm test");
    expect(screen.getByLabelText("Denied commands")).toHaveTextContent("curl");
    expect(screen.getByLabelText("Waiting for your answer")).toHaveTextContent("npm run");
  });

  it("revokes a rule", async () => {
    vi.mocked(revokeShellRule).mockResolvedValue({ allow: ["ls"], deny: ["curl"], asked: {} });
    render(<TaosAgentShellTab />);
    await waitFor(() => screen.getByLabelText("Revoke npm test"));
    fireEvent.click(screen.getByLabelText("Revoke npm test"));
    await waitFor(() => expect(revokeShellRule).toHaveBeenCalledWith("npm test"));
    await waitFor(() =>
      expect(screen.getByLabelText("Allowed commands")).not.toHaveTextContent("npm test"),
    );
  });

  it("flips a rule between allow and deny", async () => {
    vi.mocked(setShellRule).mockResolvedValue({ allow: ["ls", "npm test", "curl"], deny: [], asked: {} });
    render(<TaosAgentShellTab />);
    await waitFor(() => screen.getByLabelText("Allow curl"));
    fireEvent.click(screen.getByLabelText("Allow curl"));
    await waitFor(() => expect(setShellRule).toHaveBeenCalledWith("curl", "allow"));
    await waitFor(() => expect(screen.getByLabelText("Allowed commands")).toHaveTextContent("curl"));
  });

  it("shows a load error", async () => {
    vi.mocked(fetchShellProfile).mockRejectedValue(new Error("boom"));
    render(<TaosAgentShellTab />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("boom"));
  });
});
