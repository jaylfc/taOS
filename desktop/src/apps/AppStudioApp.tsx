import { useEffect, useState } from "react";
import { SHOW_BUILD_VIEW_EVENT } from "./appstudio/build-state";
import { Sparkles, LayoutGrid, Share2, CircleDot } from "lucide-react";
import { BuildView } from "./appstudio/BuildView";
import { TemplatesView } from "./appstudio/TemplatesView";
import { PublishView } from "./appstudio/PublishView";

/* ------------------------------------------------------------------ */
/*  App Studio -- shell                                                 */
/*                                                                     */
/*  Build taOS apps from plain words. An agent generates them against  */
/*  the taOS SDK, sandboxed and safe. Publish to your Store or share   */
/*  with family.                                                        */
/*                                                                     */
/*  Shell follows the canonical studio pattern from GameStudioApp:      */
/*  46px centered titlebar, 68px icon rail, per-view subfolder.        */
/* ------------------------------------------------------------------ */

type AppStudioView = "build" | "templates" | "publish" | "sdk";

const RAIL: { id: AppStudioView; label: string; icon: typeof Sparkles }[] = [
  { id: "build", label: "Build", icon: Sparkles },
  { id: "templates", label: "Templates", icon: LayoutGrid },
  { id: "publish", label: "Publish", icon: Share2 },
];

const RAIL_BOTTOM: { id: AppStudioView; label: string; icon: typeof Sparkles }[] = [
  { id: "sdk", label: "SDK", icon: CircleDot },
];

export function AppStudioApp({ windowId: _windowId }: { windowId: string }) {
  const [view, setView] = useState<AppStudioView>("build");

  useEffect(() => {
    const onShowBuild = () => setView("build");
    window.addEventListener(SHOW_BUILD_VIEW_EVENT, onShowBuild);
    return () => window.removeEventListener(SHOW_BUILD_VIEW_EVENT, onShowBuild);
  }, []);

  function RailButton({ id, label, icon: Icon }: { id: AppStudioView; label: string; icon: typeof Sparkles }) {
    const on = view === id;
    return (
      <button
        key={id}
        type="button"
        aria-label={label}
        aria-current={on ? "page" : undefined}
        onClick={() => setView(id)}
        className={`flex h-[46px] w-[46px] flex-col items-center justify-center gap-0.5 rounded-xl text-[9px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
          on
            ? "bg-gradient-to-b from-accent/25 to-transparent text-accent"
            : "text-shell-text-tertiary hover:bg-white/10 hover:text-shell-text-secondary"
        }`}
      >
        <Icon size={21} />
        {label}
      </button>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-shell-bg text-shell-text select-none">
      <div className="flex min-h-0 flex-1">
        {/* left rail */}
        <nav
          aria-label="App Studio views"
          className="flex w-[68px] flex-none flex-col items-center gap-1.5 border-r border-shell-border bg-shell-bg-deep py-3.5"
        >
          {RAIL.map((r) => (
            <RailButton key={r.id} {...r} />
          ))}
          <div className="flex-1" />
          {RAIL_BOTTOM.map((r) => (
            <RailButton key={r.id} {...r} />
          ))}
        </nav>

        {/* active surface */}
        <div className="flex min-w-0 flex-1 flex-col">
          {view === "build" && <BuildView />}
          {view === "templates" && <TemplatesView />}
          {view === "publish" && <PublishView />}
          {view === "sdk" && (
            <div className="flex flex-1 items-center justify-center text-[13px] text-shell-text-tertiary">
              SDK docs coming soon
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
