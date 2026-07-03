import { Sparkles } from "lucide-react";

const SLIDES = [
  {
    idx: 1,
    label: "Build it your way",
    bg: "radial-gradient(120% 130% at 18% 14%,#4a5572,transparent 55%),linear-gradient(150deg,#262c3b,#14161f)",
  },
  { idx: 2, label: "Ready today", bg: "linear-gradient(150deg,#1d3a33,#0f1f1b)" },
  { idx: 3, label: "On the way", bg: "linear-gradient(150deg,#3a2f1d,#1f190f)" },
  { idx: 4, label: "Your hardware", bg: "linear-gradient(150deg,#2c2436,#16111d)" },
  { idx: 5, label: "Get started", bg: "linear-gradient(150deg,#23303f,#121b24)" },
] as const;

const LAYOUTS = [
  { id: "title", active: true, barWidths: ["70%", "90%"] },
  { id: "split", active: false, barWidths: ["50%", "90%"] },
  { id: "center", active: false, barWidths: ["40%"] },
] as const;

export function SlidesView() {
  return (
    <div className="flex min-h-0 flex-1">
      {/* slide thumbnail rail */}
      <aside
        className="flex w-[150px] flex-none flex-col gap-2.5 overflow-auto border-r border-shell-border bg-shell-bg-deep p-3"
        aria-label="Slide thumbnails"
      >
        {SLIDES.map((s) => (
          <div
            key={s.idx}
            role="button"
            tabIndex={0}
            aria-label={`Slide ${s.idx}: ${s.label}`}
            className="relative flex aspect-video cursor-pointer items-center justify-center overflow-hidden rounded-lg border px-1.5 text-center text-[10px] font-bold text-white"
            style={{
              background: s.bg,
              borderColor:
                s.idx === 1 ? "var(--color-accent, #8b92a3)" : "rgba(255,255,255,0.08)",
              outline: s.idx === 1 ? "2px solid #a9b0c2" : undefined,
              outlineOffset: s.idx === 1 ? 1 : undefined,
            }}
          >
            <span
              className="absolute left-1.5 top-1 text-[8px]"
              style={{ color: "rgba(255,255,255,0.7)" }}
            >
              {s.idx}
            </span>
            {s.label}
          </div>
        ))}
      </aside>

      {/* slide canvas stage */}
      <div className="flex flex-1 flex-col items-center justify-center bg-shell-bg-deep p-5">
        <div
          className="relative flex w-[560px] flex-col justify-center overflow-hidden rounded-[10px] px-[52px] py-[46px] text-white"
          style={{
            aspectRatio: "16/9",
            background:
              "radial-gradient(120% 130% at 18% 14%,#4a5572,transparent 55%),linear-gradient(150deg,#262c3b,#14161f)",
            boxShadow: "0 22px 50px -16px rgba(0,0,0,0.6)",
          }}
        >
          <div
            className="text-[12px] font-bold tracking-[2px] uppercase"
            style={{ color: "rgba(255,255,255,0.6)" }}
          >
            taOS Studios
          </div>
          <h1
            className="mt-2.5 font-extrabold leading-[1.05] tracking-tight"
            style={{ fontSize: 38, letterSpacing: -1 }}
          >
            Build it your way.
          </h1>
          <p
            className="mt-3.5 max-w-[80%] text-[15px] leading-[1.5]"
            style={{ color: "rgba(255,255,255,0.82)" }}
          >
            Dedicated studios for every project, running on hardware you already own.
          </p>
          {/* selection box */}
          <div
            className="pointer-events-none absolute rounded-[4px]"
            style={{
              border: "1.5px solid #a9b0c2",
              left: 46,
              top: 96,
              right: 120,
              height: 88,
            }}
          />
        </div>
      </div>

      {/* right panel */}
      <aside className="flex w-[248px] flex-none flex-col gap-3.5 border-l border-shell-border bg-shell-bg p-[18px]">
        {/* generate a deck */}
        <div
          className="rounded-[13px] border p-3.5"
          style={{
            borderColor: "rgba(139,146,163,0.35)",
            background:
              "radial-gradient(120% 130% at 12% 10%, rgba(139,146,163,0.35), transparent 60%), var(--color-shell-surface, rgba(255,255,255,0.045))",
          }}
        >
          <div className="flex items-center gap-1.5 text-[13px] font-bold text-shell-text">
            <Sparkles size={15} className="text-accent" />
            Generate a deck
          </div>
          <p className="mb-2.5 mt-1.5 text-[11.5px] leading-[1.45] text-shell-text-secondary">
            Describe the talk and taOS drafts the slides, on brand.
          </p>
          <div className="mb-2 rounded-[10px] border border-shell-border bg-shell-bg-deep px-3 py-2.5 text-[11.5px] text-shell-text-tertiary">
            a 5-slide intro to taOS Studios for new users&hellip;
          </div>
          <button
            type="button"
            className="flex w-full items-center justify-center gap-2 rounded-[11px] border-none bg-gradient-to-br from-accent to-accent/70 py-2.5 text-[12.5px] font-bold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <Sparkles size={15} />
            Generate
          </button>
        </div>

        {/* layout picker */}
        <div className="text-[11px] font-bold uppercase tracking-[0.06em] text-shell-text-tertiary">
          Layout
        </div>
        <div className="grid grid-cols-3 gap-2">
          {LAYOUTS.map((lay) => (
            <button
              key={lay.id}
              type="button"
              aria-label={`Layout ${lay.id}`}
              className="flex aspect-[16/10] cursor-pointer flex-col gap-1 rounded-lg border bg-shell-surface p-1.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              style={{
                borderColor: lay.active ? "#a9b0c2" : "rgba(255,255,255,0.08)",
              }}
            >
              {lay.barWidths.map((w, i) => (
                <div
                  key={i}
                  className="h-[3px] rounded-sm"
                  style={{
                    width: w,
                    background: i === 0 ? "rgba(255,255,255,0.40)" : "rgba(255,255,255,0.14)",
                    alignSelf: lay.id === "center" ? "center" : undefined,
                  }}
                />
              ))}
            </button>
          ))}
        </div>
      </aside>
    </div>
  );
}
