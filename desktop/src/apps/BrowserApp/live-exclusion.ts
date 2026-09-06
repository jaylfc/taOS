/**
 * Live-exclusion detection — keeps the discard scheduler from snoozing
 * tabs the user is actively using.
 *
 * Runs from the PARENT window by inspecting iframe.contentDocument.
 * Same-origin since the proxy serves on our origin (PR 3 baked the cookie
 * jar into the proxy fetch). PR 6 may refactor to push events from
 * copilot.js, but the polling implementation works without it and is the
 * cleaner v1 — no extra protocol surface.
 *
 * Order matters: pinned > video > audio > form-active > upload. The first
 * match wins. Returns undefined when nothing applies.
 */
import type { LiveExclusion } from "./types";

export function detectLiveExclusion(
  iframe: HTMLIFrameElement,
  isPinned: boolean,
): LiveExclusion | undefined {
  if (isPinned) return "pinned";

  const doc = iframe.contentDocument;
  if (!doc) return undefined; // navigation in progress, or cross-origin

  // Audio / video playing
  // Use the iframe's own HTMLVideoElement for instanceof — this works for
  // same-origin iframes in both real browsers and JSDOM.
  const iframeWin = iframe.contentWindow as (Window & { HTMLVideoElement?: typeof HTMLVideoElement }) | null;
  const VideoElement = iframeWin?.HTMLVideoElement ?? HTMLVideoElement;
  for (const media of Array.from(doc.querySelectorAll("video, audio"))) {
    const m = media as HTMLMediaElement;
    if (!m.paused && !m.ended) {
      if (media instanceof VideoElement) {
        return "video";
      }
      return "audio";
    }
  }

  // Active form input with non-empty value
  const active = doc.activeElement as HTMLInputElement | HTMLTextAreaElement | null;
  if (active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA")) {
    if ((active.value ?? "").length > 0) return "form-active";
  }

  // Upload in progress — heuristic: an <input type="file"> with selected files.
  // The actual upload may or may not have started; the user is mid-flow either way.
  const fileInputs = doc.querySelectorAll(
    'input[type="file"]',
  ) as NodeListOf<HTMLInputElement>;
  for (const f of Array.from(fileInputs)) {
    if (f.files && f.files.length > 0) return "upload";
  }

  return undefined;
}
