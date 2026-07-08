// Pure logic for the #1741 agent-window stall watchdog, split out of
// MessagesApp so the escalation thresholds and the "which agent do we wait
// for" decision can be unit-tested without rendering the full chat component.
//
// The chat window streams an agent reply over a WebSocket as a series of
// deltas. If a generation stalls (endless model loop, broken stream, or a
// missing completion event) no further frames arrive and the window would
// otherwise look frozen with no hint. The component arms a watch on send,
// bumps `lastActivityAt` on every inbound frame, and clears it on completion;
// these helpers decide when to arm and what banner (if any) to show.

export interface StallWatch {
  channelId: string;
  agent: string;
  lastActivityAt: number;
}

export interface StallWatchChannel {
  id: string;
  type?: string;
  members?: string[];
}

/**
 * Decide which agent a send should wait on, or null if none is expected to
 * reply. In a DM with an agent we always wait; elsewhere we only wait when an
 * agent member is @mentioned, so human-only channels never trip the watch.
 */
export function pickWatchAgent(
  channel: StallWatchChannel | undefined,
  text: string,
  agentNames: string[],
): string | null {
  if (!channel) return null;
  const agentMembers = (channel.members ?? []).filter(
    (m) => m !== "user" && agentNames.includes(m),
  );
  if (channel.type === "dm") return agentMembers[0] ?? null;
  return agentMembers.find((m) => text.includes(`@${m}`)) ?? null;
}

export interface StallInfo {
  agent: string;
  seconds: number;
  stalled: boolean;
}

// Silent for the first HINT window (healthy responses resolve well before it),
// a soft "taking longer" hint after, an amber "may be stalled" warning at WARN.
export const STALL_HINT_MS = 20_000;
export const STALL_WARN_MS = 75_000;

/**
 * Derive the stall banner from the current watch, or null if nothing should
 * show (no watch, watch belongs to another channel, or activity is recent).
 */
export function computeStallInfo(
  watch: StallWatch | null,
  selectedChannel: string | null,
  now: number,
): StallInfo | null {
  if (!watch || watch.channelId !== selectedChannel) return null;
  const elapsed = now - watch.lastActivityAt;
  if (elapsed < STALL_HINT_MS) return null;
  return {
    agent: watch.agent,
    seconds: Math.floor(elapsed / 1000),
    stalled: elapsed >= STALL_WARN_MS,
  };
}
