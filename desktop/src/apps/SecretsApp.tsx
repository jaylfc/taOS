import { useState, useEffect, useCallback } from "react";
import { KeyRound, Plus, Eye, EyeOff, Trash2, Edit, X, Filter } from "lucide-react";
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  Label,
} from "@/components/ui";
import { GitHubConnect } from "./secrets/GitHubConnect";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface Secret {
  name: string;
  category: string;
  value: string;       // masked unless revealed
  description: string;
  agents: string[];
  revealed?: boolean;
}

type CategoryFilter = "all" | "api-key" | "credential" | "token" | "config";

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const CATEGORY_STYLES: Record<string, string> = {
  "api-key": "bg-sky-500/20 text-sky-400",
  credential: "bg-violet-500/20 text-violet-400",
  token: "bg-amber-500/20 text-amber-400",
  config: "bg-emerald-500/20 text-emerald-400",
};

const MASKED = "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022";

/* ------------------------------------------------------------------ */
/*  AddEditDialog                                                      */
/* ------------------------------------------------------------------ */

function AddEditDialog({
  initial,
  onSave,
  onClose,
}: {
  initial: Partial<Secret> | null;
  onSave: (secret: Secret) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [value, setValue] = useState(initial?.value === MASKED ? "" : initial?.value ?? "");
  const [category, setCategory] = useState(initial?.category ?? "api-key");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [agentsStr, setAgentsStr] = useState(initial?.agents?.join(", ") ?? "");

  const isEdit = !!initial?.name;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    onSave({
      name: name.trim(),
      category,
      value: value || MASKED,
      description: description.trim(),
      agents: agentsStr
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
    });
  };

  return (
    <div
      className="absolute inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={isEdit ? "Edit secret" : "Add secret"}
    >
      <Card
        className="w-full max-w-md max-h-full flex flex-col shadow-2xl overflow-hidden bg-shell-surface"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <CardHeader className="flex flex-row items-center justify-between border-b border-white/5 px-5 py-4 shrink-0">
          <div className="flex items-center gap-2">
            <KeyRound size={16} className="text-accent" />
            <CardTitle className="text-sm font-semibold">{isEdit ? "Edit Secret" : "Add Secret"}</CardTitle>
          </div>
          <Button
            variant="ghost"
            size="icon"
            onClick={onClose}
            aria-label="Close dialog"
            className="h-7 w-7"
          >
            <X size={16} />
          </Button>
        </CardHeader>

        <form onSubmit={handleSubmit}>
          <CardContent className="px-5 py-4 space-y-3.5 flex-1 overflow-y-auto">
            <div className="space-y-1.5">
              <Label htmlFor="secret-name">Name</Label>
              <Input
                id="secret-name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="OPENAI_API_KEY"
                disabled={isEdit}
                autoFocus={!isEdit}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="secret-value">Value</Label>
              <Input
                id="secret-value"
                type="password"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder={isEdit ? "Leave blank to keep existing" : "sk-..."}
                className="font-mono"
                autoFocus={isEdit}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="secret-category">Category</Label>
              <select
                id="secret-category"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                className="flex h-9 w-full rounded-lg border border-white/10 bg-shell-bg-deep px-3 py-1 text-sm text-shell-text focus-visible:outline-none focus-visible:border-accent/40 focus-visible:ring-2 focus-visible:ring-accent/20"
              >
                <option value="api-key">API Key</option>
                <option value="credential">Credential</option>
                <option value="token">Token</option>
                <option value="config">Config</option>
              </select>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="secret-description">Description</Label>
              <Input
                id="secret-description"
                type="text"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="What is this secret used for?"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="secret-agents">Agent Access (comma-separated)</Label>
              <Input
                id="secret-agents"
                type="text"
                value={agentsStr}
                onChange={(e) => setAgentsStr(e.target.value)}
                placeholder="research-agent, code-reviewer"
              />
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <Button
                type="button"
                variant="secondary"
                onClick={onClose}
              >
                Cancel
              </Button>
              <Button type="submit">
                {isEdit ? "Update" : "Add"}
              </Button>
            </div>
          </CardContent>
        </form>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  SecretsApp (main)                                                  */
/* ------------------------------------------------------------------ */

export function SecretsApp({ windowId: _windowId }: { windowId: string }) {
  const [secrets, setSecrets] = useState<Secret[]>([]);
  const [loading, setLoading] = useState(true);
  const [categoryFilter, setCategoryFilter] = useState<CategoryFilter>("all");
  const [dialog, setDialog] = useState<{ open: boolean; editing: Secret | null }>({
    open: false,
    editing: null,
  });

  const fetchSecrets = useCallback(async () => {
    // 10s timeout via AbortController so a hung backend never leaves the
    // panel stuck on "Loading..." forever. On timeout/abort we fall through
    // to the empty state.
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10_000);
    try {
      const res = await fetch("/api/secrets", {
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      if (res.ok) {
        const ct = res.headers.get("content-type") ?? "";
        if (ct.includes("application/json")) {
          const data = await res.json();
          if (Array.isArray(data)) {
            setSecrets(
              data.map((s: Record<string, unknown>) => ({
                name: String(s.name ?? ""),
                category: String(s.category ?? "config"),
                value: MASKED,
                description: String(s.description ?? ""),
                agents: Array.isArray(s.agents) ? s.agents.map(String) : [],
              })),
            );
            setLoading(false);
            return;
          }
        }
      }
    } catch { /* fall through */ }
    finally {
      clearTimeout(timer);
    }
    // Empty state — no mock secrets for security reasons
    setSecrets([]);
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchSecrets();
  }, [fetchSecrets]);

  const handleReveal = async (name: string) => {
    const secret = secrets.find((s) => s.name === name);
    if (!secret) return;

    if (secret.revealed) {
      setSecrets((prev) =>
        prev.map((s) => (s.name === name ? { ...s, value: MASKED, revealed: false } : s)),
      );
      return;
    }

    try {
      const res = await fetch(`/api/secrets/${encodeURIComponent(name)}`, {
        headers: { Accept: "application/json" },
      });
      if (res.ok) {
        const ct = res.headers.get("content-type") ?? "";
        if (ct.includes("application/json")) {
          const data = await res.json();
          if (data.value) {
            setSecrets((prev) =>
              prev.map((s) =>
                s.name === name ? { ...s, value: String(data.value), revealed: true } : s,
              ),
            );
            return;
          }
        }
      }
    } catch { /* ignore */ }
    // If the API doesn't work, just show a placeholder
    setSecrets((prev) =>
      prev.map((s) =>
        s.name === name ? { ...s, value: "[value not available]", revealed: true } : s,
      ),
    );
  };

  const handleDelete = (name: string) => {
    setSecrets((prev) => prev.filter((s) => s.name !== name));
  };

  const handleSave = (secret: Secret) => {
    if (dialog.editing) {
      setSecrets((prev) =>
        prev.map((s) =>
          s.name === dialog.editing!.name
            ? { ...secret, value: secret.value || s.value, revealed: false }
            : s,
        ),
      );
    } else {
      setSecrets((prev) => [...prev, { ...secret, revealed: false }]);
    }
    setDialog({ open: false, editing: null });
  };

  const filtered = secrets.filter((s) => {
    if (categoryFilter !== "all" && s.category !== categoryFilter) return false;
    return true;
  });

  return (
    <div className="flex flex-col h-full bg-shell-bg text-shell-text select-none relative">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-white/5">
        <div className="flex items-center gap-2">
          <KeyRound size={18} className="text-accent" />
          <h1 className="text-sm font-semibold">Secrets</h1>
          <span className="text-xs text-shell-text-tertiary">
            {secrets.length} stored
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Filter size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-shell-text-tertiary pointer-events-none" />
            <select
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value as CategoryFilter)}
              className="pl-8 pr-3 py-1.5 rounded-lg bg-shell-bg-deep text-sm text-shell-text border border-white/5 focus:outline-none focus:ring-1 focus:ring-accent appearance-none cursor-pointer"
              aria-label="Filter by category"
            >
              <option value="all">All Categories</option>
              <option value="api-key">API Key</option>
              <option value="credential">Credential</option>
              <option value="token">Token</option>
              <option value="config">Config</option>
            </select>
          </div>
          <Button
            size="sm"
            onClick={() => setDialog({ open: true, editing: null })}
            aria-label="Add new secret"
          >
            <Plus size={14} />
            Add Secret
          </Button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto">
        <div className="p-4">
          <GitHubConnect />
        </div>
        {loading ? (
          <div className="flex items-center justify-center h-full text-shell-text-tertiary text-sm">
            Loading secrets...
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-shell-text-tertiary">
            <KeyRound size={40} className="opacity-30" />
            <p className="text-sm">
              {secrets.length === 0 ? "No secrets stored" : "No secrets match this filter"}
            </p>
            {secrets.length === 0 && (
              <Button
                size="sm"
                onClick={() => setDialog({ open: true, editing: null })}
                className="mt-1"
              >
                <Plus size={13} />
                Add your first secret
              </Button>
            )}
          </div>
        ) : (
          <div className="overflow-x-auto -mx-4 md:mx-0">
            <table className="w-full text-left min-w-[720px]" aria-label="Secrets table">
            <thead>
              <tr className="border-b border-white/5 text-[11px] uppercase tracking-wider text-shell-text-tertiary">
                <th className="px-4 py-2.5 font-medium">Name</th>
                <th className="px-4 py-2.5 font-medium">Category</th>
                <th className="px-4 py-2.5 font-medium">Value</th>
                <th className="px-4 py-2.5 font-medium">Description</th>
                <th className="px-4 py-2.5 font-medium">Agent Access</th>
                <th className="px-4 py-2.5 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((secret) => (
                <tr
                  key={secret.name}
                  className="border-b border-white/5 hover:bg-shell-surface/50 transition-colors"
                >
                  <td className="px-4 py-3">
                    <span className="font-medium text-sm font-mono">{secret.name}</span>
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-medium ${
                        CATEGORY_STYLES[secret.category] ?? "bg-white/5 text-shell-text-tertiary"
                      }`}
                    >
                      {secret.category}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1.5">
                      <span className="text-sm font-mono text-shell-text-secondary max-w-[180px] truncate">
                        {secret.value}
                      </span>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => handleReveal(secret.name)}
                        className="h-7 w-7"
                        aria-label={secret.revealed ? `Hide ${secret.name}` : `Reveal ${secret.name}`}
                        title={secret.revealed ? "Hide" : "Reveal"}
                      >
                        {secret.revealed ? <EyeOff size={13} /> : <Eye size={13} />}
                      </Button>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-sm text-shell-text-secondary max-w-[200px] truncate">
                    {secret.description || <span className="text-shell-text-tertiary">--</span>}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {secret.agents.length === 0 ? (
                        <span className="text-xs text-shell-text-tertiary">None</span>
                      ) : (
                        secret.agents.map((agent) => (
                          <span
                            key={agent}
                            className="px-1.5 py-0.5 rounded bg-white/5 text-[10px] text-shell-text-secondary"
                          >
                            {agent}
                          </span>
                        ))
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => setDialog({ open: true, editing: secret })}
                        className="h-7 w-7"
                        aria-label={`Edit ${secret.name}`}
                        title="Edit"
                      >
                        <Edit size={14} />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => handleDelete(secret.name)}
                        className="h-7 w-7 hover:text-red-400 hover:bg-red-500/15"
                        aria-label={`Delete ${secret.name}`}
                        title="Delete"
                      >
                        <Trash2 size={14} />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Add/Edit dialog */}
      {dialog.open && (
        <AddEditDialog
          initial={dialog.editing}
          onSave={handleSave}
          onClose={() => setDialog({ open: false, editing: null })}
        />
      )}
    </div>
  );
}
