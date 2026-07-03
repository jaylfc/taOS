import { useEffect, useState } from "react";
import { Folder, Bell, Users, Shield, Sparkles, Upload, Share2, Download, CheckSquare } from "lucide-react";
import { analyzeAppSource, type Finding } from "./analyze-source";
import { FindingsPanel } from "./FindingsPanel";

/* ------------------------------------------------------------------ */
/*  PublishView -- app identity + capabilities + side publish panel    */
/* ------------------------------------------------------------------ */

// Placeholder source for the "Chore Quest" demo app shown throughout App
// Studio. App Studio does not yet persist the agent's generated files (the
// Build view only streams a chat transcript, see BuildView.tsx) -- once that
// pipeline lands, this should be replaced with the actual generated source
// for the app being published. Wiring the analyzer against this in the
// meantime keeps the publish-time security gate exercised end to end: the
// backend runs the exact same analyze_app_source() call it runs on the real
// install/publish path.
const DEMO_APP_SOURCE: Record<string, string> = {
  "index.html": [
    "<!doctype html>",
    "<html>",
    "  <head><title>Chore Quest</title></head>",
    "  <body>",
    '    <div id="app"></div>',
    '    <script src="app.js"></script>',
    "  </body>",
    "</html>",
  ].join("\n"),
  "app.js": [
    "const chores = [",
    '  { who: "Mara", task: "Take out the bins", done: true },',
    '  { who: "Ben", task: "Walk the dog", done: false },',
    "];",
    "",
    "function render() {",
    '  const root = document.getElementById("app");',
    '  const list = document.createElement("ul");',
    "  chores.forEach((c) => {",
    '    const item = document.createElement("li");',
    "    item.textContent = `${c.who}: ${c.task}`;",
    "    list.appendChild(item);",
    "  });",
    "  root.replaceChildren(list);",
    "}",
    "",
    "render();",
  ].join("\n"),
};

interface PermRow {
  key: string;
  icon: React.ElementType;
  label: string;
  desc: string;
  defaultOn: boolean;
}

const PERMS: PermRow[] = [
  {
    key: "workspace",
    icon: Folder,
    label: "Workspace files",
    desc: "Read and write the app's own folder",
    defaultOn: true,
  },
  {
    key: "notifications",
    icon: Bell,
    label: "Notifications",
    desc: "Send reminders for due chores",
    defaultOn: true,
  },
  {
    key: "household",
    icon: Users,
    label: "Household members",
    desc: "See names and avatars of your people",
    defaultOn: false,
  },
];

export function PublishView() {
  const [enabled, setEnabled] = useState<Record<string, boolean>>(
    Object.fromEntries(PERMS.map((p) => [p.key, p.defaultOn]))
  );
  const [findings, setFindings] = useState<Finding[]>([]);
  const [scanning, setScanning] = useState(true);
  const [scanError, setScanError] = useState<string | null>(null);

  const toggle = (key: string) =>
    setEnabled((prev) => ({ ...prev, [key]: !prev[key] }));

  useEffect(() => {
    let cancelled = false;
    setScanning(true);
    setScanError(null);
    analyzeAppSource(DEMO_APP_SOURCE)
      .then((result) => {
        if (cancelled) return;
        setFindings(result.findings);
      })
      .catch((e) => {
        if (cancelled) return;
        setScanError(String(e instanceof Error ? e.message : e));
      })
      .finally(() => {
        if (!cancelled) setScanning(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const blocked = !scanning && !scanError && findings.some((f) => f.severity === "critical");
  const publishDisabled = scanning || blocked;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* view header */}
      <div className="flex h-[54px] flex-none items-center gap-3 border-b border-shell-border px-[22px]">
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Publish</h2>
        <span className="text-[12px] text-shell-text-tertiary">Review, set permissions, share</span>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* main */}
        <div className="min-w-0 flex-1 overflow-auto p-6">
          <div className="mx-auto max-w-[560px]">
            {/* app identity */}
            <div className="mb-[22px] flex items-center gap-[15px]">
              <div
                className="flex h-[62px] w-[62px] flex-none items-center justify-center rounded-[16px] text-white shadow-[0_8px_22px_rgba(0,0,0,0.35)]"
                style={{ background: "linear-gradient(135deg,#6f7687,#474d5e)" }}
              >
                <CheckSquare size={30} />
              </div>
              <div>
                <div className="flex items-center gap-[8px]">
                  <span className="text-[19px] font-extrabold tracking-[-0.02em]">Chore Quest</span>
                  <span
                    data-testid="provenance-badge"
                    data-provenance="ai-generated"
                    className="flex items-center gap-1 rounded-full border border-shell-border bg-shell-surface px-[8px] py-[2px] text-[10px] font-semibold text-shell-text-secondary"
                  >
                    <Sparkles size={11} />
                    AI-generated
                  </span>
                </div>
                <div className="mt-[3px] text-[12.5px] text-shell-text-secondary">
                  A weekly chore tracker with points and a family leaderboard.
                </div>
              </div>
            </div>

            {/* security scan panel */}
            <FindingsPanel loading={scanning} error={scanError} findings={findings} />

            {/* capabilities section */}
            <div className="mb-[11px] text-[11px] font-bold uppercase tracking-[0.06em] text-shell-text-tertiary">
              Capabilities
            </div>

            {PERMS.map((p) => {
              const Icon = p.icon;
              const on = enabled[p.key];
              return (
                <div
                  key={p.key}
                  className="mb-[9px] flex items-center gap-3 rounded-[13px] border border-shell-border bg-shell-surface p-[13px_14px]"
                  data-testid={`perm-row-${p.key}`}
                >
                  <div className="flex h-[34px] w-[34px] items-center justify-center rounded-[10px] bg-shell-surface-active text-accent">
                    <Icon size={17} />
                  </div>
                  <div>
                    <div className="text-[13px] font-semibold">{p.label}</div>
                    <div className="mt-[1px] text-[11px] text-shell-text-tertiary">{p.desc}</div>
                  </div>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={on}
                    aria-label={`Toggle ${p.label}`}
                    onClick={() => toggle(p.key)}
                    className="relative ml-auto h-[21px] w-[38px] flex-none rounded-full transition-colors"
                    style={{ background: on ? "var(--color-accent)" : undefined }}
                  >
                    {!on && (
                      <span className="block h-full w-full rounded-full bg-shell-surface-active" />
                    )}
                    <span
                      className="absolute top-[2px] h-[17px] w-[17px] rounded-full bg-white transition-[left]"
                      style={{ left: on ? "19px" : "2px" }}
                    />
                  </button>
                </div>
              );
            })}

            {/* safety note */}
            <div
              className="mt-2 flex gap-[10px] rounded-[13px] border p-[13px_15px]"
              style={{
                background: "rgba(95,191,120,0.08)",
                borderColor: "rgba(95,191,120,0.25)",
              }}
            >
              <Shield size={17} className="mt-[1px] flex-none" style={{ color: "#5fbf78" }} />
              <p className="text-[12px] leading-relaxed text-shell-text-secondary">
                Runs sandboxed with no network access. It can only touch what you grant above, and
                you can change these any time.
              </p>
            </div>
          </div>
        </div>

        {/* side panel */}
        <div className="flex w-[280px] flex-none flex-col gap-[13px] border-l border-shell-border bg-shell-bg-deep p-[22px_20px]">
          {/* preview tile */}
          <div
            className="flex aspect-[16/10] items-center justify-center overflow-hidden rounded-[13px] border border-shell-border font-bold text-white"
            style={{ background: "linear-gradient(140deg,#2c3142,#171a24)" }}
          >
            Chore Quest
          </div>

          <button
            type="button"
            disabled={publishDisabled}
            aria-disabled={publishDisabled}
            title={blocked ? "Fix the critical security findings before publishing" : undefined}
            className="flex h-[46px] items-center justify-center gap-[9px] rounded-[13px] text-[13.5px] font-bold text-white shadow-[0_8px_22px_-8px_rgba(139,146,163,0.35)] disabled:cursor-not-allowed disabled:opacity-50"
            style={{ background: "linear-gradient(135deg,var(--color-accent),var(--color-accent))" }}
          >
            <Upload size={16} />
            {blocked ? "Blocked by security scan" : "Publish to my Store"}
          </button>

          <button
            type="button"
            className="flex h-[46px] items-center justify-center gap-[9px] rounded-[13px] border border-shell-border bg-shell-surface text-[13.5px] font-bold text-shell-text"
          >
            <Share2 size={16} />
            Share with family
          </button>

          <button
            type="button"
            className="flex h-[46px] items-center justify-center gap-[9px] rounded-[13px] border border-shell-border bg-shell-surface text-[13.5px] font-bold text-shell-text"
          >
            <Download size={16} />
            Export package
          </button>

          <p className="text-center text-[11px] leading-relaxed text-shell-text-tertiary">
            Community submissions are reviewed before they appear in the public Store.
          </p>
        </div>
      </div>
    </div>
  );
}
