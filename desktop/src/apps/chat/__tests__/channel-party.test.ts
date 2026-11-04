import { describe, it, expect } from "vitest";
import { describeChannelParty } from "../channel-party";
import type { Channel, LiveAgent, ArchivedAgentEntry } from "../types";

describe("describeChannelParty", () => {
  it("dm-remote channel members ['user','hub:peer'] gives kind person and subtitle ''", () => {
    const channel: Channel = {
      id: "ch-1",
      name: "hub:peer",
      type: "dm-remote",
      members: ["user", "hub:peer"],
    };
    const result = describeChannelParty(channel, [], "kind", []);
    expect(result.kind).toBe("person");
    expect(result.subtitle).toBe("");
  });

  it("dm with members ['user','old-bot'], no live agents, archivedAgents gives title 'Old Bot' and kind agent", () => {
    const channel: Channel = {
      id: "ch-2",
      name: "old-bot",
      type: "dm",
      members: ["user", "old-bot"],
    };
    const archivedAgents: ArchivedAgentEntry[] = [
      {
        id: "a1",
        archived_slug: "old-bot-x",
        original: { name: "old-bot", display_name: "Old Bot" },
      },
    ];
    const result = describeChannelParty(channel, [], "kind", archivedAgents);
    expect(result.title).toBe("Old Bot");
    expect(result.kind).toBe("agent");
  });

  it("a group of 3 members gives subtitle '3 members'", () => {
    const channel: Channel = {
      id: "ch-3",
      name: "design",
      type: "group",
      members: ["user", "tom", "lin"],
    };
    const result = describeChannelParty(channel, [], "kind", []);
    expect(result.subtitle).toBe("3 members");
  });

  it("no members, topic '   ', description 'Desc' gives 'Desc'", () => {
    const channel: Channel = {
      id: "ch-4",
      name: "empty",
      type: "group",
      members: [],
      topic: "   ",
      description: "Desc",
    };
    const result = describeChannelParty(channel, [], "kind", []);
    expect(result.subtitle).toBe("Desc");
  });
});