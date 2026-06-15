/**
 * Persistent screen-capture grant for full-fidelity agent screenshots.
 *
 * getDisplayMedia needs a user gesture and shows a prompt, and the browser will
 * not let an agent call it headlessly. But the MediaStream it returns is
 * persistent: the user grants ONCE (one gesture, one prompt) and we keep the
 * stream alive, then grab frames from it on demand with no further prompt. This
 * captures the real composited screen -- including cross-origin iframes (the
 * Browser's proxied page) that DOM rasterisation cannot read.
 *
 * The user stays in control: the browser shows a sharing indicator and can stop
 * the share at any time (we listen for 'ended' and clear the grant). A truly
 * invisible/persistent grant that survives reload needs a native wrapper or a
 * browser extension; this is the standard-web ceiling and covers agent-driven
 * on-demand capture during a session.
 */

let _stream: MediaStream | null = null;
let _video: HTMLVideoElement | null = null;

/**
 * Fired on window whenever the capture grant changes (granted, or stopped --
 * including when the user ends the share from the browser's native bar). UI
 * controls listen to stay in sync rather than only updating on their own click.
 */
export const SCREEN_CAPTURE_CHANGED_EVENT = "taos:screen-capture-changed";

function _emitChange(): void {
  window.dispatchEvent(new CustomEvent(SCREEN_CAPTURE_CHANGED_EVENT));
}

/** True when a live capture stream is available for frame grabs. */
export function hasScreenCapture(): boolean {
  return !!_stream && _stream.getVideoTracks().some((t) => t.readyState === "live");
}

/**
 * Prompt the user to grant screen capture (once per session). Returns true on
 * grant. Safe to call again to re-grant after the user stopped sharing.
 */
export async function grantScreenCapture(): Promise<boolean> {
  if (hasScreenCapture()) return true;
  const md = navigator.mediaDevices;
  if (!md?.getDisplayMedia) return false;
  let stream: MediaStream;
  try {
    stream = await md.getDisplayMedia({
      video: { frameRate: { ideal: 4, max: 10 } },
      audio: false,
      // Bias toward the taOS tab so a single click captures the desktop.
      ...({ preferCurrentTab: true } as Record<string, unknown>),
    });
  } catch {
    return false; // user cancelled or denied
  }
  _stream = stream;
  const track = stream.getVideoTracks()[0];
  if (track) {
    track.addEventListener("ended", revokeScreenCapture);
  }
  const video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.srcObject = stream;
  try {
    await video.play();
  } catch {
    /* autoplay of a muted stream rarely fails; frames still grab from currentTime */
  }
  _video = video;
  _emitChange();
  return true;
}

/** Stop the capture grant and release the stream. */
export function revokeScreenCapture(): void {
  const had = !!_stream;
  _stream?.getTracks().forEach((t) => t.stop());
  _stream = null;
  if (_video) {
    _video.srcObject = null;
    _video = null;
  }
  if (had) _emitChange();
}

/**
 * Grab one frame from the live capture stream as a PNG data URL, or null when
 * no capture is granted / no frame is available. Prefers ImageCapture
 * (crisp, no scaling) and falls back to drawing the video element to a canvas.
 */
export async function grabScreenFrame(): Promise<string | null> {
  if (!hasScreenCapture() || !_stream) return null;
  const track = _stream.getVideoTracks()[0];
  if (!track) return null;

  // Preferred path: ImageCapture.grabFrame -> ImageBitmap -> canvas.
  const ImageCaptureCtor = (window as unknown as { ImageCapture?: new (t: MediaStreamTrack) => { grabFrame(): Promise<ImageBitmap> } }).ImageCapture;
  if (ImageCaptureCtor) {
    try {
      const bitmap = await new ImageCaptureCtor(track).grabFrame();
      const canvas = document.createElement("canvas");
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const ctx = canvas.getContext("2d");
      if (ctx) {
        ctx.drawImage(bitmap, 0, 0);
        return canvas.toDataURL("image/png");
      }
    } catch {
      /* fall through to the video path */
    }
  }

  // Fallback: draw the playing <video> to a canvas.
  if (_video && _video.videoWidth > 0) {
    const canvas = document.createElement("canvas");
    canvas.width = _video.videoWidth;
    canvas.height = _video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (ctx) {
      ctx.drawImage(_video, 0, 0, canvas.width, canvas.height);
      return canvas.toDataURL("image/png");
    }
  }
  return null;
}
