import type { PersonaSelection } from "./types";

export function PersonaBlank({ onSelect }: { onSelect: (s: PersonaSelection) => void }) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[320px] gap-3 text-center">
      <p className="opacity-70 max-w-sm text-sm">
        Deploy with no persona. You can add one later from the Agent Settings &rarr; Persona tab.
      </p>
      <button
        onClick={() => onSelect({ kind: "blank", soul_md: "", agent_md: "" })}
        className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium hover:bg-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-400"
      >
        Deploy with no persona →
      </button>
    </div>
  );
}
