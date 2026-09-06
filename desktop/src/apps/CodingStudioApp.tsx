import { useState } from "react";
import { Sparkles, Code2, Play, LayoutGrid, Settings2 } from "lucide-react";
import { BuildView, type Workspace } from "./codingstudio/BuildView";
import { CodeView } from "./codingstudio/CodeView";
import { TemplatesView } from "./codingstudio/TemplatesView";
import { PreviewView } from "./codingstudio/PreviewView";

type CodingView = "build" | "code" | "preview" | "templates";

const RAIL: { id: CodingView; label: string; icon: typeof Sparkles }[] = [
  { id: "build", label: "Build", icon: Sparkles },
  { id: "code", label: "Code", icon: Code2 },
  { id: "preview", label: "Preview", icon: Play },
  { id: "templates", label: "Templates", icon: LayoutGrid },
];

export function CodingStudioApp({ windowId: _windowId }: { windowId: string }) {
  const [view, setView] = useState<CodingView>("build");
  const [activeWorkspace, setActiveWorkspace] = useState<Workspace | null>(null);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-shell-bg text-shell-text select-none">
      <div className="flex min-h-0 flex-1">
        {/* left rail */}
        <nav
          aria-label="Coding Studio views"
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
            aria-label="Models"
            className="flex h-[46px] w-[46px] flex-col items-center justify-center gap-0.5 rounded-xl text-[9px] font-semibold text-shell-text-tertiary transition-colors hover:bg-white/10 hover:text-shell-text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <Settings2 size={21} />
            Models
          </button>
        </nav>

        {/* active surface */}
        <div className="flex min-w-0 flex-1 flex-col">
          {view === "build" && <BuildView onActiveWorkspaceChange={setActiveWorkspace} />}
          {view === "code" && <CodeView />}
          {view === "preview" && (
            <PreviewView
              workspaceId={activeWorkspace?.id ?? null}
              workspaceName={activeWorkspace?.name}
            />
          )}
          {view === "templates" && <TemplatesView />}
        </div>
      </div>
    </div>
  );
}
