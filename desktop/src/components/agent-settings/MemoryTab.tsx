import { useEffect, useState } from "react";

interface MemoryTabProps {
  agent: { name: string; memory_plugin?: string };
  onUpdated: () => void;
}

interface LibrarianConfig {
  enabled?: boolean;
  tasks?: Record<string, boolean>;
  fanout?: { default?: string; auto_scale?: boolean };
}

interface MemoryStats {
  notes?: number;
  edges?: number;
  lastWrite?: string;
}

export function MemoryTab({ agent, onUpdated }: MemoryTabProps) {
  const [plugin, setPlugin] = useState<string>(agent.memory_plugin || "taosmd");
  const [librarian, setLibrarian] = useState<LibrarianConfig | null>(null);
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [advanced, setAdvanced] = useState(false);

  useEffect(() => {
    fetch(`/api/agents/${agent.name}/librarian`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setLibrarian)
      .catch(() => setLibrarian(null));

    fetch(`/api/memory/stats?agent=${agent.name}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) =>
        setStats(
          d ? { notes: d.notes, edges: d.edges, lastWrite: d.last_write } : null
        )
      )
      .catch(() => setStats(null));
  }, [agent.name]);

  const changePlugin = async (p: string) => {
    setPlugin(p);
    await fetch(`/api/agents/${agent.name}/memory`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ memory_plugin: p }),
    });
    onUpdated();
  };

  const patchLib = async (patch: Record<string, unknown>) => {
    const res = await fetch(`/api/agents/${agent.name}/librarian`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (res.ok) setLibrarian(await res.json());
  };

  return (
    <div className="flex flex-col gap-4 p-4 h-full overflow-auto">
      {/* Plugin selector */}
      <section>
        <div className="text-xs uppercase opacity-60 mb-1">Memory plugin</div>
        <div className="flex items-center gap-2">
          <select
            value={plugin}
            onChange={(e) => changePlugin(e.target.value)}
            className="border border-white/10 rounded bg-shell-bg px-2 py-1 text-sm text-shell-text-secondary focus:outline-none focus:ring-1 focus:ring-white/20"
            aria-label="Memory plugin"
          >
            <option value="taosmd">taOSmd (built-in)</option>
            <option value="none">None</option>
          </select>
          <a
            href="#store?category=memory"
            className="text-blue-400 text-sm hover:text-blue-300 transition-colors"
          >
            Get more plugins →
          </a>
        </div>
        {plugin === "taosmd" && (
          <p className="text-xs opacity-60 mt-2">
            Persistent memory: knowledge graph, archive, crystal store. Usage
            contract injected at top of every conversation.
          </p>
        )}
      </section>

      {/* Stats strip */}
      {plugin === "taosmd" && (
        <section className="grid grid-cols-3 gap-2">
          {(
            [
              { label: "Notes", value: stats?.notes ?? "—" },
              { label: "Graph edges", value: stats?.edges ?? "—" },
              { label: "Last write", value: stats?.lastWrite ?? "—" },
            ] as Array<{ label: string; value: string | number }>
          ).map((s) => (
            <div key={s.label} className="bg-blue-950/30 rounded p-2">
              <div className="text-lg font-semibold">{s.value}</div>
              <div className="text-[10px] uppercase opacity-60">{s.label}</div>
            </div>
          ))}
        </section>
      )}

      {/* Librarian controls */}
      {plugin === "taosmd" && librarian && (
        <section className="border-t border-white/5 pt-4 flex flex-col gap-3">
          <div className="text-xs uppercase opacity-60">Librarian</div>

          <label className="flex items-center justify-between text-sm">
            <span>Enable Librarian</span>
            <input
              type="checkbox"
              checked={!!librarian.enabled}
              onChange={(e) => patchLib({ enabled: e.target.checked })}
              aria-label="Enable Librarian"
            />
          </label>

          <button
            onClick={() => setAdvanced((a) => !a)}
            className="self-start text-sm text-blue-400 hover:text-blue-300 transition-colors"
          >
            {advanced ? "Hide advanced" : "Show advanced…"}
          </button>

          {advanced && (
            <div className="flex flex-col gap-2 pl-4 border-l border-white/10">
              {Object.entries(librarian.tasks || {}).map(([task, enabled]) => (
                <label
                  key={task}
                  className="flex items-center justify-between text-sm"
                >
                  <span>{task}</span>
                  <input
                    type="checkbox"
                    checked={!!enabled}
                    onChange={(e) =>
                      patchLib({ tasks: { [task]: e.target.checked } })
                    }
                    aria-label={`Task: ${task}`}
                  />
                </label>
              ))}

              <label className="flex items-center justify-between text-sm">
                <span>Fanout</span>
                <select
                  value={librarian.fanout?.default || "low"}
                  onChange={(e) => patchLib({ fanout: e.target.value })}
                  className="border border-white/10 rounded bg-shell-bg px-2 py-1 text-sm text-shell-text-secondary focus:outline-none focus:ring-1 focus:ring-white/20"
                  aria-label="Librarian fanout level"
                >
                  {["off", "low", "med", "high"].map((l) => (
                    <option key={l} value={l}>
                      {l}
                    </option>
                  ))}
                </select>
              </label>

              <label className="flex items-center justify-between text-sm">
                <span>Auto-scale</span>
                <input
                  type="checkbox"
                  checked={!!librarian.fanout?.auto_scale}
                  onChange={(e) =>
                    patchLib({ fanout_auto_scale: e.target.checked })
                  }
                  aria-label="Librarian auto-scale fanout"
                />
              </label>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
