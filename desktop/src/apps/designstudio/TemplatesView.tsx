import { useState } from "react";

const FILTER_PILLS = ["All", "Social", "Posters", "Presentations", "Logos", "Print"];

export interface TemplateChoice {
  name: string;
  width: number;
  height: number;
}

const TEMPLATES: {
  name: string;
  caption: string;
  size: string;
  width: number;
  height: number;
  gradient: string;
}[] = [
  {
    name: "Instagram Post",
    caption: "Square post",
    size: "1080x1080",
    width: 1080,
    height: 1080,
    gradient: "linear-gradient(140deg, #2c3142, #171a24)",
  },
  {
    name: "Story",
    caption: "Vertical story",
    size: "1080x1920",
    width: 1080,
    height: 1920,
    gradient: "linear-gradient(140deg, #3d8f7a, #1d4d42)",
  },
  {
    name: "Poster",
    caption: "Event poster",
    size: "1080x1350",
    width: 1080,
    height: 1350,
    gradient: "linear-gradient(140deg, #5a6b86, #2b3447)",
  },
  {
    name: "Presentation",
    caption: "Slide deck",
    size: "16:9",
    width: 1920,
    height: 1080,
    gradient: "linear-gradient(140deg, #c98b5b, #7a4f2e)",
  },
  {
    name: "Logo",
    caption: "Brand mark",
    size: "500x500",
    width: 500,
    height: 500,
    gradient: "linear-gradient(140deg, #4a4150, #241c28)",
  },
  {
    name: "Flyer",
    caption: "A5 flyer",
    size: "print",
    width: 1748,
    height: 2480,
    gradient: "linear-gradient(140deg, #356270, #16323a)",
  },
  {
    name: "Banner",
    caption: "Web banner",
    size: "1500x500",
    width: 1500,
    height: 500,
    gradient: "linear-gradient(140deg, #6b7689, #3a4151)",
  },
  {
    name: "Business Card",
    caption: "Card",
    size: "print",
    width: 1050,
    height: 600,
    gradient: "linear-gradient(140deg, #7a5d8a, #3d2c47)",
  },
];

export interface TemplatesViewProps {
  onSelectTemplate: (template: TemplateChoice) => void;
}

export function TemplatesView({ onSelectTemplate }: TemplatesViewProps) {
  const [activeFilter, setActiveFilter] = useState("All");

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* view header */}
      <div
        className="flex flex-none items-center gap-3 border-b border-shell-border px-[22px]"
        style={{ height: "54px" }}
      >
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Templates</h2>
        <span className="text-[12px] text-shell-text-tertiary">Start from a ready-made design</span>
      </div>

      {/* scrollable body */}
      <div className="flex-1 overflow-auto p-[22px]">
        {/* filter pills */}
        <div className="mb-[18px] flex flex-wrap gap-[9px]">
          {FILTER_PILLS.map((pill) => (
            <button
              key={pill}
              type="button"
              onClick={() => setActiveFilter(pill)}
              className={`rounded-full px-[14px] py-[7px] text-[12px] font-semibold transition-colors ${
                activeFilter === pill
                  ? "border-0 text-white"
                  : "border border-shell-border bg-shell-surface text-shell-text-secondary hover:border-shell-border-strong"
              }`}
              style={
                activeFilter === pill
                  ? { background: "#8b92a3" }
                  : undefined
              }
            >
              {pill}
            </button>
          ))}
        </div>

        {/* template grid */}
        <div className="grid grid-cols-2 gap-[14px] sm:grid-cols-3 lg:grid-cols-4">
          {TEMPLATES.map(({ name, caption, size, width, height, gradient }) => (
            <button
              key={name}
              type="button"
              onClick={() => onSelectTemplate({ name, width, height })}
              className="cursor-pointer overflow-hidden rounded-[14px] border border-shell-border bg-shell-surface text-left transition-all hover:-translate-y-[3px] hover:border-shell-border-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
            >
              <div
                className="flex h-[150px] items-center justify-center text-[19px] font-extrabold tracking-[-0.5px] text-white"
                style={{ background: gradient }}
              >
                {name}
              </div>
              <div className="px-3 py-2.5 text-[12px] font-semibold text-shell-text-secondary">
                {caption}{" "}
                <span className="text-[11px] font-normal text-shell-text-tertiary">· {size}</span>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
