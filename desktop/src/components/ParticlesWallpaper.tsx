import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Particles, { initParticlesEngine } from "@tsparticles/react";
import { loadSlim } from "@tsparticles/slim";
import type { Container, ISourceOptions } from "@tsparticles/engine";
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
  const containerRef = useRef<Container | null>(null);

  useEffect(() => {
    if (!enginePromise) {
      enginePromise = initParticlesEngine(async (engine) => {
        await loadSlim(engine);
      });
    }
    let alive = true;
    enginePromise.then(
      () => {
        if (alive) setReady(true);
      },
      (err) => {
        // Engine failed to load: leave the wallpaper unrendered so the graphite
        // fallback background shows, and don't leave the rejection unhandled.
        console.warn("tsParticles engine failed to load", err);
      },
    );
    return () => {
      alive = false;
    };
  }, []);

  // tsParticles only auto-pauses when its element leaves the viewport, which a
  // full-screen wallpaper never does (it just gets covered by windows). Pause
  // it when the tab/desktop is hidden so it doesn't burn the Pi in the
  // background.
  useEffect(() => {
    const onVisibility = () => {
      const c = containerRef.current;
      if (!c) return;
      if (document.hidden) c.pause();
      else c.play();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  const onLoaded = useCallback(async (container?: Container) => {
    containerRef.current = container ?? null;
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
      pauseOnOutsideViewport: true, // plus a visibilitychange pause (see effect above) for the covered/hidden case
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
      <Particles id="taos-particles" options={options} particlesLoaded={onLoaded} className="absolute inset-0 h-full w-full" />
    </div>
  );
}
