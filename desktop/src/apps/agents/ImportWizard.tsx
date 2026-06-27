import { useState, useEffect, useRef } from "react";
import { Upload, X, ChevronRight, ChevronLeft, Check, Plus, FileArchive, Copy, ExternalLink } from "lucide-react";
import { Button, Card, Input, Label } from "@/components/ui";
import { slugifyClient, isValidSlug, SLUG_REGEX } from "@/lib/slug";
import {
  validateBundleFile,
  buildImportFormData,
  formatBundleSize,
  ACCEPTED_BUNDLE_EXTENSIONS,
  type SecretRow,
} from "./importBundle";

/* ------------------------------------------------------------------ */
/*  ImportWizard                                                        */
/*                                                                     */
/*  Mirrors DeployWizard's shell, step chrome and token usage. UI +    */
/*  client-side only: the final Import POSTs a multipart bundle to the  */
/*  placeholder /api/agents/import endpoint (backend is a later slice). */
/* ------------------------------------------------------------------ */

const FILE_ACCEPT = ACCEPTED_BUNDLE_EXTENSIONS.join(",");

// Command the user runs in their existing Hermes CLI to produce the bundle.
const HERMES_EXPORT_CMD = "hermes profile export <agent-name>";
const HERMES_DOCS_URL =
  "https://hermes-agent.nousresearch.com/docs/user-guide/profile-distributions";

export function ImportWizard({
  open,
  onClose,
}: {
  open: boolean;
  onClose: (imported?: boolean) => void;
}) {
  const [step, setStep] = useState(0);

  // Step 1: Bundle file
  const [bundle, setBundle] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [cmdCopied, setCmdCopied] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Step 2: Details
  const [name, setName] = useState("");
  const [customSlug, setCustomSlug] = useState<string | null>(null);
  const [editingSlug, setEditingSlug] = useState(false);
  const [model, setModel] = useState("");
  const [secrets, setSecrets] = useState<SecretRow[]>([]);

  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);

  // Reset when opened
  useEffect(() => {
    if (open) {
      setStep(0);
      setBundle(null);
      setDragging(false);
      setCmdCopied(false);
      setName("");
      setCustomSlug(null);
      setEditingSlug(false);
      setModel("");
      setSecrets([]);
      setImporting(false);
      setImportError(null);
    }
  }, [open]);

  if (!open) return null;

  const STEPS = ["Framework", "Bundle", "Details", "Review"];
  const totalSteps = STEPS.length;
  const reviewStep = totalSteps - 1;

  const bundleCheck = validateBundleFile(bundle);

  const canNext = () => {
    if (step === 0) return true; // Hermes locked in
    if (step === 1) return bundleCheck.ok;
    if (step === 2) {
      if (name.trim().length === 0) return false;
      if (customSlug !== null && !isValidSlug(customSlug)) return false;
      return true;
    }
    return true;
  };

  function handleFiles(files: FileList | null) {
    const f = files && files[0] ? files[0] : null;
    if (f) setBundle(f);
  }

  async function handleCopyCmd() {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(HERMES_EXPORT_CMD);
        setCmdCopied(true);
        setTimeout(() => setCmdCopied(false), 2000);
      }
    } catch { /* clipboard unavailable: the command stays visible to copy by hand */ }
  }

  function updateSecret(i: number, patch: Partial<SecretRow>) {
    setSecrets((prev) => prev.map((row, j) => (j === i ? { ...row, ...patch } : row)));
  }

  async function handleImport() {
    const check = validateBundleFile(bundle);
    if (!check.ok || !bundle) {
      setImportError(check.error ?? "Select a valid bundle.");
      return;
    }
    setImporting(true);
    setImportError(null);
    try {
      const fd = buildImportFormData({
        framework: "hermes",
        bundle,
        name: customSlug || name.trim(),
        model,
        secrets,
      });
      const res = await fetch("/api/agents/import", {
        method: "POST",
        credentials: "include",
        body: fd,
      });
      if (!res.ok) {
        let msg = `Import failed (${res.status})`;
        try {
          const err = await res.json();
          if (err?.error) msg = String(err.error);
        } catch { /* non-JSON error body: keep status message */ }
        setImportError(msg);
        setImporting(false);
        return;
      }
      onClose(true);
    } catch (e) {
      setImportError(e instanceof Error ? e.message : "Network error");
      setImporting(false);
    }
  }

  return (
    <div
      className="absolute inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      style={{
        paddingTop: "calc(1rem + env(safe-area-inset-top, 0px))",
        paddingBottom: "calc(1rem + env(safe-area-inset-bottom, 0px))",
      }}
      onClick={() => onClose()}
      role="dialog"
      aria-modal="true"
      aria-label="Import Agent"
    >
      <div
        className="w-full max-w-lg max-h-full min-h-0 bg-shell-surface rounded-xl border border-white/10 shadow-2xl overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/5 shrink-0">
          <div className="flex items-center gap-2">
            <Upload size={16} className="text-accent" />
            <h2 className="text-sm font-semibold">Import Agent</h2>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={() => onClose()}
            aria-label="Close wizard"
          >
            <X size={16} />
          </Button>
        </div>

        {/* Step indicators */}
        <div className="flex items-center gap-1 px-5 py-3 border-b border-white/5 shrink-0 overflow-x-auto">
          {STEPS.map((label, i) => (
            <div key={label} className="flex items-center gap-1">
              <div
                className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-medium transition-colors ${
                  i < step
                    ? "bg-accent/20 text-accent"
                    : i === step
                      ? "bg-accent text-white"
                      : "bg-white/5 text-shell-text-tertiary"
                }`}
              >
                {i < step ? <Check size={12} /> : i + 1}
              </div>
              <span
                className={`text-[11px] hidden sm:inline ${
                  i === step ? "text-shell-text" : "text-shell-text-tertiary"
                }`}
              >
                {label}
              </span>
              {i < STEPS.length - 1 && <div className="w-4 h-px bg-white/10 mx-0.5" />}
            </div>
          ))}
        </div>

        {/* Body */}
        <div className="px-5 py-5 flex-1 min-h-0 overflow-y-auto">
          {/* Step 0: Framework (Hermes locked) */}
          {step === 0 && (
            <div className="space-y-2">
              <span className="block text-xs text-shell-text-secondary mb-2">Source Framework</span>
              <div className="w-full text-left px-4 py-3 rounded-lg border border-accent bg-accent/10">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">Hermes</span>
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-500/20 text-amber-400 leading-none">
                    Beta
                  </span>
                </div>
                <div className="text-xs mt-0.5 text-shell-text-secondary">
                  Import a profile bundle exported with <code>hermes profile export</code>.
                </div>
              </div>
              <p className="text-xs text-shell-text-tertiary mt-3">
                More frameworks are coming. For now, only Hermes bundles can be imported.
              </p>
            </div>
          )}

          {/* Step 1: Bundle upload */}
          {step === 1 && (
            <div className="space-y-3">
              <span className="block text-xs text-shell-text-secondary mb-2">Profile Bundle</span>

              {/* How to get your bundle */}
              <div className="rounded-lg border border-white/10 bg-shell-bg-deep px-4 py-3 space-y-2.5">
                <div className="text-xs font-medium text-shell-text">How to get your bundle</div>
                <p className="text-xs text-shell-text-secondary leading-relaxed">
                  taOS imports a Hermes profile export bundle. In your existing Hermes CLI, run the
                  command below to produce a portable archive (.tar.gz / .zip), then upload it here.
                </p>
                <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-md border border-white/10 bg-shell-surface">
                  <code className="flex-1 min-w-0 text-xs font-mono text-shell-text truncate">
                    {HERMES_EXPORT_CMD}
                  </code>
                  <button
                    type="button"
                    onClick={handleCopyCmd}
                    className="flex items-center gap-1 text-xs text-shell-text-tertiary hover:text-shell-text transition-colors shrink-0"
                    aria-label="Copy export command"
                  >
                    {cmdCopied ? <Check size={13} /> : <Copy size={13} />}
                    {cmdCopied ? "Copied" : "Copy"}
                  </button>
                </div>
                <p className="text-xs text-shell-text-tertiary leading-relaxed">
                  Your API keys are not in the bundle by design, so you'll add those in the Secrets step.
                </p>
                <a
                  href={HERMES_DOCS_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-blue-400 hover:underline"
                >
                  Learn more
                  <ExternalLink size={12} />
                </a>
              </div>

              <div
                role="button"
                tabIndex={0}
                aria-label="Choose a profile bundle, or drop a file here"
                onClick={() => fileInputRef.current?.click()}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    fileInputRef.current?.click();
                  }
                }}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragging(false);
                  handleFiles(e.dataTransfer.files);
                }}
                className={`flex flex-col items-center justify-center gap-2 px-4 py-8 rounded-lg border border-dashed text-center cursor-pointer transition-colors ${
                  dragging
                    ? "border-accent bg-accent/10"
                    : "border-white/15 bg-shell-bg-deep hover:bg-white/5"
                }`}
              >
                <Upload size={22} className="text-shell-text-tertiary" />
                <div className="text-sm text-shell-text">
                  Drop your bundle here, or <span className="text-blue-400">browse</span>
                </div>
                <div className="text-xs text-shell-text-tertiary">
                  {ACCEPTED_BUNDLE_EXTENSIONS.join(", ")} · up to 200 MB
                </div>
              </div>
              <input
                ref={fileInputRef}
                type="file"
                accept={FILE_ACCEPT}
                className="sr-only"
                aria-hidden="true"
                tabIndex={-1}
                onChange={(e) => handleFiles(e.target.files)}
              />

              {bundle && (
                <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg border border-accent/30 bg-accent/5">
                  <FileArchive size={16} className="text-accent shrink-0" />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium truncate">{bundle.name}</div>
                    <div className="text-xs text-shell-text-tertiary">{formatBundleSize(bundle.size)}</div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setBundle(null)}
                    className="text-shell-text-tertiary hover:text-red-400 transition-colors shrink-0"
                    aria-label="Remove selected bundle"
                  >
                    <X size={14} />
                  </button>
                </div>
              )}

              {bundle && !bundleCheck.ok && bundleCheck.error && (
                <p className="text-xs text-red-400" role="alert">{bundleCheck.error}</p>
              )}

              <p className="text-xs text-shell-text-tertiary">
                Secrets are not included in the bundle by design, so you'll supply them in the next step.
              </p>
            </div>
          )}

          {/* Step 2: Details */}
          {step === 2 && (
            <Card className="p-0 border-0 bg-transparent shadow-none space-y-4">
              <div>
                <Label htmlFor="import-agent-name" className="mb-1.5 block">
                  Agent Name
                </Label>
                <Input
                  id="import-agent-name"
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="my-agent"
                  autoFocus
                />
                {(() => {
                  const derivedSlug = slugifyClient(name);
                  const slug = customSlug ?? derivedSlug;
                  const slugInvalid = customSlug !== null && !isValidSlug(customSlug);
                  return (
                    <>
                      <div className="text-xs opacity-60 mt-1">
                        Slug: <code>{slug || "—"}</code>{" "}
                        <button
                          type="button"
                          onClick={() => {
                            setCustomSlug(customSlug ?? derivedSlug);
                            setEditingSlug(true);
                          }}
                          className="text-blue-400 hover:underline"
                        >
                          edit
                        </button>
                      </div>
                      {editingSlug && (
                        <Input
                          value={customSlug ?? derivedSlug}
                          onChange={(e) => setCustomSlug(e.target.value)}
                          onBlur={() => setEditingSlug(false)}
                          className="mt-1 text-sm"
                          aria-label="Edit slug"
                          pattern={SLUG_REGEX.source}
                        />
                      )}
                      {slugInvalid && (
                        <p className="mt-1 text-xs text-red-400">
                          Slug must match <code>[a-z0-9][a-z0-9-]&#123;0,62&#125;</code>
                        </p>
                      )}
                    </>
                  );
                })()}
              </div>

              <div>
                <Label htmlFor="import-agent-model" className="mb-1.5 block">
                  Model
                  <span className="ml-1.5 font-normal text-shell-text-tertiary">(optional)</span>
                </Label>
                <Input
                  id="import-agent-model"
                  type="text"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder="Leave empty to keep the bundle's model"
                  aria-describedby="import-agent-model-desc"
                />
                <p id="import-agent-model-desc" className="mt-1 text-xs text-shell-text-tertiary">
                  Override the chat model, or leave blank to use the one recorded in the bundle.
                </p>
              </div>

              <div>
                <Label className="mb-1.5 block">
                  Secrets
                  <span className="ml-1.5 font-normal text-shell-text-tertiary">
                    (env vars required by the bundle)
                  </span>
                </Label>
                <div className="space-y-1.5">
                  {secrets.map((row, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <Input
                        value={row.key}
                        onChange={(e) => updateSecret(i, { key: e.target.value })}
                        placeholder="ENV_VAR_NAME"
                        className="flex-1 text-sm font-mono"
                        aria-label={`Secret name ${i + 1}`}
                      />
                      <Input
                        value={row.value}
                        onChange={(e) => updateSecret(i, { value: e.target.value })}
                        placeholder="value"
                        type="password"
                        className="flex-1 text-sm"
                        aria-label={`Secret value ${i + 1}`}
                      />
                      <button
                        type="button"
                        onClick={() => setSecrets((prev) => prev.filter((_, j) => j !== i))}
                        className="text-shell-text-tertiary hover:text-red-400 transition-colors shrink-0"
                        aria-label={`Remove secret ${i + 1}`}
                      >
                        <X size={14} />
                      </button>
                    </div>
                  ))}
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setSecrets((prev) => [...prev, { key: "", value: "" }])}
                    className="w-full"
                  >
                    <Plus size={13} />
                    Add secret
                  </Button>
                </div>
              </div>
            </Card>
          )}

          {/* Step 3: Review */}
          {step === 3 && (
            <div className="space-y-3">
              <span className="block text-xs text-shell-text-secondary mb-2">Review Import</span>
              <div className="rounded-lg bg-shell-bg-deep border border-white/5 divide-y divide-white/5">
                {[
                  ["Framework", "Hermes"],
                  ["Bundle", bundle ? `${bundle.name} (${formatBundleSize(bundle.size)})` : "—"],
                  ["Name", customSlug || name.trim() || "—"],
                  ["Model", model.trim() || "From bundle"],
                  ["Secrets", `${secrets.filter((r) => r.key.trim()).length} provided`],
                ].map(([label, value]) => (
                  <div key={label} className="flex items-center justify-between px-4 py-2.5 gap-3">
                    <span className="text-xs text-shell-text-secondary shrink-0">{label}</span>
                    <span className="text-sm font-medium truncate text-right">{value}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Error */}
        {importError && (
          <div className="mx-5 mb-3 px-3 py-2 rounded-lg bg-red-500/15 border border-red-500/30 text-xs text-red-300 flex items-start justify-between gap-2">
            <span role="alert" className="min-w-0">{importError}</span>
            <button
              type="button"
              onClick={() => setImportError(null)}
              className="text-red-300/70 hover:text-red-200 shrink-0"
              aria-label="Dismiss error"
            >
              <X size={13} />
            </button>
          </div>
        )}

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-3 border-t border-white/5 shrink-0">
          <Button
            variant="outline"
            size="sm"
            onClick={() => (step === 0 ? onClose() : setStep(step - 1))}
          >
            <ChevronLeft size={14} />
            {step === 0 ? "Cancel" : "Back"}
          </Button>

          {step < reviewStep ? (
            <Button size="sm" onClick={() => setStep(step + 1)} disabled={!canNext()}>
              Next
              <ChevronRight size={14} />
            </Button>
          ) : (
            <Button
              size="sm"
              onClick={handleImport}
              disabled={importing}
              className="bg-emerald-600 hover:bg-emerald-500 text-white"
            >
              <Upload size={13} />
              {importing ? "Importing..." : "Import Agent"}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
