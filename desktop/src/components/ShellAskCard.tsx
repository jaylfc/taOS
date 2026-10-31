import { useState } from "react";
import { TerminalSquare } from "lucide-react";

/** One shell command the taOS Agent asked to run (a `shell_ask` chat frame). */
export interface ShellAsk {
  program: string;
  decision_id: string;
  command: string;
}

type AskState = "idle" | "saving" | "allowed" | "denied" | "answered" | "error";

const STATUS_TEXT: Partial<Record<AskState, string>> = {
  allowed: "Allowed. Ask the agent to try again.",
  denied: "Denied.",
  answered: "Already answered.",
  error: "Could not save. Try again, or answer it in Decisions.",
};

/**
 * Inline card for a shell command the agent has not been allowed yet. It
 * answers the same Decision the Decisions inbox shows, so either place
 * settles it; the answer becomes a rule in Agents > taOS Agent > Shell.
 */
export function ShellAskCard({ ask }: { ask: ShellAsk }) {
  const [state, setState] = useState<AskState>("idle");

  async function answer(value: "approve" | "deny") {
    setState("saving");
    try {
      const res = await fetch(`/api/decisions/${encodeURIComponent(ask.decision_id)}/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value }),
      });
      if (res.ok) setState(value === "approve" ? "allowed" : "denied");
      else setState(res.status === 409 ? "answered" : "error");
    } catch {
      setState("error");
    }
  }

  const done = state === "allowed" || state === "denied" || state === "answered";

  return (
    <div className="flex justify-start">
      <div
        className="max-w-[85%] rounded-xl px-3 py-2 text-sm bg-shell-surface border border-white/10 flex flex-col gap-2"
        aria-label={`Shell command request: ${ask.program}`}
      >
        <div className="flex items-center gap-2">
          <TerminalSquare size={14} className="text-shell-text-secondary shrink-0" />
          <span>
            Let the agent run <code className="text-xs">{ask.program}</code> in its shell?
          </span>
        </div>
        <code className="text-xs text-shell-text-secondary break-all">{ask.command}</code>
        {!done && (
          <div className="flex gap-2">
            <button
              onClick={() => answer("approve")}
              disabled={state === "saving"}
              aria-label={`Allow ${ask.program}`}
              className="bg-accent text-white px-3 py-1 rounded text-xs disabled:opacity-50"
            >
              Allow
            </button>
            <button
              onClick={() => answer("deny")}
              disabled={state === "saving"}
              aria-label={`Deny ${ask.program}`}
              className="bg-white/10 hover:bg-white/15 px-3 py-1 rounded text-xs disabled:opacity-50"
            >
              Deny
            </button>
          </div>
        )}
        {STATUS_TEXT[state] && (
          <div role="status" className="text-xs text-shell-text-secondary">
            {STATUS_TEXT[state]}
          </div>
        )}
      </div>
    </div>
  );
}
