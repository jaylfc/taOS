import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import type { ClusterResource, ClusterResources } from "@/lib/cluster";

// Read-only Resources tab (agent offices S10): one flat table over
// GET /api/cluster/resources with client-side filters. Deliberately no
// start/stop/move affordances here, those are later slices.

const ALL = "__all__";
/** Select value standing in for a null project, so those rows stay filterable. */
const NO_PROJECT = "__none__";

const STATE_PILL_CLASS: Record<string, string> = {
  running: "bg-emerald-500/15 text-emerald-300 border-emerald-500/25",
  stopped: "bg-white/[0.03] border-white/10 text-shell-text-tertiary",
  frozen: "bg-sky-500/15 text-sky-200 border-sky-500/25",
  unknown: "bg-white/[0.03] border-white/10 text-shell-text-tertiary",
};

const SELECT_CLASS =
  "h-8 rounded-md border border-white/10 bg-shell-bg-deep px-2 text-xs text-shell-text focus-visible:outline-none focus-visible:border-accent/40 focus-visible:ring-2 focus-visible:ring-accent/20 transition-colors";

function formatCpu(cores: number | null): string {
  return cores == null ? "--" : `${cores} core${cores === 1 ? "" : "s"}`;
}

function formatRam(mb: number | null): string {
  if (mb == null) return "--";
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`;
}

function uniqueSorted(values: string[]): string[] {
  return Array.from(new Set(values)).sort((a, b) => a.localeCompare(b));
}

function FilterSelect({
  id,
  label,
  value,
  options,
  onChange,
  renderOption = (v) => v,
}: {
  id: string;
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
  renderOption?: (v: string) => string;
}) {
  return (
    <div className="flex items-center gap-1">
      <label htmlFor={id} className="text-[11px] text-shell-text-tertiary">
        {label}
      </label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={SELECT_CLASS}>
        <option value={ALL}>All</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {renderOption(o)}
          </option>
        ))}
      </select>
    </div>
  );
}

/** Body of the Cluster app Resources tab. */
export function ClusterResourcesPanel({
  data,
  loading,
  error,
}: {
  data: ClusterResources | null;
  loading: boolean;
  error?: string | null;
}) {
  const [typeFilter, setTypeFilter] = useState(ALL);
  const [workerFilter, setWorkerFilter] = useState(ALL);
  const [projectFilter, setProjectFilter] = useState(ALL);
  const [stateFilter, setStateFilter] = useState(ALL);
  const [search, setSearch] = useState("");

  const resources = useMemo(() => data?.resources ?? [], [data]);
  const sourceErrors = data?.errors ?? [];

  const options = useMemo(
    () => ({
      type: uniqueSorted(resources.map((r) => r.type)),
      worker: uniqueSorted(resources.map((r) => r.worker)),
      project: uniqueSorted(resources.map((r) => r.project ?? NO_PROJECT)),
      state: uniqueSorted(resources.map((r) => r.state)),
    }),
    [resources],
  );

  // A refresh can drop the option a filter is currently pinned to. When that
  // happens, the selected value is no longer among the derived options, so the
  // filter would silently match nothing. Reset any such filter to All so the
  // remaining rows stay visible. Runs whenever the options change.
  useEffect(() => {
    const check = (value: string, opts: string[]) =>
      value !== ALL && !opts.includes(value) ? ALL : value;
    setTypeFilter((v) => check(v, options.type));
    setWorkerFilter((v) => check(v, options.worker));
    setProjectFilter((v) => check(v, options.project));
    setStateFilter((v) => check(v, options.state));
  }, [options]);

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return resources.filter((r: ClusterResource) => {
      if (typeFilter !== ALL && r.type !== typeFilter) return false;
      if (workerFilter !== ALL && r.worker !== workerFilter) return false;
      if (projectFilter !== ALL && (r.project ?? NO_PROJECT) !== projectFilter) return false;
      if (stateFilter !== ALL && r.state !== stateFilter) return false;
      if (q && !r.name.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [resources, typeFilter, workerFilter, projectFilter, stateFilter, search]);

  if (error) {
    return (
      <div className="p-3">
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/25 bg-amber-500/10 px-2.5 py-2 text-[11px] text-amber-200">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>{error}</span>
        </div>
      </div>
    );
  }
  if (loading && !data) {
    return (
      <div className="flex items-center justify-center gap-2 py-6 text-[11px] text-shell-text-tertiary">
        <Loader2 size={12} className="animate-spin" aria-hidden="true" />
        Loading resources...
      </div>
    );
  }

  return (
    <div className="p-3 space-y-3">
      {sourceErrors.length > 0 && (
        <div
          role="status"
          className="flex items-start gap-2 rounded-lg border border-amber-500/25 bg-amber-500/10 px-2.5 py-2 text-[11px] text-amber-200"
        >
          <AlertTriangle size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>Some sources could not be read: {sourceErrors.join(", ")}</span>
        </div>
      )}

      {resources.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-6 text-center">
          <p className="text-[11px] text-shell-text-tertiary">No resources reported yet.</p>
          <p className="text-[10px] text-shell-text-tertiary leading-relaxed px-2">
            Agents, backends and apps running anywhere in the cluster show up here.
          </p>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filter resources">
            <FilterSelect id="cluster-resources-type" label="Type" value={typeFilter} options={options.type} onChange={setTypeFilter} />
            <FilterSelect id="cluster-resources-worker" label="Worker" value={workerFilter} options={options.worker} onChange={setWorkerFilter} />
            <FilterSelect
              id="cluster-resources-project"
              label="Project"
              value={projectFilter}
              options={options.project}
              onChange={setProjectFilter}
              renderOption={(v) => (v === NO_PROJECT ? "(none)" : v)}
            />
            <FilterSelect id="cluster-resources-state" label="State" value={stateFilter} options={options.state} onChange={setStateFilter} />
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by name"
              aria-label="Search resources by name"
              className={`${SELECT_CLASS} min-w-[10rem] flex-1`}
            />
          </div>

          <div className="overflow-x-auto rounded-lg border border-white/5">
            <table className="w-full text-left text-[11px]">
              <caption className="sr-only">
                Cluster resources, {rows.length} of {resources.length} shown
              </caption>
              <thead className="bg-white/[0.02] text-shell-text-tertiary">
                <tr>
                  {["Name", "Type", "Worker", "Project", "State", "CPU", "RAM", "Warnings"].map((h) => (
                    <th key={h} scope="col" className="px-2 py-1.5 font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-2 py-4 text-center text-shell-text-tertiary italic">
                      No resources match these filters.
                    </td>
                  </tr>
                ) : (
                  rows.map((r) => (
                    <tr key={r.id} className="border-t border-white/5">
                      <td className="px-2 py-1.5 text-shell-text font-medium">{r.name}</td>
                      <td className="px-2 py-1.5 text-shell-text-secondary">{r.type}</td>
                      <td className="px-2 py-1.5 text-shell-text-secondary">{r.worker}</td>
                      <td className="px-2 py-1.5 text-shell-text-secondary">{r.project ?? "--"}</td>
                      <td className="px-2 py-1.5">
                        <span
                          className={`text-[9px] px-1.5 py-0.5 rounded-full border font-medium ${
                            STATE_PILL_CLASS[r.state] ?? STATE_PILL_CLASS.unknown
                          }`}
                        >
                          {r.state}
                        </span>
                      </td>
                      <td className="px-2 py-1.5 text-shell-text-secondary">{formatCpu(r.cpu_cores)}</td>
                      <td className="px-2 py-1.5 text-shell-text-secondary">{formatRam(r.memory_mb)}</td>
                      <td className="px-2 py-1.5">
                        <div className="flex flex-wrap gap-1">
                          {r.warnings.map((w) => (
                            <span
                              key={w}
                              className="text-[9px] px-1.5 py-0.5 rounded-full border border-amber-500/25 bg-amber-500/10 text-amber-200"
                              aria-label={`Warning: ${w}`}
                            >
                              {w}
                            </span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
