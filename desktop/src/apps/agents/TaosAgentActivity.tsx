import { useEffect, useState } from "react";
import { ExternalLink } from "lucide-react";

/* ------------------------------------------------------------------ */
/*  TaosAgentActivity                                                  */
/*  Plain list of what the taOS agent did for the user: gated external */
/*  writes and reversible local fixes (trace kinds external_write and  */
/*  local_fix on the taos-agent trace). When, what, where, Open link.  */
/* ------------------------------------------------------------------ */

interface ActivityEvent {
  id: string;
  kind: string;
  created_at: number;
  payload: Record<string, unknown>;
}

const AUDIT_KINDS = ["external_write", "local_fix"] as const;

const STATE_LABEL: Record<string, string> = {
  pending: "waiting for you",
  confirmed: "allowed",
  executed: "done",
  declined: "declined",
  failed: "failed",
};

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

/** Newest first; one line per gated action (its latest row), every local fix. */
export function collapseActivity(events: ActivityEvent[]): ActivityEvent[] {
  const sorted = [...events].sort((a, b) => b.created_at - a.created_at);
  const seen = new Set<string>();
  return sorted.filter((e) => {
    if (e.kind !== "external_write") return true;
    const id = str(e.payload.action_id);
    if (!id) return true;
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

function what(e: ActivityEvent): string {
  if (e.kind === "local_fix") {
    return `${str(e.payload.tool)} on ${str(e.payload.target)}`;
  }
  const state = STATE_LABEL[str(e.payload.state)] ?? str(e.payload.state);
  return state ? `${str(e.payload.action)} (${state})` : str(e.payload.action);
}

function where(e: ActivityEvent): string {
  if (e.kind === "local_fix") return "this computer";
  return str(e.payload.repo) || str(e.payload.target);
}

export function TaosAgentActivity() {
  const [events, setEvents] = useState<ActivityEvent[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const all: ActivityEvent[] = [];
      for (const kind of AUDIT_KINDS) {
        try {
          const res = await fetch(`/api/agents/taos-agent/trace?kind=${kind}&limit=200`);
          if (res.ok) {
            const data = await res.json();
            all.push(...((data.events ?? []) as ActivityEvent[]));
          }
        } catch {
          // non-fatal: show whatever loaded
        }
      }
      if (!cancelled) setEvents(collapseActivity(all));
    }
    load();
    return () => { cancelled = true; };
  }, []);

  if (events === null) {
    return <div className="text-sm opacity-60">Loading…</div>;
  }
  if (events.length === 0) {
    return (
      <p className="text-xs text-shell-text-tertiary">
        Nothing yet. Pull requests, issues and fixes the taOS agent makes for you show up here.
      </p>
    );
  }
  return (
    <ul aria-label="taOS agent activity" className="flex flex-col divide-y divide-white/5">
      {events.map((e) => {
        const url = str(e.payload.url);
        return (
          <li key={e.id} className="flex items-center gap-3 py-2 text-xs">
            <span className="w-32 shrink-0 text-shell-text-tertiary">
              {new Date(e.created_at * 1000).toLocaleString()}
            </span>
            <span className="flex-1 min-w-0 truncate text-shell-text">{what(e)}</span>
            <span className="shrink-0 truncate max-w-[10rem] text-shell-text-secondary">{where(e)}</span>
            {url.startsWith("https://") ? (
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="shrink-0 inline-flex items-center gap-1 text-blue-400 hover:text-blue-300"
              >
                Open <ExternalLink size={11} aria-hidden />
              </a>
            ) : (
              <span className="shrink-0 w-10" />
            )}
          </li>
        );
      })}
    </ul>
  );
}
