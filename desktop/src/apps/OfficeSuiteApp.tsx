import { useState } from "react";
import { Sparkles, Type, Grid, Monitor, Database } from "lucide-react";
import { WriteView } from "./officesuite/WriteView";
import { CalcView } from "./officesuite/CalcView";
import { SlidesView } from "./officesuite/SlidesView";

type OfficeView = "write" | "calc" | "slides" | "data";

const RAIL: { id: OfficeView; label: string; icon: typeof Sparkles }[] = [
  { id: "write", label: "Write", icon: Type },
  { id: "calc", label: "Calc", icon: Grid },
  { id: "slides", label: "Slides", icon: Monitor },
  { id: "data", label: "Data", icon: Database },
];

export function OfficeSuiteApp({ windowId: _windowId }: { windowId: string }) {
  const [view, setView] = useState<OfficeView>("write");

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-shell-bg text-shell-text select-none">
      <div className="flex min-h-0 flex-1">
        {/* left rail */}
        <nav
          aria-label="Office Suite views"
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
          <button
            type="button"
            aria-label="Assist"
            className="flex h-[46px] w-[46px] flex-col items-center justify-center gap-0.5 rounded-xl text-[9px] font-semibold text-shell-text-tertiary transition-colors hover:bg-white/10 hover:text-shell-text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <Sparkles size={21} />
            Assist
          </button>
        </nav>

        {/* active surface */}
        <div className="flex min-w-0 flex-1 flex-col">
          {view === "write" && <WriteView />}
          {view === "calc" && <CalcView />}
          {view === "slides" && <SlidesView />}
          {view === "data" && (
            <div className="flex flex-1 items-center justify-center text-[13px] text-shell-text-tertiary">
              Data view coming soon
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
