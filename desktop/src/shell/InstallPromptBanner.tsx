import { useEffect, useState } from "react";
import { Share } from "lucide-react";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { isIOS, isStandalone } from "@/lib/platform";

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<{ outcome: "accepted" | "dismissed" }>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

const DISMISS_MS = 30 * 24 * 60 * 60 * 1000;
const KEY = "taos-install-dismissed";

export function InstallPromptBanner() {
  const isMobile = useIsMobile();
  const [event, setEvent] = useState<BeforeInstallPromptEvent | null>(null);
  const [ios, setIos] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    const onPrompt = (e: Event) => {
      e.preventDefault();
      setEvent(e as BeforeInstallPromptEvent);
    };
    window.addEventListener("beforeinstallprompt", onPrompt);
    // iOS Safari never fires beforeinstallprompt and has no programmatic
    // install, so detect it and show a manual Add to Home Screen instruction.
    if (isIOS() && !isStandalone()) setIos(true);
    return () => window.removeEventListener("beforeinstallprompt", onPrompt);
  }, []);

  if (dismissed || isStandalone()) return null;
  // Android shows on mobile via the install event; iOS shows on the device
  // regardless of viewport width (iPadOS reports as desktop).
  if (!isMobile && !ios) return null;

  const prev = localStorage.getItem(KEY);
  if (prev && Date.now() - Number(prev) < DISMISS_MS) return null;

  const dismiss = () => {
    localStorage.setItem(KEY, String(Date.now()));
    setDismissed(true);
  };

  // Android Chrome: a real install prompt is available.
  if (event) {
    const install = async () => {
      try {
        await event.prompt();
        await event.userChoice;
      } catch {
        /* ignore */
      }
      setEvent(null);
    };
    return (
      <div
        role="region"
        aria-label="Install prompt"
        className="flex items-center gap-3 px-4 py-2 bg-sky-500/20 border-b border-sky-500/30 text-sm"
      >
        <span className="flex-1">Install taOS for quick access</span>
        <button
          onClick={install}
          className="px-3 py-1 bg-sky-500/40 text-sky-100 rounded hover:bg-sky-500/60"
        >Install</button>
        <button
          onClick={dismiss}
          className="px-2 py-1 opacity-70 hover:opacity-100"
        >Not now</button>
      </div>
    );
  }

  // iOS Safari: no programmatic install, so instruct the manual gesture.
  if (ios) {
    return (
      <div
        role="region"
        aria-label="Install instructions"
        className="flex items-center gap-2 px-4 py-2 bg-sky-500/20 border-b border-sky-500/30 text-sm"
      >
        <span className="flex-1 inline-flex items-center gap-1 flex-wrap">
          To install, tap
          <Share size={14} className="inline align-text-bottom" aria-label="Share" />
          then "Add to Home Screen".
        </span>
        <button
          onClick={dismiss}
          className="px-2 py-1 opacity-70 hover:opacity-100"
        >Got it</button>
      </div>
    );
  }

  return null;
}
