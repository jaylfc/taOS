/**
 * Lifecycle bucketing for agent DM channels in the Messages app.
 *
 * Agent DM channels are matched to an agent by name: the channel `name`
 * (or its non-"user" member) equals the agent's name or display name. Each
 * agent DM is sorted into one of three lifecycle buckets so live,
 * suspended, and archived/deleted agents are visually separated in the
 * channel list:
 *
 *   - "live"      -- agent present in /api/agents with status "running"
 *   - "suspended" -- agent present in /api/agents with status "paused"
 *   - "archived"  -- agent present in /api/agents/archived, OR an agent DM
 *                   channel that matches no agent in either list (an
 *                   orphaned / deleted-agent channel). Any other non-running,
 *                   non-paused live status (e.g. "failed") also falls here.
 *
 * A DM channel is treated as an "agent DM" when it has a non-"user" member
 * (the agent), or its kind marks it as one. Plain user-to-user DMs (no
 * agent member) and a2a coordination channels are returned untouched under
 * `nonAgent` and keep their existing placement.
 *
 * Pure function -- no side effects -- so it can be unit tested in isolation.
 */

export interface AgentSectionChannel {
  name: string;
  members?: string[];
  settings?: { kind?: string };
}

export interface AgentSectionLiveAgent {
  name: string;
  display_name?: string;
  status?: string;
}

export interface AgentSectionArchivedAgent {
  archived_slug?: string;
  original?: { name?: string; display_name?: string };
}

export interface AgentSections<C> {
  live: C[];
  suspended: C[];
  archived: C[];
  nonAgent: C[];
}

/**
 * Agent presence states for sidebar display.
 * - "working" -- agent is actively thinking / running a tool.
 * - "live"    -- agent is registered as running and available.
 * - "idle"    -- agent is paused, stopped, failed, or otherwise unavailable.
 */
export type AgentPresence = "live" | "working" | "idle";

/**
 * Map a registry agent status + working flag to a sidebar presence state.
 * Pure function -- exported for unit testing.
 */
export function computeAgentPresence(
  status: string | undefined,
  isWorking: boolean,
): AgentPresence {
  if (isWorking) return "working";
  if (status === "running") return "live";
  return "idle";
}

/** Minimal channel shape needed to build the sidebar presence map. */
export interface PresenceSourceChannel {
  id: string;
  settings?: { taostalk_agent?: string };
}

/**
 * Build the sidebar presence map from the bucketed agent DM sections plus
 * the topic/group channels that may carry an agent binding.
 *
 * - Live agent DMs: "working" while the bound agent is in `workingSlugs`,
 *   otherwise "live".
 * - Suspended/archived agent DMs: "idle".
 * - Topic/group channels bound via `settings.taostalk_agent`: "working"
 *   only while the bound agent is in `workingSlugs`; no entry otherwise,
 *   since absence renders no dot for non-agent channels.
 *
 * Pure function -- no side effects -- so it can be unit tested in isolation.
 */
export function buildAgentPresence<C extends PresenceSourceChannel>(
  dm: { live: C[]; suspended: C[]; archived: C[] },
  boundChannels: C[],
  workingSlugs: ReadonlySet<string>,
): Record<string, AgentPresence> {
  const presence: Record<string, AgentPresence> = {};
  for (const ch of dm.live) {
    const bound = ch.settings?.taostalk_agent;
    presence[ch.id] = computeAgentPresence(
      "running",
      !!bound && workingSlugs.has(bound),
    );
  }
  for (const ch of [...dm.suspended, ...dm.archived]) {
    presence[ch.id] = computeAgentPresence(undefined, false);
  }
  for (const ch of boundChannels) {
    const bound = ch.settings?.taostalk_agent;
    if (bound && workingSlugs.has(bound)) {
      presence[ch.id] = computeAgentPresence(undefined, true);
    }
  }
  return presence;
}

/** The agent identity a DM channel points at: its name or its non-"user" member. */
function channelAgentKeys<C extends AgentSectionChannel>(ch: C): string[] {
  const keys = [ch.name];
  const member = (ch.members ?? []).find((m) => m !== "user");
  if (member) keys.push(member);
  return keys;
}

/**
 * Bucket a list of DM channels into lifecycle sections.
 *
 * @param dmChannels  DM-type channels for the current sidebar scope.
 * @param liveAgents  Agents from /api/agents (each may carry a `status`).
 * @param archivedAgents  Agents from /api/agents/archived.
 */
export function bucketAgentChannels<C extends AgentSectionChannel>(
  dmChannels: C[],
  liveAgents: AgentSectionLiveAgent[],
  archivedAgents: AgentSectionArchivedAgent[],
): AgentSections<C> {
  // Index live agents by every name a channel might match on.
  const liveByKey = new Map<string, AgentSectionLiveAgent>();
  for (const a of liveAgents) {
    liveByKey.set(a.name, a);
    if (a.display_name) liveByKey.set(a.display_name, a);
  }

  const archivedKeys = new Set<string>();
  for (const a of archivedAgents) {
    if (a.archived_slug) archivedKeys.add(a.archived_slug);
    if (a.original?.name) archivedKeys.add(a.original.name);
    if (a.original?.display_name) archivedKeys.add(a.original.display_name);
  }

  const sections: AgentSections<C> = {
    live: [],
    suspended: [],
    archived: [],
    nonAgent: [],
  };

  // Until at least one agent list has loaded we cannot tell a live agent DM
  // from an orphaned one, so a member-without-match must NOT be archived yet.
  // While both lists are empty, treat unmatched agent DMs as non-archived
  // (they keep their Direct Messages placement) and re-bucket once data loads.
  const listsLoaded = liveAgents.length > 0 || archivedAgents.length > 0;

  for (const ch of dmChannels) {
    // a2a coordination channels are not agent DMs -- leave them in place.
    if (ch.settings?.kind === "a2a") {
      sections.nonAgent.push(ch);
      continue;
    }

    const keys = channelAgentKeys(ch);
    const hasAgentMember = (ch.members ?? []).some((m) => m !== "user");

    const liveAgent = keys.map((k) => liveByKey.get(k)).find(Boolean);
    if (liveAgent) {
      if (liveAgent.status === "running") sections.live.push(ch);
      else if (liveAgent.status === "paused") sections.suspended.push(ch);
      // Any other live status (e.g. "failed") is treated as not-live and
      // grouped under Archived rather than shown as Live.
      else sections.archived.push(ch);
      continue;
    }

    if (keys.some((k) => archivedKeys.has(k))) {
      sections.archived.push(ch);
      continue;
    }

    // A DM with an agent member that matches no known agent is an orphaned /
    // deleted-agent channel -- it belongs under Archived, never Live. But
    // only once the agent lists have loaded; before that, an unmatched agent
    // DM is almost certainly live and must stay non-archived. A DM with no
    // agent member is a plain user DM and keeps its placement.
    if (hasAgentMember && listsLoaded) sections.archived.push(ch);
    else sections.nonAgent.push(ch);
  }

  return sections;
}
