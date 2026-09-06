import { useState } from "react";
import { Sparkles, CheckSquare, AlignLeft, Columns2, Gamepad2, MessageSquare, Image, Clock, LayoutDashboard } from "lucide-react";
import { seedBuildPrompt } from "./build-state";

/* ------------------------------------------------------------------ */
/*  TemplatesView -- hero + template grid                              */
/* ------------------------------------------------------------------ */

interface TplCard {
  label: string;
  desc: string;
  icon: React.ElementType;
  gradient: string;
}

const TEMPLATES: TplCard[] = [
  {
    label: "Dashboard",
    desc: "Cards, charts, and live stats.",
    icon: LayoutDashboard,
    gradient: "linear-gradient(135deg,#6f7687,#474d5e)",
  },
  {
    label: "Tracker",
    desc: "Lists, checkboxes, streaks.",
    icon: CheckSquare,
    gradient: "linear-gradient(135deg,#5f8a6f,#456f54)",
  },
  {
    label: "Form",
    desc: "Collect input, store responses.",
    icon: AlignLeft,
    gradient: "linear-gradient(135deg,#5d7a8a,#46606c)",
  },
  {
    label: "Kanban",
    desc: "Columns and draggable cards.",
    icon: Columns2,
    gradient: "linear-gradient(135deg,#7a7488,#5e596b)",
  },
  {
    label: "Mini-game",
    desc: "Score, timer, simple loop.",
    icon: Gamepad2,
    gradient: "linear-gradient(135deg,#8a7a5d,#6c5e46)",
  },
  {
    label: "Agent Panel",
    desc: "A custom UI for one of your agents.",
    icon: MessageSquare,
    gradient: "linear-gradient(135deg,#6f7687,#474d5e)",
  },
  {
    label: "Gallery",
    desc: "A grid of media with detail view.",
    icon: Image,
    gradient: "linear-gradient(135deg,#5f8a6f,#456f54)",
  },
  {
    label: "Blank",
    desc: "An empty taOS SDK app.",
    icon: Clock,
    gradient: "linear-gradient(135deg,#7a7488,#5e596b)",
  },
];

function templatePrompt(t: TplCard): string {
  return `Build a ${t.label} taOS app. ${t.desc}`;
}

export function TemplatesView() {
  const [heroPrompt, setHeroPrompt] = useState(
    "a shared shopping list the whole house can add to from their phones...",
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* view header */}
      <div className="flex h-[54px] flex-none items-center gap-3 border-b border-shell-border px-[22px]">
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">New app</h2>
        <span className="text-[12px] text-shell-text-tertiary">
          Describe it, or start from a template
        </span>
      </div>

      {/* scrollable body */}
      <div className="flex-1 overflow-auto p-[22px]">
        {/* hero */}
        <div
          className="mb-5 rounded-[18px] border border-shell-border p-[22px_24px]"
          style={{
            background:
              "radial-gradient(120% 160% at 10% 10%, var(--tw-color-accent-glow, rgba(139,146,163,0.35)), transparent 55%), var(--color-shell-bg-deep)",
          }}
        >
          <h3 className="text-[20px] font-extrabold tracking-[-0.02em]">
            Build a taOS app in plain words.
          </h3>
          <p className="mt-[7px] max-w-[580px] text-[13px] leading-relaxed text-shell-text-secondary">
            Describe what it should do. An agent builds it against the taOS SDK, sandboxed and safe,
            and you can publish it to your Store or share it with family.
          </p>
          <div className="mt-[15px] flex gap-[10px]">
            <textarea
              value={heroPrompt}
              onChange={(e) => setHeroPrompt(e.target.value)}
              rows={2}
              className="flex-1 resize-none rounded-[13px] border border-shell-border bg-shell-surface px-[15px] py-3 text-[13px] text-shell-text-secondary placeholder:text-shell-text-tertiary focus-visible:border-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/20"
            />
            <button
              type="button"
              onClick={() => seedBuildPrompt(heroPrompt)}
              className="flex items-center gap-[9px] rounded-[15px] px-[22px] text-[14px] font-bold text-white"
              style={{ background: "linear-gradient(135deg,var(--color-accent),var(--color-accent))" }}
            >
              <Sparkles size={18} />
              Build
            </button>
          </div>
          <p className="mt-2 text-[11px] text-shell-text-tertiary">
            Switches to the Build tab to use your prompt.
          </p>
        </div>

        {/* section label */}
        <div className="mb-3 mt-1.5 text-[12px] font-bold uppercase tracking-[0.05em] text-shell-text-tertiary">
          Start from a template
        </div>

        {/* template grid */}
        <div className="grid grid-cols-4 gap-[13px]">
          {TEMPLATES.map((t) => {
            const Icon = t.icon;
            return (
              <button
                key={t.label}
                type="button"
                onClick={() => seedBuildPrompt(templatePrompt(t))}
                className="flex cursor-pointer flex-col gap-[9px] rounded-[15px] border border-shell-border bg-shell-surface p-[15px] text-left transition-all hover:-translate-y-[3px] hover:border-shell-border-strong"
              >
                <div
                  className="flex h-10 w-10 items-center justify-center rounded-[11px] text-white"
                  style={{ background: t.gradient }}
                >
                  <Icon size={21} />
                </div>
                <div className="text-[13.5px] font-bold">{t.label}</div>
                <div className="flex-1 text-[11.5px] leading-[1.45] text-shell-text-secondary">
                  {t.desc}
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}