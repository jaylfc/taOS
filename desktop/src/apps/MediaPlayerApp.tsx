import { useEffect, useRef, useState } from "react";
import Plyr from "plyr";
import "plyr/dist/plyr.css";
import { Film, FolderOpen } from "lucide-react";

export function MediaPlayerApp({ windowId: _windowId, url }: { windowId: string; url?: string }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const playerRef = useRef<Plyr | null>(null);
  const [mediaUrl, setMediaUrl] = useState<string | null>(url ?? null);
  const [fileName, setFileName] = useState<string>("");

  useEffect(() => {
    if (!url) return;
    setMediaUrl(url);
    const path = decodeURIComponent(url.split("/").pop() ?? "");
    setFileName(path.split("/").pop() ?? "");
  }, [url]);

  function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;

    if (mediaUrl) URL.revokeObjectURL(mediaUrl);

    const url = URL.createObjectURL(file);
    setMediaUrl(url);
    setFileName(file.name);
  }

  useEffect(() => {
    if (!videoRef.current || !mediaUrl) return;

    playerRef.current = new Plyr(videoRef.current, {
      controls: [
        "play-large",
        "play",
        "progress",
        "current-time",
        "mute",
        "volume",
        "fullscreen",
      ],
    });

    playerRef.current.play().catch(() => {
      // autoplay may be blocked by browser policy
    });

    return () => {
      playerRef.current?.destroy();
      playerRef.current = null;
    };
  }, [mediaUrl]);

  useEffect(() => {
    return () => {
      if (mediaUrl) URL.revokeObjectURL(mediaUrl);
    };
  }, [mediaUrl]);

  return (
    <div className="flex h-full flex-col bg-shell-bg text-shell-text select-none">
      {!mediaUrl ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-5 p-8">
          <div className="flex flex-col items-center gap-4 rounded-2xl border border-shell-border bg-shell-surface px-10 py-9 text-center shadow-[var(--shadow-card)]">
            <span className="flex h-14 w-14 items-center justify-center rounded-2xl border border-shell-border bg-white/5 text-shell-text-tertiary">
              <Film size={26} />
            </span>
            <div className="flex flex-col gap-1">
              <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-shell-text">
                No media loaded
              </h2>
              <p className="text-[12.5px] text-shell-text-secondary">
                Pick a video or audio file to start playing.
              </p>
            </div>
            <label
              className="flex h-9 cursor-pointer items-center justify-center gap-1.5 rounded-xl bg-accent px-4 text-[12px] font-semibold text-white transition-all hover:brightness-105 focus-within:outline-none focus-within:ring-2 focus-within:ring-accent/40"
              aria-label="Choose media file"
            >
              <FolderOpen size={15} />
              Open File
              <input
                type="file"
                accept="video/*,audio/*"
                onChange={handleFileSelect}
                className="hidden"
              />
            </label>
          </div>
        </div>
      ) : (
        <div className="flex h-full min-h-0 flex-col">
          <div className="flex flex-none items-center gap-2 border-b border-shell-border bg-shell-bg-deep px-3 py-2">
            <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-shell-text">
              {fileName}
            </span>
            <label
              className="flex h-7 cursor-pointer items-center gap-1.5 rounded-lg border border-shell-border bg-shell-surface px-2.5 text-[11.5px] font-semibold text-shell-text-secondary transition-colors hover:bg-white/10 hover:text-shell-text hover:border-shell-border-strong focus-within:outline-none focus-within:ring-2 focus-within:ring-accent/40"
              aria-label="Choose a different media file"
            >
              <FolderOpen size={13} />
              Open
              <input
                type="file"
                accept="video/*,audio/*"
                onChange={handleFileSelect}
                className="hidden"
              />
            </label>
          </div>
          <div
            className="flex min-h-0 flex-1 items-center justify-center bg-shell-bg-deep"
            style={{ paddingBottom: "env(safe-area-inset-bottom, 0px)" }}
          >
            <video
              ref={videoRef}
              src={mediaUrl}
              className="max-h-full max-w-full"
              aria-label="Media player"
            />
          </div>
        </div>
      )}
    </div>
  );
}
