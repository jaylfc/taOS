export type PersonaSource = "builtin" | "awesome-openclaw" | "prompt-library" | "user";

export interface PersonaSummary {
  source: PersonaSource;
  id: string;
  name: string;
  description?: string;
  preview: string;
}

export interface PersonaSelection {
  kind: "library" | "custom" | "blank";
  source_persona_id?: string;
  soul_md: string;
  agent_md: string;
  save_to_library?: { name: string; description?: string };
}
