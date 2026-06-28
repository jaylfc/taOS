import { useEffect, useState } from "react";

// Per-project Decisions archive (Decisions spec MVP item 4): a read-only,
// project-scoped view of the decision history so the workspace shows not just
// what was done but why it went the way it did. It is a filtered read of
// GET /api/decisions?project_id=; answering happens in the Decisions app, not
// here. Kept self-contained (its own minimal types/helpers) so it does not
// couple the Projects app to the Decisions app internals.

type DecisionStatus = "pending" | "answered" | "expired" | "superseded";

interface DecisionOption {
  label: string;
  value: string;
}

interface Decision {
  id: string;
  from_agent: string;
  question: string;
  status: DecisionStatus;
  options: DecisionOption[];
  answer?: { value: string | string[] } | null;
  created_at: number | string;
}

// created_at is an epoch-seconds REAL on the backend but may serialise as an
// ISO string; normalise both to milliseconds.
function toMillis(ts: number | string): number {
  if (typeof ts === "number") return ts < 1e12 ? ts * 1000 : ts;
  const parsed = Date.parse(ts);
  return Number.isNaN(parsed) ? Date.now() : parsed;
}

function relativeTime(ts: number | string): string {
  // Clamp to 0 so a row a moment in the future (client/server clock skew) does
  // not render as "-1m ago".
  const diff = Math.max(0, Date.now() - toMillis(ts));
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function answerText(d: Decision): string {
  const value = d.answer?.value;
  if (value == null) return "";
  const values = Array.isArray(value) ? value : [value];
  return values
    .map((v) => d.options.find((o) => o.value === v)?.label ?? v)
    .join(", ");
}

// The list endpoint returns { items: [...] }; tolerate a bare array too.
function asDecisionList(data: unknown): Decision[] {
  if (Array.isArray(data)) return data as Decision[];
  if (data && Array.isArray((data as { items?: unknown }).items)) {
    return (data as { items: Decision[] }).items;
  }
  return [];
}

const STATUS_STYLE: Record<DecisionStatus, string> = {
  pending: "bg-amber-500/10 text-amber-400",
  answered: "bg-accent/10 text-accent",
  superseded: "bg-shell-bg-deep text-shell-text-tertiary",
  expired: "bg-shell-bg-deep text-shell-text-tertiary",
};

export function ProjectDecisions({ projectId }: { projectId: string }) {
  const [items, setItems] = useState<Decision[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    // Reset on project switch so the previous project's decisions never linger,
    // and so a failure is shown as an error rather than a stale or empty list.
    setLoading(true);
    setError(false);
    setItems([]);
    fetch(`/api/decisions?project_id=${encodeURIComponent(projectId)}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (!cancelled) setItems(asDecisionList(data));
      })
      .catch(() => {
        // A non-ok response or network error must read as a failure, not as an
        // empty project.
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  if (loading) {
    return <p className="text-sm text-shell-text-tertiary">Loading decisions...</p>;
  }

  if (error) {
    return (
      <p className="text-sm text-red-400" role="alert">
        Could not load decisions for this project.
      </p>
    );
  }

  if (items.length === 0) {
    return (
      <p className="text-sm text-shell-text-secondary">
        No decisions for this project yet.
      </p>
    );
  }

  return (
    <ul className="flex flex-col gap-2" aria-label="Project decisions">
      {items.map((d) => {
        const handle = d.from_agent.startsWith("@")
          ? d.from_agent
          : `@${d.from_agent}`;
        // Show the recorded answer whenever there is one, including a
        // superseded decision (it was answered before being replaced).
        const answer = answerText(d);
        return (
          <li
            key={d.id}
            className="flex flex-col gap-1.5 rounded-xl border border-shell-border bg-shell-surface p-3.5"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center rounded-full bg-accent/10 px-2 py-0.5 text-xs font-medium text-accent">
                {handle}
              </span>
              <span
                className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium capitalize ${STATUS_STYLE[d.status]}`}
              >
                {d.status}
              </span>
              <span className="ml-auto text-xs text-shell-text-tertiary">
                {relativeTime(d.created_at)}
              </span>
            </div>
            <p className="text-sm font-medium text-shell-text">{d.question}</p>
            {answer && (
              <p className="text-xs text-shell-text-secondary">Answer: {answer}</p>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export default ProjectDecisions;
