import { useEffect, useRef } from "react";

/**
 * First-run setup hands Messages the assistant it just deployed. The deploy
 * makes the agent's DM once the agent is up, which takes a while, so Messages
 * re-reads its channels until that DM appears and then opens it, once.
 */

const POLL_MS = 3000;
/** Long enough for a first container start on a phone; the poll stops after. */
const GIVE_UP_MS = 10 * 60 * 1000;

interface ChannelLike {
  id: string;
  type: string;
  members?: string[];
}

export function findAgentDm<C extends ChannelLike>(channels: C[], agent: string): C | undefined {
  return channels.find(
    (c) =>
      c.type === "dm" &&
      (c.members ?? []).length === 2 &&
      (c.members ?? []).includes("user") &&
      (c.members ?? []).includes(agent),
  );
}

export function useFirstChat(
  agent: string | undefined,
  channels: ChannelLike[],
  refresh: () => Promise<void>,
  open: (channelId: string) => void,
): { waiting: boolean; channelId: string | undefined } {
  const opened = useRef(false);
  const dm = agent ? findAgentDm(channels, agent) : undefined;

  useEffect(() => {
    if (!dm || opened.current) return;
    opened.current = true;
    open(dm.id);
  }, [dm, open]);

  const found = Boolean(dm);
  useEffect(() => {
    if (!agent || found) return;
    const started = Date.now();
    const id = setInterval(() => {
      if (Date.now() - started > GIVE_UP_MS) {
        clearInterval(id);
        return;
      }
      void refresh();
    }, POLL_MS);
    return () => clearInterval(id);
  }, [agent, found, refresh]);

  return { waiting: Boolean(agent) && !found, channelId: dm?.id };
}

export function SuggestedPrompts({ prompts, onPick }: { prompts: string[]; onPick: (p: string) => void }) {
  if (prompts.length === 0) return null;
  return (
    <div className="mx-4 mb-2 flex flex-wrap gap-2 shrink-0" role="group" aria-label="Suggested prompts">
      {prompts.map((p) => (
        <button
          key={p}
          type="button"
          onClick={() => onPick(p)}
          className="px-3 py-1.5 rounded-full text-[13px] bg-shell-surface border border-white/10 text-shell-text hover:bg-shell-surface-active transition-colors text-left"
        >
          {p}
        </button>
      ))}
    </div>
  );
}
