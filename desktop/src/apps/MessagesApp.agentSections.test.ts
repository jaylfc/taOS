import { describe, it, expect } from "vitest";
import {
  bucketAgentChannels,
  buildAgentPresence,
  collectBoundChannels,
  computeAgentPresence,
  type AgentSectionChannel,
  type AgentSectionLiveAgent,
  type AgentSectionArchivedAgent,
  type AgentPresence,
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

describe("computeAgentPresence", () => {
  it("returns 'working' when the agent is working regardless of status", () => {
    expect(computeAgentPresence("running", true)).toBe("working");
    expect(computeAgentPresence("paused", true)).toBe("working");
    expect(computeAgentPresence("stopped", true)).toBe("working");
    expect(computeAgentPresence(undefined, true)).toBe("working");
  });

  it("returns 'live' when the agent is running and not working", () => {
    expect(computeAgentPresence("running", false)).toBe("live");
  });

  it("returns 'idle' when the agent is paused and not working", () => {
    expect(computeAgentPresence("paused", false)).toBe("idle");
  });

  it("returns 'idle' when the agent is stopped and not working", () => {
    expect(computeAgentPresence("stopped", false)).toBe("idle");
  });

  it("returns 'idle' when the agent status is undefined and not working", () => {
    expect(computeAgentPresence(undefined, false)).toBe("idle");
  });

  it("returns 'working' takes precedence over 'live'", () => {
    expect(computeAgentPresence("running", true)).toBe("working");
  });
});

describe("buildAgentPresence", () => {
  const ch = (id: string, agent?: string) => ({
    id,
    settings: agent ? { taostalk_agent: agent } : undefined,
  });
  const noDm = { live: [], suspended: [], archived: [] };

  it("marks a live agent DM 'working' while its agent is thinking", () => {
    const presence = buildAgentPresence(
      { live: [ch("c1", "hermes")], suspended: [], archived: [] },
      [],
      new Set(["hermes"]),
    );
    expect(presence).toEqual({ c1: "working" });
  });

  it("marks a live agent DM 'live' when its agent is not thinking", () => {
    const presence = buildAgentPresence(
      { live: [ch("c1", "hermes")], suspended: [], archived: [] },
      [],
      new Set(),
    );
    expect(presence).toEqual({ c1: "live" });
  });

  it("marks suspended and archived agent DMs 'idle'", () => {
    const presence = buildAgentPresence(
      { live: [], suspended: [ch("c2", "scout")], archived: [ch("c3")] },
      [],
      new Set(["scout"]),
    );
    expect(presence).toEqual({ c2: "idle", c3: "idle" });
  });

  it("marks a bound topic/group channel 'working' while its agent is thinking", () => {
    const presence = buildAgentPresence(noDm, [ch("t1", "hermes")], new Set(["hermes"]));
    expect(presence).toEqual({ t1: "working" });
  });

  it("gives bound-but-idle and unbound topic/group channels no entry", () => {
    const presence = buildAgentPresence(
      noDm,
      [ch("t1", "hermes"), ch("t2")],
      new Set(),
    );
    expect(presence).toEqual({});
  });

  describe("MessagesApp presence assembly (regression test for standalone project channels)", () => {
    const ch = (id: string, agent?: string) => ({
      id,
      settings: agent ? { taostalk_agent: agent } : undefined,
    });

    it("bound project channel gets presence when its agent is thinking", () => {
      const projectGroups = [
        { channels: [ch("p1", "hermes")] },
        { channels: [ch("p2")], },
      ];
      const dmSections = { live: [], suspended: [], archived: [] };
      const grouped = { topic: [], group: [] };
      const workingSlugs = new Set(["hermes"]);
      const agentPresence = buildAgentPresence(
        dmSections,
        collectBoundChannels(grouped, projectGroups),
        workingSlugs,
      );
      expect(agentPresence).toEqual({ p1: "working" });
    });

    it("bound project channel gets no presence when its agent is not thinking", () => {
      const projectGroups = [
        { channels: [ch("p1", "hermes")] },
      ];
      const dmSections = { live: [], suspended: [], archived: [] };
      const grouped = { topic: [], group: [] };
      const workingSlugs = new Set();
      const agentPresence = buildAgentPresence(
        dmSections,
        collectBoundChannels(grouped, projectGroups),
        workingSlugs,
      );
      expect(agentPresence).toEqual({});
    });
  });
});
