import { useEffect, useRef, useState } from "react";
import { SCREENSHOT_FLASH_EVENT } from "@/hooks/use-desktop-command-stream";

/**
 * A subtle screen effect played when a desktop screenshot is captured (agent or
 * user). A quick aperture-style flash: a soft white veil fades in and out while
 * a thin inset frame pulses, evoking a camera shutter without being jarring.
 *
 * Marked data-screenshot-exclude so it never appears in the captured image even
 * though it is on-screen during the (sub-second) rasterisation.
 *
 * Respects prefers-reduced-motion: a single brief opacity blip, no scale.
 */
export function ScreenshotFlash() {
  const [active, setActive] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const onFlash = () => {
      setActive(false);
      // Force a reflow so re-triggering restarts the animation.
      requestAnimationFrame(() => setActive(true));
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setActive(false), 600);
    };
    window.addEventListener(SCREENSHOT_FLASH_EVENT, onFlash);
    return () => {
      window.removeEventListener(SCREENSHOT_FLASH_EVENT, onFlash);
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  if (!active) return null;

  return (
    <div
      data-screenshot-exclude="true"
      aria-hidden="true"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 2147483646,
        pointerEvents: "none",
      }}
    >
      <div className="taos-shot-veil" />
      <div className="taos-shot-frame" />
      <style>{`
        @keyframes taos-shot-veil {
          0% { opacity: 0; }
          12% { opacity: 0.55; }
          100% { opacity: 0; }
        }
        @keyframes taos-shot-frame {
          0% { opacity: 0; transform: scale(1.012); }
          15% { opacity: 1; transform: scale(1); }
          100% { opacity: 0; transform: scale(1); }
        }
        .taos-shot-veil {
          position: absolute; inset: 0;
          background: radial-gradient(120% 120% at 50% 50%, #ffffff 0%, #ffffff 60%, rgba(255,255,255,0.6) 100%);
          animation: taos-shot-veil 600ms cubic-bezier(0.22, 1, 0.36, 1) forwards;
        }
        .taos-shot-frame {
          position: absolute; inset: 10px;
          border: 2px solid rgba(255,255,255,0.85);
          border-radius: 14px;
          box-shadow: 0 0 0 1px rgba(0,0,0,0.15), inset 0 0 40px rgba(255,255,255,0.25);
          animation: taos-shot-frame 600ms cubic-bezier(0.22, 1, 0.36, 1) forwards;
        }
        @media (prefers-reduced-motion: reduce) {
          .taos-shot-veil { animation-duration: 300ms; }
          .taos-shot-veil { background: rgba(255,255,255,0.9); }
          .taos-shot-frame { display: none; }
        }
      `}</style>
    </div>
  );
}
