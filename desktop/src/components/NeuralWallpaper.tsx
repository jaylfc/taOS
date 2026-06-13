import { useEffect, useRef } from "react";

/**
 * "neural" animated wallpaper renderer — an adaptive canvas of drifting nodes
 * and proximity links over a graphite field, inspired by the original taOS
 * neural wallpaper but calmed to a neutral macOS-dark palette. Renders at
 * native resolution for any aspect ratio (16:10, ultrawide 32:10, mobile
 * portrait) from one source, so no per-resolution image assets are needed.
 *
 * One render component behind the generic animated-wallpaper kind. The optional
 * slogan is a separate overlay (WallpaperTextOverlay), not part of the
 * renderer, so it works over any wallpaper.
 *
 * Perf: density scales with viewport area, the loop pauses while the tab/app is
 * hidden, and prefers-reduced-motion renders a single static frame. The canvas
 * fills its (positioned) parent via ResizeObserver.
 */

const NODE = "236,237,240"; // soft silver
const LINK = "150,156,170"; // cool graphite
const ACCENT = "174,180,196"; // faint highlight on "active" nodes
const LINK_DIST = 150;

interface Node {
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  active: boolean;
  tw: number;
}

export function NeuralWallpaper() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    let w = 0;
    let h = 0;
    let nodes: Node[] = [];
    let raf = 0;
    let running = false;

    // Gradients depend only on w/h, so they are built once per resize in build()
    // and reused every frame rather than reallocated ~180x/sec (Pi GC churn).
    let bgGrad: CanvasGradient | null = null;
    let glowGrad: CanvasGradient | null = null;
    let vignetteGrad: CanvasGradient | null = null;

    const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    let reduceMotion = motionQuery.matches;

    function build() {
      const rect = canvas!.getBoundingClientRect();
      w = Math.max(1, Math.round(rect.width));
      h = Math.max(1, Math.round(rect.height));
      canvas!.width = Math.round(w * dpr);
      canvas!.height = Math.round(h * dpr);
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);

      bgGrad = ctx!.createLinearGradient(0, 0, w, h);
      bgGrad.addColorStop(0, "#26262a");
      bgGrad.addColorStop(0.42, "#1d1d1f");
      bgGrad.addColorStop(1, "#101011");
      glowGrad = ctx!.createRadialGradient(w * 0.5, h * 0.46, 0, w * 0.5, h * 0.46, Math.max(w, h) * 0.6);
      glowGrad.addColorStop(0, "rgba(255,255,255,0.06)");
      glowGrad.addColorStop(0.5, "rgba(255,255,255,0.015)");
      glowGrad.addColorStop(1, "rgba(255,255,255,0)");
      vignetteGrad = ctx!.createRadialGradient(w * 0.5, h * 0.48, Math.min(w, h) * 0.3, w * 0.5, h * 0.48, Math.max(w, h) * 0.75);
      vignetteGrad.addColorStop(0, "rgba(0,0,0,0)");
      vignetteGrad.addColorStop(1, "rgba(0,0,0,0.42)");

      const count = Math.max(40, Math.min(260, Math.round((w * h) / 14000)));
      nodes = Array.from({ length: count }, () => ({
        x: Math.random() * w,
        y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.22,
        vy: (Math.random() - 0.5) * 0.22,
        r: Math.random() * 1.4 + 0.7,
        active: Math.random() < 0.16,
        tw: Math.random() * Math.PI * 2,
      }));
    }

    function background() {
      ctx!.fillStyle = bgGrad!;
      ctx!.fillRect(0, 0, w, h);
      ctx!.fillStyle = glowGrad!;
      ctx!.fillRect(0, 0, w, h);
    }

    function vignette() {
      ctx!.fillStyle = vignetteGrad!;
      ctx!.fillRect(0, 0, w, h);
    }

    const LINK_RGB = `rgb(${LINK})`;
    const NODE_RGB = `rgb(${NODE})`;
    const ACCENT_RGB = `rgb(${ACCENT})`;

    function frame(t: number) {
      background();
      for (const n of nodes) {
        n.x += n.vx;
        n.y += n.vy;
        if (n.x < -20) n.x = w + 20;
        else if (n.x > w + 20) n.x = -20;
        if (n.y < -20) n.y = h + 20;
        else if (n.y > h + 20) n.y = -20;
      }
      // Links: one fixed strokeStyle, per-pair opacity via globalAlpha (no
      // per-pair rgba string allocation in this O(n^2) hot loop).
      ctx!.strokeStyle = LINK_RGB;
      ctx!.lineWidth = 0.7;
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i]!;
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j]!;
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const d2 = dx * dx + dy * dy;
          if (d2 < LINK_DIST * LINK_DIST) {
            ctx!.globalAlpha = (1 - Math.sqrt(d2) / LINK_DIST) * 0.5;
            ctx!.beginPath();
            ctx!.moveTo(a.x, a.y);
            ctx!.lineTo(b.x, b.y);
            ctx!.stroke();
          }
        }
      }
      ctx!.globalAlpha = 1;
      for (const n of nodes) {
        const tw = 0.6 + 0.4 * Math.sin(t * 0.0014 + n.tw);
        if (n.active) {
          ctx!.fillStyle = ACCENT_RGB;
          ctx!.globalAlpha = 0.85 * tw;
          ctx!.shadowColor = `rgba(${ACCENT},0.7)`;
          ctx!.shadowBlur = 8;
        } else {
          ctx!.fillStyle = NODE_RGB;
          ctx!.globalAlpha = 0.6 * tw;
          ctx!.shadowBlur = 0;
        }
        ctx!.beginPath();
        ctx!.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx!.fill();
      }
      ctx!.globalAlpha = 1;
      ctx!.shadowBlur = 0;
      vignette();
      raf = requestAnimationFrame(frame);
    }

    function staticFrame() {
      background();
      ctx!.fillStyle = NODE_RGB;
      ctx!.globalAlpha = 0.6;
      for (const n of nodes) {
        ctx!.beginPath();
        ctx!.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx!.fill();
      }
      ctx!.globalAlpha = 1;
      vignette();
    }

    function render() {
      if (reduceMotion) staticFrame();
      else start();
    }
    function start() {
      if (running || reduceMotion) return;
      running = true;
      raf = requestAnimationFrame(frame);
    }
    function stop() {
      running = false;
      cancelAnimationFrame(raf);
    }

    build();
    render();

    const ro = new ResizeObserver(() => {
      stop();
      build();
      if (reduceMotion) staticFrame();
      else if (!document.hidden) start();
    });
    ro.observe(canvas);

    const onVisibility = () => {
      if (document.hidden) stop();
      else start();
    };
    document.addEventListener("visibilitychange", onVisibility);

    // React to the OS reduced-motion setting changing mid-session.
    const onMotionChange = () => {
      reduceMotion = motionQuery.matches;
      stop();
      if (reduceMotion) staticFrame();
      else if (!document.hidden) start();
    };
    motionQuery.addEventListener("change", onMotionChange);

    return () => {
      stop();
      ro.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      motionQuery.removeEventListener("change", onMotionChange);
    };
  }, []);

  return (
    <div className="absolute inset-0 z-0 overflow-hidden" aria-hidden="true">
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />
    </div>
  );
}
