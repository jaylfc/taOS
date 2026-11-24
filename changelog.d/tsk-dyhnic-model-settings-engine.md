### Added
- Per-agent, per-chat, and per-model sampling settings (temperature, top_p, top_k, min_p, max_output_tokens, reasoning_effort, repetition_penalty, presence_penalty, seed, stop_sequences, system_prompt_addition) with four-level precedence: model default < agent < chat < request
- Forced mode for settings that overrides request values and lower-level defaults
- Capability filtering per provider/model (OpenAI, Anthropic, Ollama families) with value ranges, unsupported settings dropped with x-taos-settings-dropped header
- Chat-level settings via x-taos-chat-id header (validated against channel membership) and turn registry (auto-detected from chat delivery)
- Audit trail for all setting changes with actor, old/new values, mode, and source
- Write permissions: admins and owners only (agents cannot write settings)
