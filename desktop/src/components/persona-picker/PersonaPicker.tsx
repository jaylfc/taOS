import { useState } from "react";
import type { PersonaSelection } from "./types";
import { PersonaBrowse } from "./PersonaBrowse";
import { PersonaCreate } from "./PersonaCreate";
import { PersonaBlank } from "./PersonaBlank";

type Tab = "browse" | "create" | "blank";

export function PersonaPicker({
  onSelect,
}: {
  onSelect: (s: PersonaSelection) => void;
}) {
  const [tab, setTab] = useState<Tab>("browse");
  return (
    <div className="flex flex-col gap-3">
      <div role="tablist" className="flex gap-2 border-b">
        {(["browse", "create", "blank"] as const).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`px-3 py-1.5 ${tab === t ? "border-b-2 border-blue-400 text-blue-400" : "opacity-60"}`}
          >
            {t === "browse" ? "Browse" : t === "create" ? "Create new" : "Blank"}
          </button>
        ))}
      </div>
      {tab === "browse" && <PersonaBrowse onSelect={onSelect} />}
      {tab === "create" && <PersonaCreate onSelect={onSelect} />}
      {tab === "blank" && <PersonaBlank onSelect={onSelect} />}
    </div>
  );
}
