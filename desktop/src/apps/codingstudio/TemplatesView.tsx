import {
  Sparkles,
  LayoutPanelTop,
  List,
  TerminalSquare,
  Diamond,
  Square,
  Calendar,
  ChevronRight,
  Globe,
} from "lucide-react";
import { type LucideIcon } from "lucide-react";

const TEMPLATES: {
  name: string;
  desc: string;
  stack: string;
  gradient: string;
  Icon: LucideIcon;
}[] = [
  {
    name: "Web App",
    desc: "React + Vite single-page app with routing.",
    stack: "react - ts - vite",
    gradient: "linear-gradient(135deg,#6f7687,#565d6e)",
    Icon: LayoutPanelTop,
  },
  {
    name: "REST API",
    desc: "FastAPI service with typed routes and docs.",
    stack: "python - fastapi",
    gradient: "linear-gradient(135deg,#5f8a6f,#4a6f57)",
    Icon: List,
  },
  {
    name: "CLI Tool",
    desc: "Command-line app with args and help output.",
    stack: "node - commander",
    gradient: "linear-gradient(135deg,#7a7488,#5e596b)",
    Icon: TerminalSquare,
  },
  {
    name: "Discord Bot",
    desc: "Slash-command bot wired to your agents.",
    stack: "node - discord.js",
    gradient: "linear-gradient(135deg,#5d7a8a,#465e6c)",
    Icon: Diamond,
  },
  {
    name: "Static Site",
    desc: "Fast marketing or docs site, deploy to LAN.",
    stack: "astro - html",
    gradient: "linear-gradient(135deg,#8a7a5d,#6c5e46)",
    Icon: Square,
  },
  {
    name: "Data Pipeline",
    desc: "Scheduled job that pulls, transforms, stores.",
    stack: "python - pandas",
    gradient: "linear-gradient(135deg,#6f7687,#565d6e)",
    Icon: Calendar,
  },
  {
    name: "Python Script",
    desc: "A single-file utility for a quick automation.",
    stack: "python",
    gradient: "linear-gradient(135deg,#5f8a6f,#4a6f57)",
    Icon: ChevronRight,
  },
  {
    name: "Browser Extension",
    desc: "Chrome/Firefox extension scaffold + manifest.",
    stack: "ts - webext",
    gradient: "linear-gradient(135deg,#7a7488,#5e596b)",
    Icon: Globe,
  },
];

export function TemplatesView() {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* view header */}
      <div
        className="flex flex-none items-center gap-3 border-b border-shell-border px-[22px]"
        style={{ height: "54px" }}
      >
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">New project</h2>
        <span className="text-[12px] text-shell-text-tertiary">
          Start from a prompt, a template, or blank
        </span>
      </div>

      {/* scrollable body */}
      <div className="flex-1 overflow-auto p-[22px]">
        {/* hero card */}
        <div
          className="relative mb-[22px] overflow-hidden rounded-[18px] border border-shell-border bg-shell-bg-deep p-[22px]"
          style={{
            backgroundImage:
              "radial-gradient(120% 160% at 10% 10%, rgba(139,146,163,0.18), transparent 55%)",
          }}
        >
          <h3 className="text-[20px] font-extrabold tracking-[-0.02em]">
            Describe what you want to build.
          </h3>
          <p className="mt-[7px] max-w-[560px] text-[13px] leading-[1.5] text-shell-text-secondary">
            Tell taOS the app, tool, or script you need. An agent on your cluster scaffolds it,
            writes the code, runs it, and hands you a live preview you can keep editing.
          </p>
          <div className="mt-4 flex gap-2.5">
            <input
              className="flex-1 rounded-[13px] border border-shell-border bg-shell-surface px-[15px] py-3 text-[13px] text-shell-text placeholder:text-shell-text-tertiary focus:outline-none focus:ring-1 focus:ring-accent/40"
              placeholder="a habit tracker with a weekly streak chart and reminders..."
            />
            <button
              type="button"
              className="flex cursor-pointer items-center gap-2 rounded-[15px] border-0 px-[22px] py-3 text-[14px] font-bold text-white"
              style={{
                background: "linear-gradient(135deg,#a9b0c2,#8b92a3)",
                boxShadow: "0 8px 22px -8px rgba(139,146,163,0.35)",
              }}
            >
              <Sparkles size={18} />
              Build
            </button>
          </div>
        </div>

        {/* template grid */}
        <div className="mb-3 ml-0.5 mt-1.5 text-[12px] font-bold uppercase tracking-[0.05em] text-shell-text-tertiary">
          Start from a template
        </div>
        <div className="grid grid-cols-4 gap-[13px]">
          {TEMPLATES.map(({ name, desc, stack, gradient, Icon }) => (
            <div
              key={name}
              className="flex cursor-pointer flex-col gap-[9px] rounded-[15px] border border-shell-border bg-shell-surface p-[15px] transition-all hover:-translate-y-[3px] hover:border-shell-border-strong hover:bg-shell-surface-active"
            >
              <div
                className="flex h-10 w-10 items-center justify-center rounded-[11px] text-white"
                style={{ background: gradient }}
              >
                <Icon size={21} />
              </div>
              <div className="text-[13.5px] font-bold tracking-[-0.01em]">{name}</div>
              <div className="flex-1 text-[11.5px] leading-[1.45] text-shell-text-secondary">
                {desc}
              </div>
              <div className="font-mono text-[10.5px] font-semibold text-shell-text-tertiary">
                {stack}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
