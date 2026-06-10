import { useState, useEffect, useCallback } from "react";
import {
  Activity, AlertCircle, Brain, Bug, ChevronDown, ChevronRight,
  Cpu, Loader2, MessageSquare, Shield, Wrench, Zap,
} from "lucide-react";
import { Button } from "@/components/ui";

interface TraceEvent {
  id: string;
  kind: string;
  trace_id: string | null;
  created_at: number;
  model: string | null;
  duration_ms: number | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cost_usd: number | null;
  error: string | null;
  payload: Record<string, unknown>;
}

interface OtelSpan {
  span_id: string;
  trace_id: string;
  parent_span_id: string | null;
  name: string;
  start_time_ns: number;
  end_time_ns: number;
  status_code: string | null;
}

function fmtMs(ms: number | null): string {
  if (ms === null) return "";
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function fmtCost(usd: number | null): string {
  if (!usd) return "";
  return usd < 0.001 ? `$${(usd * 1000).toFixed(3)}m` : `$${usd.toFixed(4)}`;
}

function fmtTime(ts: number): string {
  const delta = Date.now() - ts * 1000;
  if (delta < 60_000) return "just now";
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)}m ago`;
  if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)}h ago`;
  return new Date(ts * 1000).toLocaleString();
}

const KIND_ICON: Record<string, React.ComponentType<{ size?: number; className?: string }>> = {
  llm_call: Cpu,
  tool_call: Wrench,
  tool_result: Wrench,
  reasoning: Brain,
  message_in: MessageSquare,
  message_out: MessageSquare,
  error: AlertCircle,
  lifecycle: Zap,
  reasoning_audit: Shield,
};

const KIND_COLOR: Record<string, string> = {
  llm_call: "text-blue-400",
  tool_call: "text-yellow-400",
  tool_result: "text-green-400",
  reasoning: "text-purple-400",
  message_in: "text-shell-text-secondary",
  message_out: "text-shell-text-secondary",
  error: "text-red-400",
  lifecycle: "text-shell-text-tertiary",
  reasoning_audit: "text-shell-text-secondary",
};

function kindLabel(evt: TraceEvent): string {
  if (evt.kind === "llm_call" && evt.model) {
    return evt.model.split("/").pop() ?? evt.model;
  }
  if (evt.kind === "tool_call" || evt.kind === "tool_result") {
    const tool = (evt.payload as { tool?: string }).tool;
    return tool ? `${evt.kind.replace("_", " ")} — ${tool}` : evt.kind.replace("_", " ");
  }
  return evt.kind.replace("_", " ");
}

function SpanWaterfall({ spans }: { spans: OtelSpan[] }) {
  const sorted = [...spans].sort((a, b) => a.start_time_ns - b.start_time_ns);
  const minNs = sorted[0]?.start_time_ns ?? 0;
  const maxNs = Math.max(...sorted.map((s) => s.end_time_ns));
  const totalNs = maxNs - minNs || 1;

  return (
    <div className="mt-2 space-y-0.5">
      <p className="text-[10px] text-shell-text-tertiary uppercase tracking-wide mb-1">
        OTel spans ({spans.length})
      </p>
      {sorted.map((span) => {
        const left = ((span.start_time_ns - minNs) / totalNs) * 100;
        const width = Math.max(((span.end_time_ns - span.start_time_ns) / totalNs) * 100, 1);
        const dMs = Math.round((span.end_time_ns - span.start_time_ns) / 1e6);
        return (
          <div key={span.span_id} title={`${span.name} — ${dMs}ms`}>
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-shell-text-tertiary w-28 shrink-0 truncate">
                {span.name}
              </span>
              <div className="relative flex-1 h-3 rounded-sm overflow-hidden bg-white/5">
                <div
                  className={`absolute h-full rounded-sm ${span.status_code === "ERROR" ? "bg-red-500/60" : "bg-blue-500/50"}`}
                  style={{ left: `${left}%`, width: `${width}%` }}
                />
              </div>
              <span className="text-[10px] text-shell-text-tertiary w-12 text-right shrink-0">
                {dMs}ms
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function AgentTracesPanel({ agentName }: { agentName: string }) {
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [spans, setSpans] = useState<OtelSpan[]>([]);
  const [spansLoading, setSpansLoading] = useState(false);
  const [debug, setDebug] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const loadEvents = useCallback(async () => {
    try {
      const res = await fetch(
        `/api/agents/${encodeURIComponent(agentName)}/trace?limit=100`,
      );
      if (res.ok) {
        const data = await res.json();
        // API returns newest-first; reverse for chronological display.
        setEvents(((data.events ?? []) as TraceEvent[]).reverse());
      }
    } catch {
      // ignore — keep stale state visible
    }
    setLoading(false);
  }, [agentName]);

  useEffect(() => {
    loadEvents();
    const interval = setInterval(loadEvents, 10_000);
    return () => clearInterval(interval);
  }, [loadEvents]);

  const loadSpans = useCallback(async (traceId: string) => {
    setSpansLoading(true);
    try {
      const res = await fetch(
        `/api/agents/${encodeURIComponent(agentName)}/otel-spans?trace_id=${encodeURIComponent(traceId)}&limit=200`,
      );
      setSpans(res.ok ? ((await res.json()).spans ?? []) : []);
    } catch {
      setSpans([]);
    }
    setSpansLoading(false);
  }, [agentName]);

  const select = (evt: TraceEvent) => {
    if (selectedId === evt.id) {
      setSelectedId(null);
      setSpans([]);
      return;
    }
    setSelectedId(evt.id);
    if (evt.trace_id) loadSpans(evt.trace_id);
    else setSpans([]);
  };

  const toggleExpand = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  // Summary stats (llm_call events only)
  const llmCalls = events.filter((e) => e.kind === "llm_call");
  const totalTokens = llmCalls.reduce((s, e) => s + (e.tokens_in ?? 0) + (e.tokens_out ?? 0), 0);
  const totalCost = llmCalls.reduce((s, e) => s + (e.cost_usd ?? 0), 0);
  const avgLatency =
    llmCalls.length > 0
      ? Math.round(llmCalls.reduce((s, e) => s + (e.duration_ms ?? 0), 0) / llmCalls.length)
      : null;

  // Most-recent reasoning_audit verdict
  const audit = [...events].reverse().find((e) => e.kind === "reasoning_audit");
  const auditVerdict = (audit?.payload as { verdict?: string } | undefined)?.verdict;

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 size={20} className="animate-spin text-shell-text-tertiary" />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Summary strip */}
      <div className="flex items-center gap-3 px-3 py-1.5 border-b border-white/5 bg-shell-surface/20 shrink-0 text-xs text-shell-text-secondary">
        <span className="flex items-center gap-1">
          <Cpu size={10} className="text-blue-400" />
          {llmCalls.length} calls
        </span>
        {totalTokens > 0 && (
          <span>{totalTokens.toLocaleString()} tokens</span>
        )}
        {totalCost > 0 && <span>{fmtCost(totalCost)}</span>}
        {avgLatency !== null && <span>avg {fmtMs(avgLatency)}</span>}
        {auditVerdict && (
          <span
            className={`flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium ${
              auditVerdict === "pass"
                ? "bg-green-500/20 text-green-400"
                : auditVerdict === "warn"
                  ? "bg-yellow-500/20 text-yellow-400"
                  : "bg-red-500/20 text-red-400"
            }`}
          >
            <Shield size={9} />
            reasoning {auditVerdict}
          </span>
        )}
        <Button
          variant="ghost"
          size="icon"
          className={`ml-auto h-5 w-5 ${debug ? "text-yellow-400" : "text-shell-text-tertiary"}`}
          onClick={() => setDebug((d) => !d)}
          title="Toggle debug mode"
          aria-label="Toggle debug mode"
          aria-pressed={debug}
        >
          <Bug size={11} />
        </Button>
      </div>

      {/* Event list */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {events.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-shell-text-tertiary gap-2">
            <Activity size={24} />
            <p className="text-sm">No traces yet</p>
            <p className="text-xs">Traces appear here as the agent runs</p>
          </div>
        ) : (
          events.map((evt) => {
            const Icon = KIND_ICON[evt.kind] ?? Activity;
            const iconColor = KIND_COLOR[evt.kind] ?? "text-shell-text-tertiary";
            const isSelected = selectedId === evt.id;
            const isOpen = expanded[evt.id];
            const auditVerdictRow =
              evt.kind === "reasoning_audit"
                ? (evt.payload as { verdict?: string }).verdict
                : undefined;

            return (
              <div
                key={evt.id}
                className={`rounded border px-2 py-1.5 cursor-pointer transition-colors ${
                  isSelected
                    ? "border-blue-500/40 bg-blue-500/10"
                    : "border-white/5 bg-shell-surface/20 hover:bg-shell-surface/40"
                }`}
                onClick={() => select(evt)}
                role="button"
                tabIndex={0}
                aria-expanded={isSelected}
                onKeyDown={(e) => e.key === "Enter" && select(evt)}
              >
                {/* Row header */}
                <div className="flex items-center gap-2 min-w-0">
                  <Icon size={11} className={`${iconColor} shrink-0`} aria-hidden />
                  <span className="text-xs flex-1 min-w-0 truncate text-shell-text">
                    {kindLabel(evt)}
                  </span>
                  {evt.duration_ms !== null && (
                    <span className="text-[10px] text-shell-text-tertiary shrink-0">
                      {fmtMs(evt.duration_ms)}
                    </span>
                  )}
                  {evt.tokens_in !== null && (
                    <span className="text-[10px] text-shell-text-tertiary shrink-0">
                      {((evt.tokens_in ?? 0) + (evt.tokens_out ?? 0)).toLocaleString()}t
                    </span>
                  )}
                  {evt.cost_usd ? (
                    <span className="text-[10px] text-shell-text-tertiary shrink-0">
                      {fmtCost(evt.cost_usd)}
                    </span>
                  ) : null}
                  {auditVerdictRow && (
                    <span
                      className={`text-[10px] px-1 rounded shrink-0 ${
                        auditVerdictRow === "pass"
                          ? "bg-green-500/20 text-green-400"
                          : auditVerdictRow === "warn"
                            ? "bg-yellow-500/20 text-yellow-400"
                            : "bg-red-500/20 text-red-400"
                      }`}
                    >
                      {auditVerdictRow}
                    </span>
                  )}
                  <span className="text-[10px] text-shell-text-tertiary shrink-0">
                    {fmtTime(evt.created_at)}
                  </span>
                  <button
                    className="shrink-0 text-shell-text-tertiary hover:text-shell-text"
                    onClick={(e) => toggleExpand(evt.id, e)}
                    aria-label={isOpen ? "Collapse" : "Expand"}
                  >
                    {isOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                  </button>
                </div>

                {/* Inline error */}
                {evt.error && (
                  <p className="mt-0.5 text-[10px] text-red-400 truncate pl-4">
                    {evt.error}
                  </p>
                )}

                {/* Expanded body */}
                {isOpen && (
                  <div className="mt-1.5 pl-4 space-y-1">
                    {evt.kind === "reasoning" && (
                      <p className="text-xs text-shell-text-secondary whitespace-pre-wrap line-clamp-6">
                        {String((evt.payload as { text?: string }).text ?? "")}
                      </p>
                    )}
                    {(evt.kind === "message_in" || evt.kind === "message_out") && (
                      <p className="text-xs text-shell-text-secondary whitespace-pre-wrap line-clamp-4">
                        {String(
                          (evt.payload as { text?: string; content?: string }).text ??
                            (evt.payload as { text?: string; content?: string }).content ??
                            "",
                        )}
                      </p>
                    )}
                    {evt.kind === "lifecycle" && (
                      <p className="text-xs text-shell-text-secondary">
                        {String((evt.payload as { event?: string }).event ?? "")}
                        {(evt.payload as { reason?: string }).reason
                          ? ` — ${(evt.payload as { reason?: string }).reason}`
                          : ""}
                      </p>
                    )}
                    {evt.kind === "reasoning_audit" &&
                      ((evt.payload as { flags?: string[] }).flags ?? []).map((f, i) => (
                        <p key={i} className="text-[10px] text-yellow-300">
                          • {f}
                        </p>
                      ))}
                    {debug && (
                      <pre className="text-[10px] text-shell-text-tertiary p-1.5 rounded bg-shell-bg-deep border border-white/5 overflow-x-auto max-h-40">
                        {JSON.stringify(evt.payload, null, 2)}
                      </pre>
                    )}
                  </div>
                )}

                {/* OTel waterfall on selection */}
                {isSelected && (
                  <div className="mt-2 pl-4">
                    {spansLoading ? (
                      <div className="flex items-center gap-1 text-[10px] text-shell-text-tertiary">
                        <Loader2 size={9} className="animate-spin" />
                        loading spans…
                      </div>
                    ) : spans.length > 0 ? (
                      <SpanWaterfall spans={spans} />
                    ) : (
                      <p className="text-[10px] text-shell-text-tertiary">
                        No OTel spans for this trace
                      </p>
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
