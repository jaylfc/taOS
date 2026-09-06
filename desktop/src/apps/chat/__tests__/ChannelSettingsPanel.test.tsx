import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChannelSettingsPanel } from "../ChannelSettingsPanel";

describe("ChannelSettingsPanel", () => {
  const channel = {
    id: "c1",
    name: "roundtable",
    type: "group" as const,
    topic: "The arena",
    members: ["user", "tom", "don"],
    settings: { response_mode: "quiet" as const, max_hops: 3, cooldown_seconds: 5, muted: [] as string[] },
  };
  const knownAgents = [{ name: "tom" }, { name: "don" }, { name: "linus" }];

  it("renders the four sections with correct header labels", () => {
    render(<ChannelSettingsPanel channel={channel} knownAgents={knownAgents}
             onClose={vi.fn()} onChanged={vi.fn()} />);
    expect(screen.getByRole("heading", { name: /Channel settings/ })).toBeInTheDocument();
    expect(screen.getByText("Overview")).toBeInTheDocument();
    expect(screen.getByText("Members")).toBeInTheDocument();
    expect(screen.getByText("Moderation")).toBeInTheDocument();
    expect(screen.getByText("Advanced")).toBeInTheDocument();
  });

  it("populates inputs from the channel prop", () => {
    render(<ChannelSettingsPanel channel={channel} knownAgents={knownAgents}
             onClose={vi.fn()} onChanged={vi.fn()} />);
    expect((screen.getByDisplayValue("roundtable"))).toBeInTheDocument();
    expect(screen.getByDisplayValue("The arena")).toBeInTheDocument();
  });
});
