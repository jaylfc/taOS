import { useState, useEffect, useCallback } from "react";
import { LayoutList, Sparkles, Music2, LayoutGrid, Download, FolderOpen, Loader2 } from "lucide-react";
import { StudioView } from "./musicstudio/StudioView";
import { ComposeView, type ComposedTrack } from "./musicstudio/ComposeView";
import { SoundsView } from "./musicstudio/SoundsView";
import { MixerView } from "./musicstudio/MixerView";
import { LibraryView } from "./musicstudio/LibraryView";
import { useStudioEngine } from "./musicstudio/use-studio-engine";
import { saveSong, exportSongFile } from "./musicstudio/songs-api";
import { exportSongMidiFile } from "./musicstudio/midi-export";
import { exportSongWavFile } from "./musicstudio/wav-export";

type MusicView = "studio" | "compose" | "sounds" | "mixer" | "export" | "library";

const RAIL_MAIN: { id: MusicView; label: string; icon: typeof LayoutList }[] = [
  { id: "studio", label: "Studio", icon: LayoutList },
  { id: "compose", label: "Compose", icon: Sparkles },
  { id: "sounds", label: "Sounds", icon: Music2 },
  { id: "mixer", label: "Mixer", icon: LayoutGrid },
];

export function MusicStudioApp({ windowId: _windowId }: { windowId: string }) {
  const [view, setView] = useState<MusicView>("studio");
  const engine = useStudioEngine();

  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saved" | "error">("idle");

  const [renderingWav, setRenderingWav] = useState(false);
  const [wavError, setWavError] = useState<string | null>(null);
  const [wavNotice, setWavNotice] = useState<string | null>(null);

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

  const handleSave = useCallback(async () => {
    setSaving(true);
    setSaveStatus("idle");
    try {
      const saved = await saveSong(engine.song);
      engine.loadSongRecord(saved);
      setSaveStatus("saved");
    } catch {
      setSaveStatus("error");
    } finally {
      setSaving(false);
    }
  }, [engine]);

  const handleExportWav = useCallback(async () => {
    setRenderingWav(true);
    setWavError(null);
    setWavNotice(null);
    try {
      const result = await exportSongWavFile(engine.song);
      if (result.substitutedTracks.length > 0) {
        setWavNotice(
          `Rendered with synth substitutes for ${result.substitutedTracks.join(", ")} -- sampled instruments can't render offline.`,
        );
      }
    } catch (e) {
      setWavError((e as Error).message || "Couldn't render this song to WAV.");
    } finally {
      setRenderingWav(false);
    }
  }, [engine.song]);

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
            aria-label="Library"
            aria-current={view === "library" ? "page" : undefined}
            onClick={() => setView("library")}
            className={`flex h-[46px] w-[46px] flex-col items-center justify-center gap-0.5 rounded-xl text-[9px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
              view === "library"
                ? "bg-gradient-to-b from-accent/25 to-transparent text-accent"
                : "text-shell-text-tertiary hover:bg-white/10 hover:text-shell-text-secondary"
            }`}
          >
            <FolderOpen size={21} />
            Library
          </button>

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
          {view === "studio" && <StudioView engine={engine} onSave={() => void handleSave()} saving={saving} saveStatus={saveStatus} />}
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
          {view === "sounds" && <SoundsView engine={engine} />}
          {view === "mixer" && <MixerView engine={engine} />}
          {view === "library" && (
            <LibraryView
              onOpenSong={(song) => {
                engine.loadSongRecord(song);
                setView("studio");
              }}
              onCreateNew={() => {
                engine.newSong();
                setView("studio");
              }}
            />
          )}
          {view === "export" && (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 text-center">
              <p className="text-[14px] font-bold">{engine.song.name}</p>
              <p className="max-w-[360px] text-[12.5px] text-shell-text-secondary">
                Export downloads this song as a portable <span className="font-mono">.taosong</span> JSON file --
                tempo, tracks, clips and notes, ready to re-import later.
              </p>
              <div className="flex gap-2.5">
                <button
                  type="button"
                  onClick={() => exportSongFile(engine.song)}
                  className="flex items-center gap-2 rounded-[14px] border-0 px-6 py-3 text-[14px] font-bold text-white"
                  style={{ background: "linear-gradient(135deg, var(--color-accent-strong, #a9b0c2), var(--color-accent, #8b92a3))" }}
                >
                  <Download size={16} />
                  Download .taosong
                </button>
                <button
                  type="button"
                  onClick={() => exportSongMidiFile(engine.song)}
                  className="flex items-center gap-2 rounded-[14px] border border-shell-border bg-shell-surface px-6 py-3 text-[14px] font-bold text-shell-text"
                >
                  <Download size={16} />
                  Download .mid
                </button>
                <button
                  type="button"
                  onClick={() => void handleExportWav()}
                  disabled={renderingWav}
                  aria-busy={renderingWav}
                  className="flex items-center gap-2 rounded-[14px] border border-shell-border bg-shell-surface px-6 py-3 text-[14px] font-bold text-shell-text disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {renderingWav ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
                  {renderingWav ? "Rendering..." : "Download .wav"}
                </button>
              </div>
              <div role="status" aria-live="polite" className="max-w-[360px] text-[12px]">
                {wavError && <p className="text-red-400">{wavError}</p>}
                {wavNotice && <p className="text-shell-text-secondary">{wavNotice}</p>}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
