import { useEffect, useRef, useState } from "react";
import type { Slide } from "./deck";

// Fixed "design" size the slide is authored at. The outer wrapper is a
// responsive 16:9 box (thumbnail rail, editor preview, present mode, and PDF
// export all use this same component); the inner slide is rendered at this
// exact pixel size and scaled to fit, so every surface shows identical
// proportions instead of separately-tuned layouts per size.
const BASE_WIDTH = 640;
const BASE_HEIGHT = 360;

export interface SlideCanvasProps {
  slide: Slide;
  className?: string;
  /** Skip the ResizeObserver and scale by this factor directly (offscreen export capture, where the container never mounts in the visible DOM). */
  fixedScale?: number;
}

export function SlideCanvas({ slide, className, fixedScale }: SlideCanvasProps) {
  const outerRef = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(fixedScale ?? 1);

  useEffect(() => {
    if (fixedScale != null) return;
    const el = outerRef.current;
    // jsdom (unit tests) has no ResizeObserver at all; fall back to the
    // default scale rather than crashing the render.
    if (!el || typeof ResizeObserver === "undefined") return;
    const update = () => {
      if (el.clientWidth > 0) setScale(el.clientWidth / BASE_WIDTH);
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [fixedScale]);

  return (
    <div
      ref={outerRef}
      className={className}
      style={{ position: "relative", aspectRatio: "16 / 9", overflow: "hidden" }}
    >
      <div
        data-slide-stage
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: BASE_WIDTH,
          height: BASE_HEIGHT,
          transform: `scale(${scale})`,
          transformOrigin: "top left",
        }}
      >
        <SlideBody slide={slide} />
      </div>
    </div>
  );
}

function SlideBody({ slide }: { slide: Slide }) {
  const base: React.CSSProperties = {
    width: BASE_WIDTH,
    height: BASE_HEIGHT,
    background:
      "radial-gradient(120% 130% at 18% 14%,#4a5572,transparent 55%),linear-gradient(150deg,#262c3b,#14161f)",
    color: "#fff",
    display: "flex",
    flexDirection: "column",
    boxSizing: "border-box",
    overflow: "hidden",
  };

  if (slide.layout === "blank") {
    if (slide.imageDataUri) {
      return (
        <div style={{ ...base, alignItems: "center", justifyContent: "center", padding: 0 }}>
          <img
            src={slide.imageDataUri}
            alt={slide.title || "Slide image"}
            style={{ width: "100%", height: "100%", objectFit: "cover" }}
          />
        </div>
      );
    }
    return (
      <div style={{ ...base, alignItems: "center", justifyContent: "center", padding: 40 }}>
        {slide.title && (
          <h1 style={{ margin: 0, fontSize: 26, fontWeight: 700, textAlign: "center" }}>
            {slide.title}
          </h1>
        )}
        {slide.body && (
          <p
            style={{
              margin: "10px 0 0",
              fontSize: 14,
              textAlign: "center",
              color: "rgba(255,255,255,0.75)",
            }}
          >
            {slide.body}
          </p>
        )}
      </div>
    );
  }

  if (slide.layout === "section-header") {
    return (
      <div style={{ ...base, alignItems: "center", justifyContent: "center", padding: 48 }}>
        <div
          style={{
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: 2,
            textTransform: "uppercase",
            color: "rgba(255,255,255,0.55)",
          }}
        >
          Section
        </div>
        <h1
          style={{
            margin: "10px 0 0",
            fontSize: 34,
            fontWeight: 800,
            letterSpacing: -1,
            textAlign: "center",
          }}
        >
          {slide.title || "Untitled section"}
        </h1>
        {slide.body && (
          <p
            style={{
              margin: "12px 0 0",
              maxWidth: "80%",
              fontSize: 14,
              textAlign: "center",
              color: "rgba(255,255,255,0.75)",
            }}
          >
            {slide.body}
          </p>
        )}
      </div>
    );
  }

  if (slide.layout === "title") {
    return (
      <div style={{ ...base, justifyContent: "center", padding: "0 52px" }}>
        <div
          style={{
            fontSize: 12,
            fontWeight: 700,
            letterSpacing: 2,
            textTransform: "uppercase",
            color: "rgba(255,255,255,0.6)",
          }}
        >
          taOS Studios
        </div>
        <h1 style={{ margin: "10px 0 0", fontSize: 38, fontWeight: 800, letterSpacing: -1 }}>
          {slide.title || "Untitled slide"}
        </h1>
        {slide.body && (
          <p
            style={{
              margin: "14px 0 0",
              maxWidth: "80%",
              fontSize: 15,
              lineHeight: 1.5,
              color: "rgba(255,255,255,0.82)",
            }}
          >
            {slide.body}
          </p>
        )}
        {slide.imageDataUri && (
          <img
            src={slide.imageDataUri}
            alt=""
            style={{ marginTop: 16, maxHeight: 90, borderRadius: 6, objectFit: "cover" }}
          />
        )}
      </div>
    );
  }

  if (slide.layout === "two-column") {
    return (
      <div style={{ ...base, padding: "36px 44px" }}>
        <h2 style={{ margin: 0, fontSize: 24, fontWeight: 800, letterSpacing: -0.5 }}>
          {slide.title || "Untitled slide"}
        </h2>
        <div style={{ marginTop: 18, display: "flex", flex: 1, gap: 28, minHeight: 0 }}>
          <ul style={{ flex: 1, margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.7 }}>
            {slide.bullets.length === 0 && (
              <li style={{ color: "rgba(255,255,255,0.4)", listStyle: "none", marginLeft: -18 }}>
                No bullets yet
              </li>
            )}
            {slide.bullets.map((b, i) => <li key={i}>{b}</li>)}
          </ul>
          <div style={{ flex: 1, minHeight: 0, display: "flex", alignItems: "center" }}>
            {slide.imageDataUri ? (
              <img
                src={slide.imageDataUri}
                alt=""
                style={{ width: "100%", maxHeight: 220, objectFit: "cover", borderRadius: 8 }}
              />
            ) : (
              <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: "rgba(255,255,255,0.75)" }}>
                {slide.body}
              </p>
            )}
          </div>
        </div>
      </div>
    );
  }

  // title-content (default)
  return (
    <div style={{ ...base, padding: "36px 44px" }}>
      <h2 style={{ margin: 0, fontSize: 24, fontWeight: 800, letterSpacing: -0.5 }}>
        {slide.title || "Untitled slide"}
      </h2>
      {slide.body && (
        <p
          style={{
            margin: "12px 0 0",
            fontSize: 14,
            lineHeight: 1.6,
            color: "rgba(255,255,255,0.82)",
          }}
        >
          {slide.body}
        </p>
      )}
      {slide.bullets.length > 0 && (
        <ul style={{ margin: "10px 0 0", paddingLeft: 18, fontSize: 13, lineHeight: 1.7 }}>
          {slide.bullets.map((b, i) => <li key={i}>{b}</li>)}
        </ul>
      )}
      {slide.imageDataUri && (
        <img
          src={slide.imageDataUri}
          alt=""
          style={{
            marginTop: 12,
            width: "100%",
            maxHeight: 130,
            objectFit: "cover",
            borderRadius: 8,
          }}
        />
      )}
    </div>
  );
}
