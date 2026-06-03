import { type AgentModel } from "@/components/ModelPickerFlow";

/* ------------------------------------------------------------------ */
/*  Shared types                                                        */
/* ------------------------------------------------------------------ */

export interface Agent {
  name: string;
  display_name?: string;
  host: string;
  color: string;
  emoji?: string;
  status: "running" | "stopped" | "error" | "deploying";
  vectors: number;
  framework?: string;
  paused?: boolean;
  on_worker_failure?: "pause" | "fallback" | "escalate-immediately";
  fallback_models?: string[];
  kv_cache_quant_k?: string;
  kv_cache_quant_v?: string;
  kv_cache_quant_boundary_layers?: number;
  soul_md?: string;
  agent_md?: string;
  source_persona_id?: string | null;
  migrated_to_v2_personas?: boolean;
  framework_version_sha?: string | null;
}

export interface DiskState {
  used_gib: number;
  quota_gib: number;
  percent: number;
  state: "ok" | "warn" | "hard";
  last_checked_at: string;
}

export interface ArchivedAgent {
  id: string;
  archived_at: string;             // "YYYYMMDDTHHMMSS"
  archived_slug: string;
  archive_container: string;
  archive_dir: string;
  original: {
    name?: string;
    display_name?: string;
    color?: string;
    emoji?: string;
    model?: string;
    framework?: string;
  };
}

export interface Framework {
  id: string;
  name: string;
  description: string;
  verification_status: "beta" | "alpha" | "broken";
}

// AgentModel is defined and exported from ModelPickerFlow
export type Model = AgentModel;
