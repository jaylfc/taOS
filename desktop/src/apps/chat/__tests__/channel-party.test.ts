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

  it("dm matches archived agent by archived_agent_id even when the member name differs", () => {
    const channel: Channel = {
      id: "ch-5",
      name: "old-bot",
      type: "dm",
      members: ["user", "old-bot"],
      settings: { archived_agent_id: "a1" },
    };
    const archivedAgents: ArchivedAgentEntry[] = [
      {
        id: "a1",
        archived_slug: "different-slug",
        original: { name: "Different Name", display_name: "Different" },
      },
    ];
    const result = describeChannelParty(channel, [], "kind", archivedAgents);
    expect(result.kind).toBe("agent");
    expect(result.title).toBe("Different");
  });

  it("dm falls back to slug/name match when there is no archived_agent_id", () => {
    const channel: Channel = {
      id: "ch-6",
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
    expect(result.kind).toBe("agent");
    expect(result.title).toBe("Old Bot");
  });

  it("archived dm with live agent reusing slug resolves to archived agent when settings.archived is true", () => {
    const channel: Channel = {
      id: "ch-7",
      name: "old-bot",
      type: "dm",
      members: ["user", "bob"],
      settings: { archived: true, archived_agent_id: "a1" },
    };
    const liveAgents: LiveAgent[] = [{ name: "bob", display_name: "Bob" }];
    const archivedAgents: ArchivedAgentEntry[] = [
      {
        id: "a1",
        archived_slug: "bob-old",
        original: { display_name: "Old Bob" },
      },
    ];
    const result = describeChannelParty(channel, liveAgents, "kind", archivedAgents);
    expect(result.title).toBe("Old Bob");
    expect(result.subtitle).toBe("Archived agent");
  });

  it("restored dm (settings.archived false) with live agent reusing slug shows the live agent", () => {
    const channel: Channel = {
      id: "ch-8",
      name: "old-bot",
      type: "dm",
      members: ["user", "bob"],
      settings: { archived: false, archived_agent_id: "a1" },
    };
    const liveAgents: LiveAgent[] = [{ name: "bob", display_name: "Bob" }];
    const archivedAgents: ArchivedAgentEntry[] = [
      {
        id: "a1",
        archived_slug: "bob-old",
        original: { display_name: "Old Bob" },
      },
    ];
    const result = describeChannelParty(channel, liveAgents, "kind", archivedAgents);
    expect(result.title).toBe("Bob");
    expect(result.subtitle).not.toBe("Archived agent");
  });

  it("archived dm with unmatched archived_agent_id shows an archived placeholder, not a live agent reusing the slug", () => {
    const channel: Channel = {
      id: "ch-9",
      name: "old-bot",
      type: "dm",
      members: ["user", "bob"],
      settings: { archived: true, archived_agent_id: "a1" },
    };
    const liveAgents: LiveAgent[] = [{ name: "bob", display_name: "Bob" }];
    const result = describeChannelParty(channel, liveAgents, "kind", []);
    expect(result.kind).toBe("agent");
    expect(result.title).toBe("bob");
    expect(result.subtitle).toBe("Archived agent");
    expect(result.agent).toBeUndefined();
  });
});