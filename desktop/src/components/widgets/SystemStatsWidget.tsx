import { useState, useEffect } from "react";
import { useWidgetSize } from "@/hooks/use-widget-size";

interface Stats {
  cpu: number;
  ram: number;
}

function barColor(v: number): string {
  if (v >= 80) return "#f87171";
  if (v >= 50) return "#fbbf24";
  return "#4ade80";
}

function Bar({ label, value }: { label: string; value: number }) {
  const color = barColor(value);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.72rem", color: "rgba(255,255,255,0.65)" }}>
        <span>{label}</span>
        <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 600, color: "rgba(255,255,255,0.85)" }}>
          {Math.round(value)}%
        </span>
      </div>
      <div
        style={{ height: 5, borderRadius: 3, background: "rgba(255,255,255,0.08)", overflow: "hidden" }}
        role="progressbar"
        aria-label={`${label} usage`}
        aria-valuenow={Math.round(value)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          style={{
            height: "100%",
            width: `${Math.min(100, Math.max(0, value))}%`,
            borderRadius: 3,
            background: color,
            transition: "width 0.4s ease, background 0.4s ease",
            boxShadow: `0 0 6px ${color}66`,
          }}
        />
      </div>
    </div>
  );
}

function MiniGauge({ label, value }: { label: string; value: number }) {
  const color = barColor(value);
  const r = 18;
  const circ = 2 * Math.PI * r;
  const pct = Math.min(100, Math.max(0, value));
  const dash = (pct / 100) * circ;
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 3, flex: 1 }}>
      <div style={{ position: "relative", width: 48, height: 48 }}>
        <svg width="48" height="48" viewBox="0 0 48 48" aria-hidden="true">
          <circle cx="24" cy="24" r={r} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="4" />
          <circle
            cx="24" cy="24" r={r} fill="none"
            stroke={color} strokeWidth="4"
            strokeDasharray={`${dash} ${circ - dash}`}
            strokeLinecap="round"
            transform="rotate(-90 24 24)"
            style={{ transition: "stroke-dasharray 0.4s ease, stroke 0.4s ease", filter: `drop-shadow(0 0 3px ${color}99)` }}
          />
        </svg>
        <span
          style={{
            position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: "0.65rem", fontWeight: 600, color: "rgba(255,255,255,0.85)", fontVariantNumeric: "tabular-nums",
          }}
        >
          {Math.round(value)}
        </span>
      </div>
      <span style={{ fontSize: "0.6rem", fontWeight: 600, color: "rgba(255,255,255,0.4)", textTransform: "uppercase", letterSpacing: "0.06em" }}>{label}</span>
    </div>
  );
}

async function fetchJson<T>(url: string): Promise<T | null> {
  try {
    const res = await fetch(url, { headers: { Accept: "application/json" } });
    if (!res.ok) return null;
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

export function SystemStatsWidget() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState(false);
  const [containerRef, { tier }] = useWidgetSize();

  useEffect(() => {
    let cancelled = false;

    async function fetchStats() {
      const sys = await fetchJson<{ resources?: { cpu_percent?: number; ram_percent?: number } }>("/api/system");

      if (sys?.resources) {
        if (cancelled) return;
        setStats({ cpu: sys.resources.cpu_percent ?? 0, ram: sys.resources.ram_percent ?? 0 });
        setError(false);
        return;
      }

      const [cpuSeries, ramSeries] = await Promise.all([
        fetchJson<Array<{ value: number }>>("/api/metrics/system.cpu_pct?range=1h"),
        fetchJson<Array<{ value: number }>>("/api/metrics/system.ram_pct?range=1h"),
      ]);

      if (cancelled) return;
      const cpu = Array.isArray(cpuSeries) && cpuSeries.length > 0 ? cpuSeries[cpuSeries.length - 1]?.value ?? null : null;
      const ram = Array.isArray(ramSeries) && ramSeries.length > 0 ? ramSeries[ramSeries.length - 1]?.value ?? null : null;

      if (cpu === null && ram === null) { setError(true); return; }
      setStats({ cpu: cpu ?? 0, ram: ram ?? 0 });
      setError(false);
    }

    fetchStats();
    const interval = setInterval(fetchStats, 5000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  const labelStyle: React.CSSProperties = {
    fontSize: "0.65rem", fontWeight: 600, color: "rgba(255,255,255,0.4)",
    textTransform: "uppercase", letterSpacing: "0.06em",
  };

  const empty = error || !stats;
  const emptyText = error ? "Stats unavailable" : "Loading…";

  return (
    <div
      ref={containerRef}
      style={{ height: "100%", display: "flex", flexDirection: "column", padding: tier === "s" ? "0 4px" : "2px 4px 6px", overflow: "hidden" }}
      aria-label="System stats widget"
      role="region"
    >
      {tier === "s" && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", gap: 16 }}>
          {empty ? (
            <span style={{ fontSize: "0.7rem", color: "rgba(255,255,255,0.35)" }}>{emptyText}</span>
          ) : (
            <>
              <MiniGauge label="CPU" value={stats!.cpu} />
              <MiniGauge label="RAM" value={stats!.ram} />
            </>
          )}
        </div>
      )}

      {tier === "m" && (
        <>
          <div style={labelStyle}>System</div>
          <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center", gap: 10, marginTop: 8 }}>
            {empty ? (
              <span style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.35)" }}>{emptyText}</span>
            ) : (
              <>
                <Bar label="CPU" value={stats!.cpu} />
                <Bar label="RAM" value={stats!.ram} />
              </>
            )}
          </div>
        </>
      )}

      {tier === "l" && (
        <>
          <div style={labelStyle}>System</div>
          <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center", gap: 12, marginTop: 10 }}>
            {empty ? (
              <span style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.35)" }}>{emptyText}</span>
            ) : (
              <>
                <Bar label="CPU" value={stats!.cpu} />
                <Bar label="RAM" value={stats!.ram} />
              </>
            )}
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
            {!empty && (
              <>
                <div style={{ flex: 1, background: "rgba(255,255,255,0.04)", borderRadius: 8, padding: "6px 8px" }}>
                  <div style={{ fontSize: "0.6rem", color: "rgba(255,255,255,0.35)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 2 }}>CPU</div>
                  <div style={{ fontSize: "1rem", fontWeight: 600, color: "rgba(255,255,255,0.9)", fontVariantNumeric: "tabular-nums" }}>{Math.round(stats!.cpu)}%</div>
                </div>
                <div style={{ flex: 1, background: "rgba(255,255,255,0.04)", borderRadius: 8, padding: "6px 8px" }}>
                  <div style={{ fontSize: "0.6rem", color: "rgba(255,255,255,0.35)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 2 }}>RAM</div>
                  <div style={{ fontSize: "1rem", fontWeight: 600, color: "rgba(255,255,255,0.9)", fontVariantNumeric: "tabular-nums" }}>{Math.round(stats!.ram)}%</div>
                </div>
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}
