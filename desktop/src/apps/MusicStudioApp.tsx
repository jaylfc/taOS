import { useState, useEffect, useCallback } from "react";
import { LayoutList, Sparkles, Music2, LayoutGrid, Download } from "lucide-react";
import { StudioView } from "./musicstudio/StudioView";
import { ComposeView, type ComposedTrack } from "./musicstudio/ComposeView";
import { SoundsView } from "./musicstudio/SoundsView";

type MusicView = "studio" | "compose" | "sounds" | "mixer" | "export";

const RAIL_MAIN: { id: MusicView; label: string; icon: typeof LayoutList }[] = [
  { id: "studio", label: "Studio", icon: LayoutList },
  { id: "compose", label: "Compose", icon: Sparkles },
  { id: "sounds", label: "Sounds", icon: Music2 },
  { id: "mixer", label: "Mixer", icon: LayoutGrid },
];

export function MusicStudioApp({ windowId: _windowId }: { windowId: string }) {
  const [view, setView] = useState<MusicView>("studio");

  const [composePrompt, setComposePrompt] = useState("");
  const [composeStyle, setComposeStyle] = useState<string | null>(null);
  const [composeResults, setComposeResults] = useState<ComposedTrack[]>([]);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [backendAvailable, setBackendAvailable] = useState(false);

  const refreshBackendStatus = useCallback(async () => {
    try {
      const res = await fetch("/api/music/status", {
        headers: { Accept: "application/json" },
      });
      if (!res.ok) return false;
      const data = await res.json();
      const available = Boolean(data.available);
      setBackendAvailable(available);
      return available;
    } catch {
      setBackendAvailable(false);
      return false;
    }
  }, []);

  useEffect(() => {
    refreshBackendStatus();
  }, [refreshBackendStatus]);

  useEffect(() => {
    if (view === "compose") {
      refreshBackendStatus();
    }
  }, [view, refreshBackendStatus]);

  const needsBackend = !backendAvailable;
  const canGenerate = !!composePrompt.trim() && !generating && backendAvailable;

  const runCompose = useCallback(async () => {
    const usePrompt = composePrompt.trim();
    if (!usePrompt || !backendAvailable) return;

    setGenerating(true);
    setError(null);

    const styledPrompt = composeStyle
      ? `${usePrompt}, ${composeStyle.toLowerCase()} style`
      : usePrompt;

    try {
      const res = await fetch("/api/music/compose", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: styledPrompt, duration: 10 }),
      });

      const data = await res.json().catch(() => ({}));
      if (res.ok && data.filename) {
        const track: ComposedTrack = {
          id: data.filename as string,
          url: (data.path as string) ?? `/data/workspace/music/generated/${data.filename}`,
          prompt: styledPrompt,
          duration: (data.duration as number) ?? 10,
        };
        setComposeResults((prev) => [track, ...prev]);
      } else {
        setError((data as { error?: string }).error ?? `Generation failed (${res.status})`);
      }
    } catch (e) {
      setError(`Generation error: ${(e as Error).message}`);
    }

    setGenerating(false);
  }, [composePrompt, composeStyle, backendAvailable]);

  const openStore = useCallback(() => {
    window.dispatchEvent(
      new CustomEvent("taos:open-app", { detail: { app: "store" } }),
    );
  }, []);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-shell-bg text-shell-text select-none">
      <div className="flex min-h-0 flex-1">
        <nav
          aria-label="Music Studio views"
          className="flex w-[68px] flex-none flex-col items-center gap-1.5 border-r border-shell-border bg-shell-bg-deep py-3.5"
        >
          {RAIL_MAIN.map((r) => {
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
            aria-label="Export"
            onClick={() => setView("export")}
            className={`flex h-[46px] w-[46px] flex-col items-center justify-center gap-0.5 rounded-xl text-[9px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
              view === "export"
                ? "bg-gradient-to-b from-accent/25 to-transparent text-accent"
                : "text-shell-text-tertiary hover:bg-white/10 hover:text-shell-text-secondary"
            }`}
          >
            <Download size={21} />
            Export
          </button>
        </nav>

        <div className="flex min-w-0 flex-1 flex-col">
          {view === "studio" && <StudioView />}
          {view === "compose" && (
            <ComposeView
              prompt={composePrompt}
              onPromptChange={setComposePrompt}
              style={composeStyle}
              onStyleChange={setComposeStyle}
              results={composeResults}
              generating={generating}
              canGenerate={canGenerate}
              error={error}
              needsBackend={needsBackend}
              onGenerate={runCompose}
              onOpenStore={openStore}
            />
          )}
          {view === "sounds" && <SoundsView />}
          {view === "mixer" && (
            <div className="flex flex-1 items-center justify-center text-shell-text-secondary text-[13px]">
              Mixer coming soon
            </div>
          )}
          {view === "export" && (
            <div className="flex flex-1 items-center justify-center text-shell-text-secondary text-[13px]">
              Export coming soon
            </div>
          )}
        </div>
      </div>
    </div>
  );
}