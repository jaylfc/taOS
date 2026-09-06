import { useState, useEffect } from "react";
import { useWidgetSize } from "@/hooks/use-widget-size";
import { resolveAgentEmoji } from "@/lib/agent-emoji";

interface Agent {
  name: string;
  status: string;
  emoji?: string;
  framework?: string;
}

function dotColor(status: string): string {
  if (status === "online" || status === "running") return "#4ade80";
  if (status === "offline" || status === "stopped") return "#f87171";
  return "#fbbf24";
}

function PulseDot({ color }: { color: string }) {
  return (
    <span
      style={{ position: "relative", display: "inline-flex", width: 8, height: 8, flexShrink: 0 }}
      aria-hidden="true"
    >
      <span
        style={{
          position: "absolute", inset: 0, borderRadius: "50%",
          background: color, opacity: 0.4,
          animation: "ping 1.5s cubic-bezier(0,0,0.2,1) infinite",
        }}
      />
      <span style={{ position: "relative", display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: color }} />
    </span>
  );
}

export function AgentStatusWidget() {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [error, setError] = useState(false);
  const [containerRef, { tier }] = useWidgetSize();

  useEffect(() => {
    let cancelled = false;

    async function fetchAgents() {
      try {
        const res = await fetch("/api/agents", { headers: { Accept: "application/json" } });
        if (!res.ok) throw new Error("Failed");
        const ct = res.headers.get("content-type") ?? "";
        if (!ct.includes("application/json")) {
          if (!cancelled) { setAgents([]); setError(false); }
          return;
        }
        const data = await res.json();
        if (!cancelled) {
          const list = Array.isArray(data) ? data : (data.agents ?? data.items ?? []);
          setAgents(list.map((a: Record<string, unknown>) => ({
            name: (a.name as string) ?? (a.id as string) ?? "Unknown",
            status: (a.status as string) ?? "unknown",
            emoji: typeof a.emoji === "string" ? a.emoji : undefined,
            framework: typeof a.framework === "string" ? a.framework : undefined,
          })));
          setError(false);
        }
      } catch {
        if (!cancelled) setError(true);
      }
    }

    fetchAgents();
    const interval = setInterval(fetchAgents, 15000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  const running = agents ? agents.filter((a) => a.status === "running" || a.status === "online").length : 0;
  const total = agents ? agents.length : 0;
  const statusColor = running > 0 ? "#4ade80" : "#fbbf24";

  const dimText: React.CSSProperties = { fontSize: "0.65rem", color: "rgba(255,255,255,0.35)" };
  const labelStyle: React.CSSProperties = { fontSize: "0.65rem", fontWeight: 600, color: "rgba(255,255,255,0.4)", textTransform: "uppercase", letterSpacing: "0.06em" };

  return (
    <div
      ref={containerRef}
      style={{ height: "100%", display: "flex", flexDirection: "column", padding: tier === "s" ? "0 2px" : "2px 2px 4px", overflow: "hidden" }}
      aria-label="Agent status widget"
      role="region"
    >
      {tier === "s" && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", gap: 8 }}>
          <PulseDot color={statusColor} />
          <span style={{ fontSize: "1.2rem", fontWeight: 600, color: "rgba(255,255,255,0.9)", fontVariantNumeric: "tabular-nums" }}>
            {error || !agents ? "—" : total}
          </span>
          <span style={{ fontSize: "0.7rem", color: "rgba(255,255,255,0.45)" }}>
            {total === 1 ? "agent" : "agents"}
          </span>
        </div>
      )}

      {tier === "m" && (
        <>
          <div style={labelStyle}>Agents</div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 6, margin: "4px 0 8px" }}>
            <span style={{ fontSize: "1.6rem", fontWeight: 600, color: "rgba(255,255,255,0.95)", fontVariantNumeric: "tabular-nums", lineHeight: 1 }}>
              {error || !agents ? "—" : total}
            </span>
            {!error && agents && (
              <span style={{ fontSize: "0.7rem", color: "rgba(255,255,255,0.4)" }}>
                {running} running
              </span>
            )}
          </div>
          {!error && agents && agents.slice(0, 2).map((agent) => (
            <div key={agent.name} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.78rem", color: "rgba(255,255,255,0.8)", marginBottom: 4 }}>
              <PulseDot color={dotColor(agent.status)} />
              <span aria-hidden="true" style={{ fontSize: "0.95rem", lineHeight: 1 }}>
                {resolveAgentEmoji(agent.emoji, agent.framework)}
              </span>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>{agent.name}</span>
              <span style={dimText}>{agent.status}</span>
            </div>
          ))}
          {error && <span style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.35)" }}>Unavailable</span>}
          {!agents && !error && <span style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.35)" }}>Loading…</span>}
        </>
      )}

      {tier === "l" && (
        <>
          <div style={labelStyle}>Agents</div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 6, margin: "4px 0 10px" }}>
            <span style={{ fontSize: "1.8rem", fontWeight: 600, color: "rgba(255,255,255,0.95)", fontVariantNumeric: "tabular-nums", lineHeight: 1 }}>
              {error || !agents ? "—" : total}
            </span>
            {!error && agents && (
              <span style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.4)" }}>
                {running} running · {total - running} idle
              </span>
            )}
          </div>
          {error || !agents ? (
            <span style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.35)" }}>{error ? "Agents unavailable" : "Loading…"}</span>
          ) : agents.length === 0 ? (
            <span style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.35)" }}>No agents configured</span>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 5, overflowY: "auto", flex: 1 }}>
              {agents.map((agent) => (
                <div key={agent.name} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "0.8rem", color: "rgba(255,255,255,0.85)" }}>
                  <PulseDot color={dotColor(agent.status)} />
                  <span aria-hidden="true" style={{ fontSize: "0.95rem", lineHeight: 1 }}>
                    {resolveAgentEmoji(agent.emoji, agent.framework)}
                  </span>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>{agent.name}</span>
                  <span style={{ ...dimText, background: "rgba(255,255,255,0.06)", borderRadius: 4, padding: "1px 6px", whiteSpace: "nowrap" }}>
                    {agent.status}
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
