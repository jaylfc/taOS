import { useEffect, useRef } from "react";

/**
 * Live "neural" wallpaper — an adaptive canvas of drifting nodes and proximity
 * links over a graphite field, inspired by the original taOS neural wallpaper
 * but calmed to a neutral macOS-dark palette. Renders at native resolution for
 * any aspect ratio (16:10, ultrawide 32:10, mobile portrait) from one source,
 * so no per-resolution image assets are needed.
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

export function NeuralWallpaper({ wordmark = true }: { wordmark?: boolean }) {
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

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    function build() {
      const rect = canvas!.getBoundingClientRect();
      w = Math.max(1, Math.round(rect.width));
      h = Math.max(1, Math.round(rect.height));
      canvas!.width = Math.round(w * dpr);
      canvas!.height = Math.round(h * dpr);
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
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
      const g = ctx!.createLinearGradient(0, 0, w, h);
      g.addColorStop(0, "#26262a");
      g.addColorStop(0.42, "#1d1d1f");
      g.addColorStop(1, "#101011");
      ctx!.fillStyle = g;
      ctx!.fillRect(0, 0, w, h);
      const rg = ctx!.createRadialGradient(w * 0.5, h * 0.46, 0, w * 0.5, h * 0.46, Math.max(w, h) * 0.6);
      rg.addColorStop(0, "rgba(255,255,255,0.06)");
      rg.addColorStop(0.5, "rgba(255,255,255,0.015)");
      rg.addColorStop(1, "rgba(255,255,255,0)");
      ctx!.fillStyle = rg;
      ctx!.fillRect(0, 0, w, h);
    }

    function vignette() {
      const vg = ctx!.createRadialGradient(w * 0.5, h * 0.48, Math.min(w, h) * 0.3, w * 0.5, h * 0.48, Math.max(w, h) * 0.75);
      vg.addColorStop(0, "rgba(0,0,0,0)");
      vg.addColorStop(1, "rgba(0,0,0,0.42)");
      ctx!.fillStyle = vg;
      ctx!.fillRect(0, 0, w, h);
    }

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
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i]!;
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j]!;
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const d2 = dx * dx + dy * dy;
          if (d2 < LINK_DIST * LINK_DIST) {
            const o = (1 - Math.sqrt(d2) / LINK_DIST) * 0.5;
            ctx!.strokeStyle = `rgba(${LINK},${o.toFixed(3)})`;
            ctx!.lineWidth = 0.7;
            ctx!.beginPath();
            ctx!.moveTo(a.x, a.y);
            ctx!.lineTo(b.x, b.y);
            ctx!.stroke();
          }
        }
      }
      for (const n of nodes) {
        const tw = 0.6 + 0.4 * Math.sin(t * 0.0014 + n.tw);
        if (n.active) {
          ctx!.fillStyle = `rgba(${ACCENT},${(0.85 * tw).toFixed(3)})`;
          ctx!.shadowColor = `rgba(${ACCENT},0.7)`;
          ctx!.shadowBlur = 8;
        } else {
          ctx!.fillStyle = `rgba(${NODE},${(0.6 * tw).toFixed(3)})`;
          ctx!.shadowBlur = 0;
        }
        ctx!.beginPath();
        ctx!.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx!.fill();
      }
      ctx!.shadowBlur = 0;
      vignette();
      raf = requestAnimationFrame(frame);
    }

    function staticFrame() {
      background();
      for (const n of nodes) {
        ctx!.fillStyle = `rgba(${NODE},0.6)`;
        ctx!.beginPath();
        ctx!.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx!.fill();
      }
      vignette();
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
    if (reduceMotion) staticFrame();
    else start();

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

    return () => {
      stop();
      ro.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return (
    <div className="absolute inset-0 z-0 overflow-hidden" aria-hidden="true">
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />
      {wordmark && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center">
          <span
            className="font-semibold tracking-tight"
            style={{
              fontSize: "clamp(64px, 11vmin, 240px)",
              color: "rgba(236,236,238,0.96)",
              textShadow: "0 0 40px rgba(180,186,200,0.25), 0 2px 30px rgba(0,0,0,0.4)",
            }}
          >
            taOS
          </span>
        </div>
      )}
    </div>
  );
}
