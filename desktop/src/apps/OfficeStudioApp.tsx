import { useState } from "react";
import { Sparkles, Type, Grid, Table2, Monitor } from "lucide-react";
import { WriteView } from "./officestudio/WriteView";
import { CalcView } from "./officestudio/CalcView";
import { DatabaseView } from "./officestudio/DatabaseView";
import { SlidesView } from "./officestudio/SlidesView";

type OfficeView = "write" | "calc" | "db" | "slides";

const RAIL: { id: OfficeView; label: string; icon: typeof Sparkles }[] = [
  { id: "write", label: "Write", icon: Type },
  { id: "calc", label: "Calc", icon: Grid },
  { id: "db", label: "Database", icon: Table2 },
  { id: "slides", label: "Slides", icon: Monitor },
];

export function OfficeStudioApp({ windowId: _windowId }: { windowId: string }) {
  const [view, setView] = useState<OfficeView>("write");
  const [showAssistHint, setShowAssistHint] = useState(false);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-shell-bg text-shell-text select-none">
      <div className="flex min-h-0 flex-1">
        {/* left rail */}
        <nav
          aria-label="Office Studio views"
          className="flex w-[68px] flex-none flex-col items-center gap-1.5 border-r border-shell-border bg-shell-bg-deep py-3.5"
        >
          {RAIL.map((r) => {
            const Icon = r.icon;
            const on = view === r.id;
            return (
              <button
                key={r.id}
                type="button"
                aria-label={r.label}
                aria-current={on ? "page" : undefined}
                onClick={() => setView(r.id)}
                className={`flex h-[46px] w-[46px] flex-col items-center justify-center gap-0.5 rounded-xl text-[9px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                  on
                    ? "bg-gradient-to-b from-accent/25 to-transparent text-accent"
                    : "text-shell-text-tertiary hover:bg-white/10 hover:text-shell-text-secondary"
                }`}
              >
                <Icon size={21} />
                {r.label}
              </button>
            );
          })}
          <div className="flex-1" />
          <div className="relative">
            <button
              type="button"
              aria-label="Where AI Assist lives"
              aria-expanded={showAssistHint}
              onClick={() => setShowAssistHint((v) => !v)}
              className={`flex h-[46px] w-[46px] flex-col items-center justify-center gap-0.5 rounded-xl text-[9px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                showAssistHint
                  ? "bg-white/10 text-shell-text-secondary"
                  : "text-shell-text-tertiary hover:bg-white/10 hover:text-shell-text-secondary"
              }`}
            >
              <Sparkles size={21} />
              Assist
            </button>
            {showAssistHint && (
              <div
                role="note"
                className="absolute bottom-0 left-[54px] z-10 w-56 rounded-xl border border-shell-border bg-shell-surface p-3 text-[11.5px] leading-[1.45] text-shell-text-secondary shadow-card"
              >
                AI lives right where you write: use the <strong>Assist</strong> buttons in Write,
                and <strong>Ask your data</strong> in Calc.
              </div>
            )}
          </div>
        </nav>

        {/* active surface */}
        <div className="flex min-w-0 flex-1 flex-col">
          {view === "write" && <WriteView />}
          {view === "calc" && <CalcView />}
          {view === "db" && <DatabaseView />}
          {view === "slides" && <SlidesView />}
        </div>
      </div>
    </div>
  );
}
