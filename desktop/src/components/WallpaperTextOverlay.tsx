/**
 * Generic wallpaper slogan overlay — centered text drawn above any wallpaper
 * (animated or image). The text content and whether it shows are driven by the
 * active wallpaper/theme and a user toggle; this component owns only the
 * presentation. Colour / size / style / effects use sensible defaults for now
 * and are intended to become configurable (per wallpaper/theme) in a follow-up.
 */
export function WallpaperTextOverlay({ text }: { text: string }) {
  return (
    <div className="pointer-events-none absolute inset-0 z-0 grid place-items-center" aria-hidden="true">
      <span
        className="font-semibold tracking-tight"
        style={{
          fontSize: "clamp(64px, 11vmin, 240px)",
          color: "rgba(236,236,238,0.96)",
          textShadow: "0 0 40px rgba(180,186,200,0.25), 0 2px 30px rgba(0,0,0,0.4)",
        }}
      >
        {text}
      </span>
    </div>
  );
}
