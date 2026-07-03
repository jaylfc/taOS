import { useState } from "react";
import { Sparkles, SquareCode, LayoutGrid, Share2 } from "lucide-react";
import { CreateView } from "./gamestudio/CreateView";
import { EditorView } from "./gamestudio/EditorView";
import { LibraryView } from "./gamestudio/LibraryView";
import { ShareView } from "./gamestudio/ShareView";
import type { StudioView } from "./gamestudio/types";

/* ------------------------------------------------------------------ */
/*  Game Studio — an AI-assisted game maker                            */
/*                                                                     */
/*  Left icon rail (Create / Editor / Library / Share) + the active     */
/*  surface, the same shape as Images/Web/Coding Studio. Create really   */
/*  streams the taOS agent to customize a starter template; Editor is    */
/*  a three-pane file editor + live preview + AI chat; Library lists     */
/*  saved games; Share installs or exports a real .taosapp package.      */
/* ------------------------------------------------------------------ */

const RAIL: { id: StudioView; label: string; icon: typeof Sparkles }[] = [
  { id: "create", label: "Create", icon: Sparkles },
  { id: "editor", label: "Editor", icon: SquareCode },
  { id: "library", label: "Library", icon: LayoutGrid },
  { id: "share", label: "Share", icon: Share2 },
];

export function GameStudioApp({ windowId: _windowId }: { windowId: string }) {
  const [view, setView] = useState<StudioView>("create");
  const [activeGameId, setActiveGameId] = useState<string | null>(null);

  const openGame = (gameId: string) => {
    setActiveGameId(gameId);
    setView("editor");
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-shell-bg text-shell-text select-none">
      {/* scoped: custom select chevron matching the rest of the shell */}
      <style>{`
        .gs-select {
          -webkit-appearance: none; appearance: none;
          background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%238b92a3' stroke-width='2.5'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
          background-repeat: no-repeat; background-position: right 12px center; padding-right: 32px;
        }
      `}</style>

      <div className="flex min-h-0 flex-1">
        {/* left rail */}
        <nav
          aria-label="Game Studio views"
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
        </nav>

        {/* active surface */}
        <div className="flex min-w-0 flex-1 flex-col">
          {view === "create" && <CreateView onOpenGame={openGame} />}

          {view === "editor" &&
            (activeGameId ? (
              <EditorView key={activeGameId} gameId={activeGameId} />
            ) : (
              <div className="flex flex-1 flex-col items-center justify-center gap-2 text-shell-text-tertiary">
                <SquareCode size={28} />
                <p className="text-[13px]">Create a game or open one from your Library.</p>
              </div>
            ))}

          {view === "library" && <LibraryView onOpenGame={openGame} />}

          {view === "share" && <ShareView gameId={activeGameId} />}
        </div>
      </div>
    </div>
  );
}
