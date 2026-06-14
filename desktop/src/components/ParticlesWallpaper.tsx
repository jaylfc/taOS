import { useEffect, useMemo, useState } from "react";
import Particles, { initParticlesEngine } from "@tsparticles/react";
import { loadSlim } from "@tsparticles/slim";
import type { ISourceOptions } from "@tsparticles/engine";
import { useThemeStore } from "@/stores/theme-store";

/**
 * Live particle-network wallpaper (tsParticles, the maintained particles.js
 * successor). A drifting mesh of nodes + proximity links, rendered at native
 * resolution for any aspect ratio. Theme-aware: node/link/background colours
 * derive from the active scheme, so it inverts with the theme.
 *
 * This is the configurable foundation for user/agent-authored wallpapers; the
 * density / speed / colour are intended to become slider-driven (and the
 * particle config is the package payload for shareable live wallpapers).
 */

// The engine is initialised once per page; loadSlim brings the links + move
// features we need without the full bundle.
let enginePromise: Promise<void> | null = null;

export function ParticlesWallpaper() {
  const [ready, setReady] = useState(false);
  const scheme = useThemeStore((s) => s.scheme);
  const params = useThemeStore((s) => s.wallpaperParams);

  useEffect(() => {
    if (!enginePromise) {
      enginePromise = initParticlesEngine(async (engine) => {
        await loadSlim(engine);
      });
    }
    let alive = true;
    enginePromise.then(() => {
      if (alive) setReady(true);
    });
    return () => {
      alive = false;
    };
  }, []);

  const options = useMemo<ISourceOptions>(() => {
    const dark = scheme !== "light";
    const node = dark ? "#e9edf4" : "#1b1d22";
    const link = dark ? "#9aa0ad" : "#5f6773";
    const bg = dark ? "#141415" : "#eef0f3";
    return {
      fullScreen: { enable: false },
      background: { color: bg },
      fpsLimit: 60,
      detectRetina: true,
      pauseOnBlur: false,
      pauseOnOutsideViewport: true, // pause when the desktop is hidden (Pi perf)
      particles: {
        number: { value: params.density, density: { enable: true, area: 900 } },
        color: { value: node },
        links: { enable: true, distance: 140, color: link, opacity: dark ? 0.3 : 0.42, width: 1 },
        move: { enable: true, speed: params.speed, outModes: { default: "bounce" } },
        size: { value: { min: 1, max: 2.6 } },
        opacity: { value: { min: 0.25, max: 0.9 }, animation: { enable: true, speed: 0.7, sync: false } },
        shadow: { enable: params.glow > 0, color: dark ? "#aab8d0" : "#1b1d22", blur: params.glow },
      },
    };
  }, [scheme, params.density, params.speed, params.glow]);

  if (!ready) return null;

  return (
    <div className="absolute inset-0 z-0 overflow-hidden" aria-hidden="true">
      <Particles id="taos-particles" options={options} className="absolute inset-0 h-full w-full" />
    </div>
  );
}
