import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AgentContextMenu } from "../AgentContextMenu";

describe("AgentContextMenu", () => {
  beforeEach(() => {
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) }),
    ) as unknown as typeof fetch;
  });

  it("shows DM and framework items", () => {
    render(
      <AgentContextMenu slug="tom" channelId="c1" channelType="group"
        x={0} y={0} onClose={() => {}} />,
    );
    expect(screen.getByText("DM @tom")).toBeInTheDocument();
    expect(screen.getByText("Mute in this channel")).toBeInTheDocument();
    expect(screen.getByText("Remove from channel")).toBeInTheDocument();
    expect(screen.getByText("View agent info")).toBeInTheDocument();
    expect(screen.getByText("Jump to agent settings")).toBeInTheDocument();
  });

  it("hides mute and remove in DMs", () => {
    render(
      <AgentContextMenu slug="tom" channelId="c1" channelType="dm"
        x={0} y={0} onClose={() => {}} />,
    );
    expect(screen.queryByText("Mute in this channel")).not.toBeInTheDocument();
    expect(screen.queryByText("Remove from channel")).not.toBeInTheDocument();
  });

  it("shows 'Unmute' when isMuted is true", () => {
    render(
      <AgentContextMenu slug="tom" channelId="c1" channelType="group" isMuted
        x={0} y={0} onClose={() => {}} />,
    );
    expect(screen.getByText("Unmute in this channel")).toBeInTheDocument();
  });

  it("calls muteAgent then onClose when Mute is clicked", async () => {
    const onClose = vi.fn();
    render(
      <AgentContextMenu slug="tom" channelId="c1" channelType="group"
        x={0} y={0} onClose={onClose} />,
    );
    fireEvent.click(screen.getByText("Mute in this channel"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(fetch).toHaveBeenCalledWith(
      "/api/chat/channels/c1/muted",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(<AgentContextMenu slug="tom" x={0} y={0} onClose={onClose} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
