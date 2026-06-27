import { describe, it, expect } from "vitest";
import {
  bucketAgentChannels,
  type AgentSectionChannel,
  type AgentSectionLiveAgent,
  type AgentSectionArchivedAgent,
} from "./MessagesApp.agentSections";

const dm = (
  name: string,
  extra: Partial<AgentSectionChannel> = {},
): AgentSectionChannel => ({
  name,
  members: ["user", name],
  ...extra,
});

describe("bucketAgentChannels", () => {
  it("puts running agents under Live", () => {
    const channels = [dm("hermes")];
    const live: AgentSectionLiveAgent[] = [{ name: "hermes", status: "running" }];
    const result = bucketAgentChannels(channels, live, []);
    expect(result.live.map((c) => c.name)).toEqual(["hermes"]);
    expect(result.suspended).toEqual([]);
    expect(result.archived).toEqual([]);
  });

  it("puts paused agents under Suspended", () => {
    const channels = [dm("scout")];
    const live: AgentSectionLiveAgent[] = [{ name: "scout", status: "paused" }];
    const result = bucketAgentChannels(channels, live, []);
    expect(result.suspended.map((c) => c.name)).toEqual(["scout"]);
    expect(result.live).toEqual([]);
    expect(result.archived).toEqual([]);
  });

  it("puts archived-list agents under Archived", () => {
    const channels = [dm("oldbot")];
    const archived: AgentSectionArchivedAgent[] = [{ archived_slug: "oldbot" }];
    const result = bucketAgentChannels(channels, [], archived);
    expect(result.archived.map((c) => c.name)).toEqual(["oldbot"]);
    expect(result.live).toEqual([]);
    expect(result.suspended).toEqual([]);
  });

  it("puts an orphaned DM (matches no loaded agent in either list) under Archived", () => {
    // This is the deleted-agent case: the agent is gone from /api/agents and
    // /api/agents/archived, but its DM channel lingers. With a known agent
    // present (lists loaded), the unmatched DM is a real orphan and must never
    // appear as Live.
    const channels = [dm("ghost")];
    const live: AgentSectionLiveAgent[] = [{ name: "hermes", status: "running" }];
    const result = bucketAgentChannels(channels, live, []);
    expect(result.archived.map((c) => c.name)).toEqual(["ghost"]);
    expect(result.live.map((c) => c.name)).toEqual([]);
    expect(result.suspended).toEqual([]);
    expect(result.nonAgent).toEqual([]);
  });

  it("does NOT archive agent DMs while both lists are empty (not yet loaded)", () => {
    // On first render /api/agents and /api/agents/archived have not resolved
    // yet, so every agent DM matches nothing. These are almost certainly live
    // agents and must not be dumped into Archived until the lists load -- they
    // stay in their existing (nonAgent) Direct Messages placement.
    const channels = [dm("hermes"), dm("scout")];
    const result = bucketAgentChannels(channels, [], []);
    expect(result.archived).toEqual([]);
    expect(result.live).toEqual([]);
    expect(result.nonAgent.map((c) => c.name)).toEqual(["hermes", "scout"]);
  });

  it("treats a non-running, non-paused live status (e.g. failed) as Archived", () => {
    const channels = [dm("brokenbot")];
    const live: AgentSectionLiveAgent[] = [{ name: "brokenbot", status: "failed" }];
    const result = bucketAgentChannels(channels, live, []);
    expect(result.archived.map((c) => c.name)).toEqual(["brokenbot"]);
    expect(result.live).toEqual([]);
  });

  it("matches a channel against an agent by display name", () => {
    // Channel names use the agent display_name while /api/agents reports a slug.
    const channels = [dm("Hermes Confirm", { members: ["user", "hermes-confirm"] })];
    const live: AgentSectionLiveAgent[] = [
      { name: "hermes-confirm", display_name: "Hermes Confirm", status: "running" },
    ];
    const result = bucketAgentChannels(channels, live, []);
    expect(result.live.map((c) => c.name)).toEqual(["Hermes Confirm"]);
  });

  it("matches a channel against an agent by its non-user member", () => {
    const channels = [dm("Display Only", { members: ["user", "agent-slug"] })];
    const live: AgentSectionLiveAgent[] = [{ name: "agent-slug", status: "running" }];
    const result = bucketAgentChannels(channels, live, []);
    expect(result.live.map((c) => c.name)).toEqual(["Display Only"]);
  });

  it("keeps a2a coordination channels under nonAgent", () => {
    const channels = [dm("coord", { settings: { kind: "a2a" } })];
    const result = bucketAgentChannels(channels, [], []);
    expect(result.nonAgent.map((c) => c.name)).toEqual(["coord"]);
    expect(result.archived).toEqual([]);
  });

  it("keeps a plain user DM (no agent member) under nonAgent", () => {
    const channels = [{ name: "alice", members: ["user"] }];
    const result = bucketAgentChannels(channels, [], []);
    expect(result.nonAgent.map((c) => c.name)).toEqual(["alice"]);
    expect(result.archived).toEqual([]);
  });

  it("buckets a mixed list across all sections", () => {
    const channels = [
      dm("live-one"),
      dm("paused-one"),
      dm("archived-one"),
      dm("orphan-one"),
      dm("coord", { settings: { kind: "a2a" } }),
    ];
    const live: AgentSectionLiveAgent[] = [
      { name: "live-one", status: "running" },
      { name: "paused-one", status: "paused" },
    ];
    const archived: AgentSectionArchivedAgent[] = [{ archived_slug: "archived-one" }];
    const result = bucketAgentChannels(channels, live, archived);
    expect(result.live.map((c) => c.name)).toEqual(["live-one"]);
    expect(result.suspended.map((c) => c.name)).toEqual(["paused-one"]);
    expect(result.archived.map((c) => c.name).sort()).toEqual(["archived-one", "orphan-one"]);
    expect(result.nonAgent.map((c) => c.name)).toEqual(["coord"]);
  });
});
