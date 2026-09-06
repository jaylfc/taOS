import { useState, useEffect, useCallback, useRef } from "react";
import { Clapperboard, LayoutGrid } from "lucide-react";
import { CreateView } from "./videostudio/CreateView";
import { LibraryView } from "./videostudio/LibraryView";
import {
  type GeneratedVideo,
  type StudioView,
  MODEL_OPTIONS,
  DEFAULT_MODEL,
  DEFAULT_RESOLUTION,
  DEFAULT_DURATION,
} from "./videostudio/types";

/* ------------------------------------------------------------------ */
/*  Video Studio — shell                                               */
/*                                                                     */
/*  Left icon rail (Create / Library) + the active surface. Shared     */
/*  backend wiring (list / generate / delete) lives here; the views    */
/*  are presentational. Mirrors ImagesApp's shape.                     */
/* ------------------------------------------------------------------ */

const RAIL: { id: StudioView; label: string; icon: typeof Clapperboard }[] = [
  { id: "create", label: "Create", icon: Clapperboard },
  { id: "library", label: "Library", icon: LayoutGrid },
];

function randomSeed(): number {
  return Math.floor(Math.random() * 1_000_000);
}

// Max seed accepted by the backend (matches routes/video.py's
// random.randint(0, 2**32 - 1) range).
const MAX_SEED = 2 ** 32 - 1;

// Generation is an async job now: POST /api/video/generate enqueues and
// returns a job id immediately, and GET /api/video/jobs/{job_id} is polled
// until it reports done/error. POLL_INTERVAL_MS/MAX_POLL_ATTEMPTS bound how
// long the UI keeps polling before giving up (the video still finishes and
// lands in the Library -- the user can find it there).
const POLL_INTERVAL_MS = 2000;
const MAX_POLL_ATTEMPTS = 600; // ~20 minutes at POLL_INTERVAL_MS

function toGeneratedVideo(raw: Record<string, unknown>): GeneratedVideo {
  const filename = (raw.filename as string) ?? "";
  return {
    filename,
    // `raw.path` is the backend's on-disk-style path, not a servable URL --
    // serve videos through the API's filename-based route instead.
    url: filename ? `/api/video/${encodeURIComponent(filename)}` : "",
    prompt: (raw.prompt as string) ?? "",
    model: (raw.model as string) ?? "",
    duration: (raw.duration as number) ?? 0,
    resolution: (raw.resolution as string) ?? "",
    seed: (raw.seed as number) ?? 0,
    sizeBytes: (raw.size_bytes as number) ?? 0,
  };
}

export function VideoStudioApp({ windowId: _windowId }: { windowId: string }) {
  const [view, setView] = useState<StudioView>("create");

  // library / results
  const [videos, setVideos] = useState<GeneratedVideo[]>([]);
  const [loading, setLoading] = useState(true);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [selectedLibraryId, setSelectedLibraryId] = useState<string | null>(
    null,
  );

  // create form
  const [prompt, setPrompt] = useState("");
  const [modelId, setModelId] = useState<string>(DEFAULT_MODEL);
  const [duration, setDuration] = useState(DEFAULT_DURATION);
  const [resolution, setResolution] = useState(DEFAULT_RESOLUTION);
  const [seed, setSeed] = useState("");

  // generation state
  const [generating, setGenerating] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [needsBackend, setNeedsBackend] = useState(false);
  const [latest, setLatest] = useState<GeneratedVideo | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollAbortRef = useRef<AbortController | null>(null);

  /* ----------------------------- videos --------------------------- */

  const fetchVideos = useCallback(async () => {
    try {
      const res = await fetch("/api/video", {
        headers: { Accept: "application/json" },
      });
      if (res.ok) {
        const data = await res.json();
        const list = Array.isArray(data?.videos) ? data.videos : [];
        setVideos(list.map(toGeneratedVideo));
        setLibraryError(null);
        setLoading(false);
        return;
      }
      setLibraryError(`Failed to load videos (${res.status})`);
    } catch (e) {
      setLibraryError(`Failed to load videos: ${(e as Error).message}`);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchVideos();
  }, [fetchVideos]);

  const modelMeta =
    MODEL_OPTIONS.find((m) => m.id === modelId)?.label ?? modelId;

  const canGenerate = !!prompt.trim() && !generating;

  /* ----------------------------- generate ------------------------- */

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    pollAbortRef.current?.abort();
    pollAbortRef.current = null;
  }, []);

  const finishGenerating = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    stopPolling();
    setGenerating(false);
  }, [stopPolling]);

  const runGenerate = useCallback(async () => {
    const usePrompt = prompt.trim();
    if (!usePrompt) return;

    setGenerating(true);
    setGenerateError(null);
    setNeedsBackend(false);
    setElapsedSeconds(0);
    setLatest(null);
    stopPolling();
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = setInterval(() => {
      setElapsedSeconds((s) => s + 1);
    }, 1000);

    const body: Record<string, unknown> = {
      prompt: usePrompt,
      model: modelId,
      duration,
      resolution,
    };
    const trimmedSeed = seed.trim();
    if (trimmedSeed) {
      const parsedSeed = parseInt(trimmedSeed, 10);
      if (!Number.isNaN(parsedSeed) && parsedSeed >= 0) {
        body.seed = Math.min(parsedSeed, MAX_SEED);
      }
    }

    try {
      const res = await fetch("/api/video/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));

      if (!res.ok || !data.job_id) {
        const message =
          (data as { error?: string }).error ??
          `Generation failed (${res.status})`;
        setGenerateError(message);
        // The backend returns 503 specifically when no video backend is
        // configured or reachable -- that's the "install a backend" case.
        setNeedsBackend(res.status === 503);
        finishGenerating();
        return;
      }

      const jobId = data.job_id as string;
      const abortController = new AbortController();
      pollAbortRef.current = abortController;
      let attempts = 0;

      const poll = async () => {
        attempts += 1;
        try {
          const statusRes = await fetch(
            `/api/video/jobs/${encodeURIComponent(jobId)}`,
            { headers: { Accept: "application/json" }, signal: abortController.signal },
          );
          const statusData = await statusRes.json().catch(() => ({}));

          if (!statusRes.ok) {
            setGenerateError(
              (statusData as { error?: string }).error ??
                `Job status failed (${statusRes.status})`,
            );
            finishGenerating();
            return;
          }

          if (statusData.status === "done") {
            const video = toGeneratedVideo(statusData.result ?? {});
            setLatest(video);
            await fetchVideos();
            setSelectedLibraryId(video.filename);
            finishGenerating();
            return;
          }

          if (statusData.status === "error") {
            setGenerateError(statusData.error ?? "Video generation failed");
            finishGenerating();
            return;
          }

          // Still queued/running -- keep polling until the attempt cap.
          if (attempts >= MAX_POLL_ATTEMPTS) {
            setGenerateError(
              "Video generation is taking longer than expected. Check the Library later.",
            );
            finishGenerating();
          }
        } catch (e) {
          if ((e as Error).name === "AbortError") return;
          setGenerateError(`Generation error: ${(e as Error).message}`);
          finishGenerating();
        }
      };

      await poll();
      // Only arm the interval if the first poll didn't already resolve
      // (finishGenerating clears pollAbortRef.current on any terminal state).
      if (pollAbortRef.current) {
        pollTimerRef.current = setInterval(poll, POLL_INTERVAL_MS);
      }
    } catch (e) {
      setGenerateError(`Generation error: ${(e as Error).message}`);
      setNeedsBackend(false);
      finishGenerating();
    }
  }, [prompt, modelId, duration, resolution, seed, fetchVideos, stopPolling, finishGenerating]);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      stopPolling();
    };
  }, [stopPolling]);

  const handleReroll = useCallback(() => {
    setSeed(String(randomSeed()));
  }, []);

  /* ----------------------------- delete ---------------------------- */

  const handleDelete = useCallback((filename: string) => {
    if (!window.confirm(`Delete "${filename}"? This can't be undone.`)) return;
    let removed: GeneratedVideo | undefined;
    let removedIndex = -1;
    setVideos((prev) => {
      removedIndex = prev.findIndex((v) => v.filename === filename);
      removed = prev[removedIndex];
      return prev.filter((v) => v.filename !== filename);
    });
    setSelectedLibraryId((cur) => (cur === filename ? null : cur));
    setLatest((cur) => (cur?.filename === filename ? null : cur));

    fetch(`/api/video/${encodeURIComponent(filename)}`, { method: "DELETE" })
      .then((res) => {
        if (!res.ok) throw new Error(`Delete failed (${res.status})`);
      })
      .catch((e) => {
        setVideos((prev) => {
          if (!removed || prev.some((v) => v.filename === filename)) {
            return prev;
          }
          const at = Math.min(removedIndex < 0 ? prev.length : removedIndex, prev.length);
          return [...prev.slice(0, at), removed, ...prev.slice(at)];
        });
        setLibraryError(`Failed to delete video: ${(e as Error).message}`);
      });
  }, []);

  const handleDownload = useCallback((video: GeneratedVideo) => {
    if (!video.url) return;
    const a = document.createElement("a");
    a.href = video.url;
    a.download = video.filename || "video.mp4";
    a.click();
  }, []);

  const openStore = useCallback(() => {
    window.dispatchEvent(
      new CustomEvent("taos:open-app", { detail: { app: "store" } }),
    );
  }, []);

  /* ------------------------------ render ---------------------------- */

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-shell-bg text-shell-text select-none">
      <div className="flex min-h-0 flex-1">
        {/* left rail */}
        <nav
          aria-label="Video Studio views"
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
              modelId={modelId}
              onModelChange={setModelId}
              modelMeta={modelMeta}
              prompt={prompt}
              onPromptChange={setPrompt}
              duration={duration}
              onDurationChange={setDuration}
              resolution={resolution}
              onResolutionChange={setResolution}
              seed={seed}
              onReroll={handleReroll}
              latest={latest}
              generating={generating}
              elapsedSeconds={elapsedSeconds}
              canGenerate={canGenerate}
              onGenerate={() => void runGenerate()}
              error={generateError}
              needsBackend={needsBackend}
              onOpenStore={openStore}
              onOpenLibrary={() => setView("library")}
            />
          )}

          {view === "library" && (
            <LibraryView
              videos={videos}
              loading={loading}
              error={libraryError}
              selectedId={selectedLibraryId}
              onSelect={setSelectedLibraryId}
              onDownload={handleDownload}
              onDelete={handleDelete}
            />
          )}
        </div>
      </div>
    </div>
  );
}
