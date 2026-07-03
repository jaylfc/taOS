import { AlertTriangle, CheckCircle2, Info, Loader2, ShieldAlert } from "lucide-react";
import type { Finding, Severity } from "./analyze-source";

/* ------------------------------------------------------------------ */
/*  FindingsPanel -- static security scan results for App Studio       */
/*                                                                     */
/*  Shows the server-side analyze_app_source() results before publish: */
/*  loading / error / clean states, a blocked banner when any finding  */
/*  is critical, and the full list grouped by severity with file:line. */
/* ------------------------------------------------------------------ */

const SEVERITY_META: Record<Severity, { icon: React.ElementType; color: string; label: string }> = {
  critical: { icon: ShieldAlert, color: "#e5484d", label: "Critical" },
  warning: { icon: AlertTriangle, color: "#f5a623", label: "Warning" },
  info: { icon: Info, color: "#7c8ba1", label: "Info" },
};

interface FindingsPanelProps {
  loading: boolean;
  error: string | null;
  findings: Finding[];
}

export function FindingsPanel({ loading, error, findings }: FindingsPanelProps) {
  if (loading) {
    return (
      <div
        className="mb-[18px] flex items-center gap-[10px] rounded-[13px] border border-shell-border bg-shell-surface p-[13px_15px] text-[12px] text-shell-text-secondary"
        data-testid="security-findings-loading"
      >
        <Loader2 size={15} className="animate-spin text-accent" />
        Scanning app source for security issues...
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="mb-[18px] rounded-[13px] border border-red-500/30 bg-red-500/10 p-[13px_15px] text-[12px] text-red-300"
        role="alert"
        data-testid="security-findings-error"
      >
        Could not run the security scan: {error}
      </div>
    );
  }

  const critical = findings.filter((f) => f.severity === "critical");
  const warnings = findings.filter((f) => f.severity === "warning");
  const infos = findings.filter((f) => f.severity === "info");
  const blocked = critical.length > 0;

  if (findings.length === 0) {
    return (
      <div
        className="mb-[18px] flex gap-[10px] rounded-[13px] border p-[13px_15px]"
        style={{ background: "rgba(95,191,120,0.08)", borderColor: "rgba(95,191,120,0.25)" }}
        data-testid="security-findings-clean"
      >
        <CheckCircle2 size={17} className="mt-[1px] flex-none" style={{ color: "#5fbf78" }} />
        <p className="text-[12px] leading-relaxed text-shell-text-secondary">
          No security issues found. Ready to publish.
        </p>
      </div>
    );
  }

  return (
    <div className="mb-[18px]" data-testid="security-findings-panel">
      <div className="mb-[11px] flex items-center justify-between">
        <div className="text-[11px] font-bold uppercase tracking-[0.06em] text-shell-text-tertiary">
          Security scan
        </div>
        <div className="text-[11px] text-shell-text-tertiary">
          {critical.length} critical &middot; {warnings.length} warning{warnings.length === 1 ? "" : "s"}
        </div>
      </div>

      {blocked && (
        <div
          className="mb-[11px] flex items-center gap-[10px] rounded-[13px] border border-red-500/30 bg-red-500/10 p-[13px_15px]"
          role="alert"
          data-testid="security-findings-blocked"
        >
          <ShieldAlert size={17} className="mt-[1px] flex-none text-red-400" />
          <p className="text-[12px] leading-relaxed text-red-300">
            Publish is blocked: {critical.length} critical finding{critical.length === 1 ? "" : "s"} must be
            fixed first.
          </p>
        </div>
      )}

      <div className="flex flex-col gap-[7px]">
        {[...critical, ...warnings, ...infos].map((f, i) => {
          const meta = SEVERITY_META[f.severity];
          const Icon = meta.icon;
          return (
            <div
              key={`${f.rule_id}-${f.file}-${f.line}-${i}`}
              className="flex items-start gap-[10px] rounded-[11px] border border-shell-border bg-shell-surface p-[10px_12px]"
              data-testid={`finding-${f.severity}`}
            >
              <Icon size={15} className="mt-[1px] flex-none" style={{ color: meta.color }} />
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-[7px] text-[11.5px] font-semibold">
                  <span style={{ color: meta.color }}>{meta.label}</span>
                  <span className="text-shell-text-tertiary">
                    {f.file}:{f.line}
                  </span>
                </div>
                <div className="mt-[2px] text-[11.5px] leading-relaxed text-shell-text-secondary">
                  {f.message}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
