import { useState, useEffect } from "react";
import { Sun, CloudSun, Sunset, Moon, type LucideIcon } from "lucide-react";
import { useWidgetSize } from "@/hooks/use-widget-size";

function getGreeting(hour: number): string {
  if (hour >= 5 && hour < 12) return "Good morning.";
  if (hour >= 12 && hour < 17) return "Good afternoon.";
  if (hour >= 17 && hour < 21) return "Good evening.";
  return "Good night.";
}

// Lucide SVG icons rather than emoji: emoji glyphs (esp. 🌆) render as a
// tofu/box on iOS where the font lacks them. SVGs are font-independent and
// match the rest of the app's icon system.
function getGreetingIcon(hour: number): LucideIcon {
  if (hour >= 5 && hour < 12) return Sun;
  if (hour >= 12 && hour < 17) return CloudSun;
  if (hour >= 17 && hour < 21) return Sunset;
  return Moon;
}

interface SystemSummary {
  agentCount: number;
  taskCount: number;
}

async function fetchSummary(): Promise<SystemSummary | null> {
  try {
    const [agentsRes, jobsRes] = await Promise.all([fetch("/api/agents"), fetch("/api/jobs")]);
    if (!agentsRes.ok || !jobsRes.ok) return null;
    const agents = await agentsRes.json();
    const jobs = await jobsRes.json();
    const agentCount = Array.isArray(agents) ? agents.length : (agents?.count ?? 0);
    const taskCount = Array.isArray(jobs) ? jobs.length : (jobs?.count ?? 0);
    return { agentCount, taskCount };
  } catch {
    return null;
  }
}

export function GreetingWidget() {
  const [now, setNow] = useState(() => new Date());
  const [summary, setSummary] = useState<SystemSummary | null>(null);
  const [containerRef, { tier }] = useWidgetSize();

  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    fetchSummary().then(setSummary);
    const timer = setInterval(() => fetchSummary().then(setSummary), 30_000);
    return () => clearInterval(timer);
  }, []);

  const hour = now.getHours();
  const greeting = getGreeting(hour);
  const Icon = getGreetingIcon(hour);

  const subtitleParts: string[] = [];
  if (summary !== null) {
    if (summary.agentCount > 0) subtitleParts.push(`${summary.agentCount} agent${summary.agentCount !== 1 ? "s" : ""} running`);
    if (summary.taskCount > 0) subtitleParts.push(`${summary.taskCount} task${summary.taskCount !== 1 ? "s" : ""} queued`);
  }
  const subtitleText = subtitleParts.length > 0 ? subtitleParts.join(" · ") : "All systems operational";

  return (
    <div
      ref={containerRef}
      style={{
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
        height: "100%",
        padding: tier === "s" ? "4px 8px" : tier === "m" ? "12px 12px" : "20px 16px",
        gap: tier === "s" ? 4 : 8,
        textAlign: "center",
      }}
      aria-label="Greeting widget"
      role="region"
    >
      {tier !== "s" && (
        <Icon size={tier === "l" ? 36 : 28} color="rgba(255,255,255,0.92)" aria-hidden="true" />
      )}

      <span
        style={{
          fontSize: tier === "s" ? "1rem" : tier === "m" ? "1.3rem" : "1.7rem",
          fontWeight: 600,
          color: "rgba(255,255,255,0.92)",
          lineHeight: 1.2,
          letterSpacing: "-0.02em",
          ...(tier === "s"
            ? { display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6 }
            : {}),
        }}
      >
        {tier === "s" ? (
          // Compact: icon inline with text
          <>
            <Icon size={16} color="rgba(255,255,255,0.92)" aria-hidden="true" />
            {greeting}
          </>
        ) : greeting}
      </span>

      {tier !== "s" && (
        <span style={{ fontSize: tier === "l" ? "0.78rem" : "0.7rem", color: "rgba(255,255,255,0.4)", letterSpacing: "0.01em" }}>
          {subtitleText}
        </span>
      )}
    </div>
  );
}
