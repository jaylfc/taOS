/* ------------------------------------------------------------------ */
/*  Import Agent: pure client-side helpers                             */
/*                                                                     */
/*  Bundle file validation and FormData payload assembly for the       */
/*  Import wizard. Kept framework-free so they are unit-testable        */
/*  (mirrors MessagesApp.a2aSelection.ts).                             */
/* ------------------------------------------------------------------ */

/** Accepted bundle extensions produced by `hermes profile export`. */
export const ACCEPTED_BUNDLE_EXTENSIONS = [".zip", ".tar.gz", ".tgz"] as const;

/** Client-side size cap. The real limit is enforced by the backend later. */
export const MAX_BUNDLE_BYTES = 200 * 1024 * 1024; // 200 MB

export interface BundleValidation {
  ok: boolean;
  error: string | null;
}

/** A single secret env-var row entered by the user. */
export interface SecretRow {
  key: string;
  value: string;
}

/**
 * Validate a chosen bundle file: must be non-empty, within the size cap, and
 * carry an accepted extension. Returns the first failure, or ok.
 */
export function validateBundleFile(file: File | null): BundleValidation {
  if (!file) return { ok: false, error: "Select a profile bundle to import." };
  if (file.size === 0) return { ok: false, error: "The selected file is empty." };
  if (file.size > MAX_BUNDLE_BYTES) {
    return { ok: false, error: `Bundle is too large (max ${MAX_BUNDLE_BYTES / (1024 * 1024)} MB).` };
  }
  const lower = file.name.toLowerCase();
  const hasExt = ACCEPTED_BUNDLE_EXTENSIONS.some((ext) => lower.endsWith(ext));
  if (!hasExt) {
    return { ok: false, error: `Unsupported file type. Use ${ACCEPTED_BUNDLE_EXTENSIONS.join(", ")}.` };
  }
  return { ok: true, error: null };
}

/** Human-readable file size, e.g. "12.3 MB". */
export function formatBundleSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Collapse secret rows into a `{ KEY: value }` object. Blank keys are dropped
 * and trimmed; on duplicate keys the last non-empty value wins.
 */
export function secretsRowsToObject(rows: SecretRow[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (!key) continue;
    out[key] = row.value;
  }
  return out;
}

export interface ImportPayloadInput {
  framework: string;
  bundle: File;
  name: string;
  model: string;
  secrets: SecretRow[];
}

/**
 * Build the multipart FormData POSTed to /api/agents/import. Secrets are
 * serialized as a JSON string under the `secrets` field. The agent name is
 * trimmed; an empty model is omitted.
 */
export function buildImportFormData(input: ImportPayloadInput): FormData {
  const fd = new FormData();
  fd.append("framework", input.framework);
  fd.append("bundle", input.bundle);
  fd.append("name", input.name.trim());
  if (input.model.trim()) fd.append("model", input.model.trim());
  fd.append("secrets", JSON.stringify(secretsRowsToObject(input.secrets)));
  return fd;
}
