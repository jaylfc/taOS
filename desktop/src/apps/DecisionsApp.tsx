import { useState, useEffect, useCallback, useRef } from "react";
import {
  Inbox,
  Sparkles,
  Clock,
  AlertTriangle,
  Check,
  X,
  CheckCircle2,
  AlertCircle,
  History,
} from "lucide-react";
import { Button, Textarea } from "@/components/ui";

type DecisionType =
  | "single_select"
  | "multi_select"
  | "approve_deny"
  | "free_text";

type DecisionStatus = "pending" | "answered" | "expired" | "superseded";

interface DecisionOption {
  label: string;
  value: string;
  recommended?: boolean;
  rationale?: string;
}

interface DecisionAnswer {
  value: string | string[];
  answered_by?: string;
  answered_at?: number;
}

interface Decision {
  id: string;
  from_agent: string;
  project_id: string | null;
  question: string;
  type: DecisionType;
  options: DecisionOption[];
  context?: string | null;
  priority: "normal" | "blocking";
  status: DecisionStatus;
  answer?: DecisionAnswer | null;
  created_at: number | string;
  deadline?: number | null;
  // The decision this one supersedes (L1). Present on a revision; absent on an
  // original. When set, the supersession lineage can be walked via the history
  // endpoint.
  parent_decision_id?: string | null;
}

// created_at is stored as an epoch-seconds REAL on the backend, but the API
// may also serialise ISO strings; normalise both to milliseconds.
function toMillis(ts: number | string): number {
  if (typeof ts === "number") return ts < 1e12 ? ts * 1000 : ts;
  const parsed = Date.parse(ts);
  return Number.isNaN(parsed) ? Date.now() : parsed;
}

function relativeTime(ts: number | string): string {
  const diff = Date.now() - toMillis(ts);
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

const TYPE_LABEL: Record<DecisionType, string> = {
  single_select: "Pick one",
  multi_select: "Pick any",
  approve_deny: "Approve / Deny",
  free_text: "Free text",
};

function AgentBadge({ agent }: { agent: string }) {
  const handle = agent.startsWith("@") ? agent : `@${agent}`;
  return (
    <span className="inline-flex items-center rounded-full bg-accent/10 px-2 py-0.5 text-xs font-medium text-accent">
      {handle}
    </span>
  );
}

function PriorityBadge({ priority }: { priority: Decision["priority"] }) {
  if (priority !== "blocking") return null;
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-400">
      <AlertTriangle size={11} className="shrink-0" />
      Blocking
    </span>
  );
}

function RecommendedTag() {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-accent/15 px-2 py-0.5 text-[11px] font-semibold text-accent">
      <Sparkles size={10} className="shrink-0" />
      Recommended
    </span>
  );
}

/** A single selectable option button shared by single/multi select. */
function OptionRow({
  option,
  selected,
  onClick,
  disabled,
}: {
  option: DecisionOption;
  selected: boolean;
  onClick: () => void;
  disabled: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={selected}
      className={[
        "flex w-full flex-col gap-1 rounded-lg border px-3.5 py-2.5 text-left transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-60",
        selected
          ? "border-accent bg-accent/10 ring-1 ring-accent"
          : option.recommended
            ? "border-accent/40 bg-shell-surface hover:border-accent"
            : "border-shell-border bg-shell-surface hover:border-shell-border-strong",
      ].join(" ")}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-medium text-shell-text">
          {selected && <Check size={14} className="shrink-0 text-accent" />}
          {option.label}
        </span>
        {option.recommended && <RecommendedTag />}
      </div>
      {option.rationale && (
        <span className="text-xs text-shell-text-tertiary">{option.rationale}</span>
      )}
    </button>
  );
}

function DecisionCard({
  decision,
  onAnswer,
}: {
  decision: Decision;
  onAnswer: (id: string, value: string | string[]) => Promise<void>;
}) {
  const [multi, setMulti] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(value: string | string[]) {
    setError(null);
    setSubmitting(true);
    try {
      await onAnswer(decision.id, value);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not record answer.");
    } finally {
      setSubmitting(false);
    }
  }

  function toggleMulti(value: string) {
    setMulti((prev) =>
      prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value],
    );
  }

  return (
    <li className="flex flex-col gap-3 rounded-xl border border-shell-border bg-shell-bg-deep p-4">
      <div className="flex flex-wrap items-center gap-2">
        <AgentBadge agent={decision.from_agent} />
        <PriorityBadge priority={decision.priority} />
        {decision.project_id && (
          <span className="truncate rounded-full bg-shell-surface px-2 py-0.5 text-xs text-shell-text-secondary">
            {decision.project_id}
          </span>
        )}
        <span className="ml-auto flex items-center gap-1 text-xs text-shell-text-tertiary">
          <Clock size={11} className="shrink-0" />
          {relativeTime(decision.created_at)}
        </span>
      </div>

      <div className="flex flex-col gap-1">
        <p className="text-sm font-semibold text-shell-text">{decision.question}</p>
        {decision.context && (
          <p className="text-xs leading-relaxed text-shell-text-secondary">
            {decision.context}
          </p>
        )}
        <span className="mt-0.5 text-[11px] uppercase tracking-wide text-shell-text-tertiary">
          {TYPE_LABEL[decision.type]}
        </span>
      </div>

      {decision.type === "single_select" && (
        <div className="flex flex-col gap-2">
          {decision.options.map((opt) => (
            <OptionRow
              key={opt.value}
              option={opt}
              selected={false}
              disabled={submitting}
              onClick={() => submit(opt.value)}
            />
          ))}
        </div>
      )}

      {decision.type === "multi_select" && (
        <div className="flex flex-col gap-2">
          {decision.options.map((opt) => (
            <OptionRow
              key={opt.value}
              option={opt}
              selected={multi.includes(opt.value)}
              disabled={submitting}
              onClick={() => toggleMulti(opt.value)}
            />
          ))}
          <div className="flex justify-end">
            <Button
              type="button"
              disabled={submitting || multi.length === 0}
              onClick={() => submit(multi)}
              className="min-w-[100px]"
            >
              {submitting ? "Saving..." : "Submit"}
            </Button>
          </div>
        </div>
      )}

      {decision.type === "approve_deny" && (
        <div className="flex gap-2">
          <Button
            type="button"
            disabled={submitting}
            onClick={() => submit("approve")}
            className="flex-1 gap-1.5"
          >
            <Check size={15} />
            Approve
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={submitting}
            onClick={() => submit("deny")}
            className="flex-1 gap-1.5"
          >
            <X size={15} />
            Deny
          </Button>
        </div>
      )}

      {decision.type === "free_text" && (
        <div className="flex flex-col gap-2">
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Type your answer..."
            rows={3}
            maxLength={20000}
            aria-label="Answer"
            className="resize-none bg-shell-surface border-shell-border text-shell-text placeholder:text-shell-text-tertiary"
          />
          <div className="flex justify-end">
            <Button
              type="button"
              disabled={submitting || text.trim().length === 0}
              onClick={() => submit(text.trim())}
              className="min-w-[100px]"
            >
              {submitting ? "Saving..." : "Submit"}
            </Button>
          </div>
        </div>
      )}

      {error && (
        <div
          className="flex items-center gap-2 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400"
          role="alert"
        >
          <AlertCircle size={14} className="shrink-0" />
          {error}
        </div>
      )}
    </li>
  );
}

// The list endpoint returns { items: [...] }; tolerate a bare array too.
function asDecisionList(data: unknown): Decision[] {
  if (Array.isArray(data)) return data as Decision[];
  if (data && Array.isArray((data as { items?: unknown }).items)) {
    return (data as { items: Decision[] }).items;
  }
  return [];
}

function answerLabel(decision: Decision): string {
  const value = decision.answer?.value;
  if (value == null) return "—";
  const values = Array.isArray(value) ? value : [value];
  const labels = values.map((v) => {
    const opt = decision.options.find((o) => o.value === v);
    return opt ? opt.label : v;
  });
  return labels.join(", ");
}

/** Expandable supersession lineage for a revised decision. Fetches the chain
 * lazily on first expand so the archive list does not issue a request per card.
 * The endpoint returns the chain oldest first. */
function HistoryTrail({ decisionId }: { decisionId: string }) {
  const [open, setOpen] = useState(false);
  const [chain, setChain] = useState<Decision[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Guard against a state update after the card unmounts mid-fetch (an archive
  // card can unmount on a tab switch or list refresh while /history is still in
  // flight).
  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  async function toggle() {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (chain || loading) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/decisions/${decisionId}/history`);
      if (!res.ok) throw new Error("Could not load history.");
      const items = asDecisionList(await res.json());
      if (aliveRef.current) setChain(items);
    } catch (e) {
      if (aliveRef.current) {
        setError(e instanceof Error ? e.message : "Could not load history.");
      }
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-1.5">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="flex items-center gap-1 self-start text-xs font-medium text-shell-text-secondary transition-colors hover:text-shell-text"
      >
        <History size={12} className="shrink-0" />
        {open ? "Hide history" : "View history"}
      </button>
      {open && (
        <div className="flex flex-col gap-1.5 border-l border-shell-border pl-3">
          {loading && (
            <p className="text-xs text-shell-text-tertiary">Loading history...</p>
          )}
          {error && (
            <p className="text-xs text-red-400" role="alert">
              {error}
            </p>
          )}
          {chain && chain.length > 0 && (
            <ol className="flex flex-col gap-1.5" data-testid="decision-history">
              {chain.map((d, i) => (
                <li key={d.id} className="flex flex-col gap-0.5 text-xs">
                  <span className="flex items-start gap-1.5 text-shell-text-secondary">
                    <span className="shrink-0 text-shell-text-tertiary">{i + 1}.</span>
                    {d.question}
                  </span>
                  <span className="pl-4 text-shell-text-tertiary">
                    {d.status === "superseded" ? "superseded" : answerLabel(d)}
                    {" · "}
                    {relativeTime(d.answer?.answered_at ?? d.created_at)}
                  </span>
                </li>
              ))}
            </ol>
          )}
          {chain && chain.length === 0 && !loading && (
            <p className="text-xs text-shell-text-tertiary">No earlier versions.</p>
          )}
        </div>
      )}
    </div>
  );
}

function AnsweredCard({ decision }: { decision: Decision }) {
  return (
    <li className="flex flex-col gap-2 rounded-xl border border-shell-border bg-shell-surface p-4">
      <div className="flex flex-wrap items-center gap-2">
        <AgentBadge agent={decision.from_agent} />
        {decision.project_id && (
          <span className="truncate rounded-full bg-shell-bg-deep px-2 py-0.5 text-xs text-shell-text-secondary">
            {decision.project_id}
          </span>
        )}
        <span className="ml-auto text-xs text-shell-text-tertiary">
          {decision.answer?.answered_at
            ? relativeTime(decision.answer.answered_at)
            : relativeTime(decision.created_at)}
        </span>
      </div>
      <p className="text-sm font-medium text-shell-text">{decision.question}</p>
      <div className="flex items-center gap-2 text-sm">
        <CheckCircle2 size={15} className="shrink-0 text-accent" />
        <span className="text-shell-text-secondary">
          {decision.status === "superseded" ? "Superseded" : answerLabel(decision)}
        </span>
      </div>
      {decision.parent_decision_id && <HistoryTrail decisionId={decision.id} />}
    </li>
  );
}

type TabKey = "pending" | "answered";

export function DecisionsApp({ windowId: _windowId }: { windowId: string }) {
  const [tab, setTab] = useState<TabKey>("pending");
  const [pending, setPending] = useState<Decision[]>([]);
  const [answered, setAnswered] = useState<Decision[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (opts?: { silent?: boolean }) => {
    if (!opts?.silent) setLoading(true);
    try {
      const [pRes, aRes] = await Promise.all([
        fetch("/api/decisions?status=pending"),
        fetch("/api/decisions?status=answered"),
      ]);
      // Only overwrite a list when its request actually succeeded; a transient
      // failure must not blank out decisions the user can still act on.
      if (pRes.ok) setPending(asDecisionList(await pRes.json()));
      if (aRes.ok) setAnswered(asDecisionList(await aRes.json()));
    } catch {
      // Network error: keep whatever was last loaded in place.
    } finally {
      if (!opts?.silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const answer = useCallback(
    async (id: string, value: string | string[]) => {
      const res = await fetch(`/api/decisions/${id}/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const detail = data?.detail;
        throw new Error(
          typeof detail === "string" ? detail : "Could not record answer.",
        );
      }
      // Silent refresh: avoid flashing the full-screen Loading state and the
      // list re-mount on every answer.
      await load({ silent: true });
    },
    [load],
  );

  const list = tab === "pending" ? pending : answered;

  return (
    <div className="flex h-full flex-col overflow-hidden bg-shell-bg">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-shell-border px-5 py-4">
        <Inbox size={18} className="text-accent" />
        <h1 className="text-base font-semibold text-shell-text">Decisions</h1>
        {pending.length > 0 && (
          <span className="ml-1 inline-flex h-5 min-w-[20px] items-center justify-center rounded-full bg-accent px-1.5 text-xs font-semibold text-shell-bg">
            {pending.length}
          </span>
        )}
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-shell-border px-5 py-2.5">
        {(["pending", "answered"] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            aria-pressed={tab === t}
            className={[
              "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
              tab === t
                ? "bg-shell-surface text-shell-text shadow-sm"
                : "text-shell-text-secondary hover:text-shell-text",
            ].join(" ")}
          >
            {t === "pending"
              ? `Pending${pending.length ? ` (${pending.length})` : ""}`
              : "Archive"}
          </button>
        ))}
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-5 py-4">
        {loading ? (
          <p className="text-sm text-shell-text-tertiary">Loading...</p>
        ) : list.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-16 text-center">
            <Inbox size={28} className="text-shell-text-tertiary" />
            <p className="text-sm text-shell-text-secondary">
              {tab === "pending"
                ? "No decisions waiting on you."
                : "No answered decisions yet."}
            </p>
          </div>
        ) : (
          <ul className="flex flex-col gap-3">
            {list.map((d) =>
              tab === "pending" ? (
                <DecisionCard key={d.id} decision={d} onAnswer={answer} />
              ) : (
                <AnsweredCard key={d.id} decision={d} />
              ),
            )}
          </ul>
        )}
      </div>
    </div>
  );
}
