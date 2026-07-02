import { useState } from "react";
import { Sparkles, Play, Share2 } from "lucide-react";
import { CreateView } from "./gamestudio/CreateView";
import { PlayView } from "./gamestudio/PlayView";
import { ShareView } from "./gamestudio/ShareView";
import { DEFAULT_TEMPLATE } from "./gamestudio/templates";
import { OFFLINE_MODELS, type StudioView, type Template } from "./gamestudio/types";

/* ------------------------------------------------------------------ */
/*  Game Studio — shell (phase 1)                                      */
/*                                                                     */
/*  Make a game from a prompt, test it live, share it to the Store.    */
/*  Left icon rail (Create / Play / Share) + the active surface, the   */
/*  same shape as Images Studio.                                        */
/*                                                                      */
/*  Phase 1 ships the shell + a genuinely-working three.js preview.    */
/*  Offline AI generation, the skill pack and the store-publish        */
/*  backend are later phases and are surfaced as honest "coming"        */
/*  affordances, never faked. Registered as a normal app for now;       */
/*  it should become an optional install in a follow-up (#53).          */
/* ------------------------------------------------------------------ */

const RAIL: { id: StudioView; label: string; icon: typeof Sparkles }[] = [
  { id: "create", label: "Create", icon: Sparkles },
  { id: "play", label: "Play", icon: Play },
  { id: "share", label: "Share", icon: Share2 },
];

export function GameStudioApp({ windowId }: { windowId: string }) {
  const [view, setView] = useState<StudioView>("create");

  // Create-form state (illustrative in phase 1; the template drives Play).
  const [prompt, setPrompt] = useState("");
  const [genres, setGenres] = useState<Set<string>>(new Set(["Platformer"]));
  const [model, setModel] = useState<string>(OFFLINE_MODELS[0]);

  // The template loaded into Play & Test + Share. Defaults to the first so
  // the Play stage always has a real scene to render.
  const [active, setActive] = useState<Template>(DEFAULT_TEMPLATE);

  const toggleGenre = (g: string) =>
    setGenres((prev) => {
      const next = new Set(prev);
      if (next.has(g)) next.delete(g);
      else next.add(g);
      return next;
    });

  const useTemplate = (t: Template) => {
    setActive(t);
    setView("play");
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
          {view === "create" && (
            <CreateView
              windowId={windowId}
              prompt={prompt}
              onPromptChange={setPrompt}
              genres={genres}
              onToggleGenre={toggleGenre}
              model={model}
              onModelChange={setModel}
              onUseTemplate={useTemplate}
            />
          )}

          {view === "play" && <PlayView key={active.id} windowId={windowId} template={active} />}

          {view === "share" && <ShareView template={active} />}
        </div>
      </div>
    </div>
  );
}
