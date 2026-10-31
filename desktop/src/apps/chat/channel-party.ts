import type { Channel, LiveAgent } from "./types";

export interface ChannelParty {
  /** Display name for the header / composer. */
  title: string;
  /** "group" for multi-member channels, "agent" for a DM with a known agent, else "person". */
  kind: "group" | "agent" | "person";
  /** Muted line under the title (member count, or where an agent runs). */
  subtitle: string;
  /** The agent for an agent DM, when known. */
  agent?: LiveAgent;
}

/**
 * Derive the mobile title / subtitle for a channel. Only reads data the
 * channel and the live agent roster really expose; nothing is invented.
 * `where` selects what the subtitle says for an agent DM: the framework and
 * model it runs on (composer) or just its kind (header).
 */
export function describeChannelParty(
  channel: Channel | undefined,
  liveAgents: LiveAgent[] = [],
  where: "runtime" | "kind" = "runtime",
): ChannelParty {
  const name = channel?.name ?? "";
  if (channel?.type === "dm") {
    const other = (channel.members ?? []).find((m) => m !== "user") ?? name;
    const agent = liveAgents.find((a) => a.name === other);
    if (agent) {
      const runtime = [agent.framework, agent.model].filter(Boolean).join(" \u00b7 ");
      return {
        title: agent.display_name || agent.name,
        kind: "agent",
        subtitle: where === "runtime" && runtime ? runtime : "Agent",
        agent,
      };
    }
    return { title: other || name, kind: "person", subtitle: "" };
  }
  const n = channel?.members?.length ?? 0;
  return {
    title: name,
    kind: "group",
    // The member count wins; the topic is only a fallback when there is none.
    subtitle:
      n > 0
        ? `${n} ${n === 1 ? "person" : "people"}`
        : (channel?.topic || channel?.description || "").trim(),
  };
}
