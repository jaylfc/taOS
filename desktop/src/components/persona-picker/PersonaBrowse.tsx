import { useEffect, useState } from "react";
import { Search, Check } from "lucide-react";
import { fetchLibrary, fetchPersonaDetail } from "@/lib/personas-api";
import type { PersonaSource, PersonaSummary, PersonaSelection } from "./types";

const SOURCE_OPTIONS: { value: PersonaSource | ""; label: string }[] = [
  { value: "", label: "All sources" },
  { value: "builtin", label: "Built-in" },
  { value: "awesome-openclaw", label: "awesome-openclaw" },
  { value: "prompt-library", label: "prompt-library" },
  { value: "user", label: "My library" },
];

// Short human label for a source tag.
const SOURCE_LABEL: Record<string, string> = {
  builtin: "Built-in",
  "awesome-openclaw": "OpenClaw",
  "prompt-library": "Library",
  user: "Mine",
};

// Deterministic accent ring per persona so the avatar chips read as a set
// without introducing off-theme colors. Hue derived from the id.
function avatarHue(seed: string): number {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) % 360;
  return h;
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const first = parts[0] ?? "";
  if (parts.length === 0) return "?";
  if (parts.length === 1) return (first.slice(0, 2) || "?").toUpperCase();
  const last = parts[parts.length - 1] ?? "";
  return ((first[0] ?? "") + (last[0] ?? "")).toUpperCase() || "?";
}

export function PersonaBrowse({ onSelect }: { onSelect: (s: PersonaSelection) => void }) {
  const [source, setSource] = useState<PersonaSource | "">("");
  const [q, setQ] = useState("");
  const [personas, setPersonas] = useState<PersonaSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [selected, setSelected] = useState<PersonaSummary | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detail, setDetail] = useState<{
    soul_md: string;
    agent_md?: string;
    name: string;
    source: string;
    id: string;
  } | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchLibrary({ source: source || undefined, q: q || undefined })
      .then(setPersonas)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [source, q]);

  function handleSelect(persona: PersonaSummary) {
    setSelected(persona);
    setDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    fetchPersonaDetail(persona.source, persona.id)
      .then(setDetail)
      .catch((e) => setDetailError(String(e)))
      .finally(() => setDetailLoading(false));
  }

  function handleUse() {
    if (!detail) return;
    onSelect({
      kind: "library",
      source_persona_id: `${detail.source}:${detail.id}`,
      soul_md: detail.soul_md,
      agent_md: detail.agent_md ?? "",
    });
  }

  const inputClass =
    "h-9 w-full rounded-lg border border-white/10 bg-shell-bg-deep px-3 text-sm text-shell-text " +
    "placeholder:text-shell-text-tertiary focus-visible:outline-none focus-visible:border-accent/40 " +
    "focus-visible:ring-2 focus-visible:ring-accent/20 transition-colors";

  return (
    <div className="flex gap-3 min-h-0 h-[19rem]">
      {/* Left: search + list */}
      <div className="flex flex-col gap-2 w-60 shrink-0 min-h-0">
        <div className="relative">
          <Search
            size={14}
            className="absolute left-2.5 top-1/2 -translate-y-1/2 text-shell-text-tertiary pointer-events-none"
          />
          <input
            type="search"
            aria-label="Search personas"
            placeholder="Search personas"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className={inputClass + " pl-8"}
          />
        </div>
        <select
          aria-label="Filter by source"
          value={source}
          onChange={(e) => setSource(e.target.value as PersonaSource | "")}
          className={inputClass}
        >
          {SOURCE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value} className="bg-shell-surface">
              {o.label}
            </option>
          ))}
        </select>

        {error && (
          <div role="alert" className="rounded-lg border border-red-500/20 bg-red-500/10 px-2.5 py-1.5 text-xs text-red-300">
            {error}
          </div>
        )}

        <ul aria-label="Persona list" className="flex flex-col gap-1 overflow-y-auto pr-0.5 -mr-0.5 min-h-0">
          {loading && (
            <li className="py-6 text-center text-sm text-shell-text-tertiary">Loading...</li>
          )}
          {!loading && personas.length === 0 && !error && (
            <li className="py-6 text-center text-sm text-shell-text-tertiary">No personas found.</li>
          )}
          {personas.map((p) => {
            const active = selected?.id === p.id && selected?.source === p.source;
            const hue = avatarHue(`${p.source}:${p.id}`);
            return (
              <li key={`${p.source}:${p.id}`}>
                <button
                  onClick={() => handleSelect(p)}
                  aria-pressed={active}
                  className={`group w-full flex items-center gap-2.5 rounded-lg px-2 py-2 text-left transition-colors ${
                    active
                      ? "bg-accent/10 ring-1 ring-accent/40"
                      : "hover:bg-white/5"
                  }`}
                >
                  <span
                    className="shrink-0 grid place-items-center w-8 h-8 rounded-lg text-[11px] font-semibold text-white/90"
                    style={{
                      background: `linear-gradient(135deg, hsl(${hue} 55% 42%), hsl(${(hue + 40) % 360} 55% 32%))`,
                    }}
                    aria-hidden
                  >
                    {initials(p.name)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium text-shell-text truncate">{p.name}</span>
                    <span className="block text-[10px] uppercase tracking-wide text-shell-text-tertiary">
                      {SOURCE_LABEL[p.source] ?? p.source}
                    </span>
                  </span>
                  {active && <Check size={14} className="shrink-0 text-accent" />}
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      {/* Right: preview panel */}
      <div className="flex flex-1 flex-col min-w-0 rounded-lg border border-white/5 bg-shell-bg-deep/60 p-3">
        {!selected && (
          <div className="flex flex-1 flex-col items-center justify-center text-center gap-1.5 text-shell-text-tertiary">
            <div className="grid place-items-center w-10 h-10 rounded-xl border border-white/10 bg-white/5">
              <Search size={16} />
            </div>
            <p className="text-sm">Select a persona to preview it.</p>
          </div>
        )}

        {selected && (
          <>
            <div className="flex items-center gap-2.5 mb-2.5 shrink-0">
              <span
                className="shrink-0 grid place-items-center w-9 h-9 rounded-lg text-xs font-semibold text-white/90"
                style={{
                  background: `linear-gradient(135deg, hsl(${avatarHue(`${selected.source}:${selected.id}`)} 55% 42%), hsl(${(avatarHue(`${selected.source}:${selected.id}`) + 40) % 360} 55% 32%))`,
                }}
                aria-hidden
              >
                {initials(selected.name)}
              </span>
              <div className="min-w-0">
                <h3 className="text-sm font-semibold text-shell-text truncate">{selected.name}</h3>
                <span className="inline-block mt-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium bg-white/5 text-shell-text-secondary">
                  {SOURCE_LABEL[selected.source] ?? selected.source}
                </span>
              </div>
            </div>

            {detailError && (
              <div role="alert" className="rounded-lg border border-red-500/20 bg-red-500/10 px-2.5 py-1.5 text-xs text-red-300">
                {detailError}
              </div>
            )}

            {detailLoading && <p className="text-sm text-shell-text-tertiary">Loading...</p>}

            {detail && (
              <>
                <pre className="flex-1 min-h-0 overflow-y-auto whitespace-pre-wrap rounded-lg border border-white/5 bg-shell-bg p-2.5 text-xs leading-relaxed font-mono text-shell-text-secondary">
                  {detail.soul_md || "(no persona content)"}
                </pre>
                <button
                  onClick={handleUse}
                  className="mt-2.5 self-start inline-flex items-center gap-1.5 rounded-lg bg-accent px-3.5 py-2 text-sm font-medium text-white hover:bg-accent/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 transition-colors"
                >
                  <Check size={14} />
                  Use this persona
                </button>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
