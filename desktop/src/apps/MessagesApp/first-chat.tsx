import { useEffect, useRef, useState } from "react";

/**
 * First-run setup hands Messages the assistant it just deployed. The deploy
 * makes the agent's DM once the agent is up, which takes a while, so Messages
 * re-reads its channels until that DM appears and then opens it, once. It
 * also reads the deploy's status, so a failed deploy says so instead of
 * leaving "Setting up" on screen.
 */

const POLL_MS = 3000;
/** Long enough for a first container start on a phone; the poll slows after. */
const GIVE_UP_MS = 10 * 60 * 1000;
/** After giving up, a deferred model pull can still finish the deploy. */
const LATE_POLL_MS = 30 * 1000;
const TOO_LONG = "Setting up your assistant is taking longer than expected.";
const NO_DM_AFTER_SUCCESS = "Your assistant is set up, but its chat could not be created.";

/** The deploy's error when it failed. A missing record (404, e.g. after a
 * server restart) or an unreadable reply is not a failure: keep waiting.
 * Returns { status, error } where status is the deploy status and error is
 * the failure message (only when status === "failed"). */
async function deployFailure(agent: string): Promise<{ status: string | undefined; error: string | undefined }> {
  try {
    const res = await fetch(`/api/agents/${encodeURIComponent(agent)}/deploy-status`);
    if (!res.ok) return { status: undefined, error: undefined };
    const task = await res.json();
    const status = task?.status;
    if (status !== "failed") return { status, error: undefined };
    const err = typeof task.error === "string" ? task.error.trim() : "";
    if (!err) return { status, error: "The deploy failed." };
    return { status, error: /[.!?]$/.test(err) ? err : `${err}.` };
  } catch {
    return { status: undefined, error: undefined };
  }
}

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
): { waiting: boolean; channelId: string | undefined; failure: string | undefined } {
  const opened = useRef(false);
  const [failure, setFailure] = useState<string | undefined>();
  const [late, setLate] = useState(false);
  const dm = agent ? findAgentDm(channels, agent) : undefined;
  const successPollsRef = useRef(0);

  useEffect(() => {
    if (!dm || opened.current) return;
    opened.current = true;
    open(dm.id);
  }, [dm, open]);

  const found = Boolean(dm);
  useEffect(() => {
    if (!agent || found || failure) return;
    const started = Date.now();
    let live = true;
    const id = setInterval(() => {
      if (!late && Date.now() - started > GIVE_UP_MS) {
        setLate(true);
        return;
      }
      void refresh();
      void deployFailure(agent).then(({ status, error }) => {
        if (!live) return;
        if (error) {
          setFailure(error);
          return;
        }
        if (status === "success" && !found) {
          successPollsRef.current += 1;
          if (successPollsRef.current >= 3) {
            setFailure(NO_DM_AFTER_SUCCESS);
          }
        } else {
          successPollsRef.current = 0;
        }
      });
    }, late ? LATE_POLL_MS : POLL_MS);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [agent, found, failure, late, refresh]);

  return {
    waiting: Boolean(agent) && !found && !failure && !late,
    channelId: dm?.id,
    failure: found ? undefined : (failure ?? (late ? TOO_LONG : undefined)),
  };
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