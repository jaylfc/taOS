# Model Settings Reference

## Overview

taOS provides a flexible model settings system that allows administrators and users to configure sampling parameters and other model behaviors at four precedence levels:

1. **Model defaults** (lowest) -- admin-editable per-model baselines
2. **Agent settings** -- per-agent overrides, scoped to the agent's gateway identity
3. **Chat/session settings** -- per-channel overrides, applied when the caller is a channel member or has an active turn
4. **Request values** (highest) -- explicit values sent in the API request

Each setting can be stored in `default` mode (used when no higher level provides a value) or `forced` mode (overrides all lower levels including explicit request values).

## Supported Settings

| Setting | Type | Description |
|---------|------|-------------|
| `temperature` | float | Sampling temperature (0.0-2.0 for most providers) |
| `top_p` | float | Nucleus sampling threshold (0.0-1.0) |
| `top_k` | int | Top-k sampling (1-100, not supported by OpenAI) |
| `min_p` | float | Minimum probability threshold (0.0-1.0, Ollama only) |
| `max_output_tokens` | int | Maximum tokens to generate |
| `reasoning_effort` | enum | Reasoning effort level: low, medium, high, off (model-dependent) |
| `repetition_penalty` | float | Repetition penalty (Ollama only) |
| `presence_penalty` | float | Presence penalty (Ollama only) |
| `seed` | int | Random seed for deterministic sampling |
| `stop_sequences` | list[str] | Sequences that stop generation |
| `system_prompt_addition` | string | Text appended to the system message |

## Precedence Resolution

For each setting, the resolver evaluates:

1. **Forced entries** (highest level first): chat forced > agent forced
2. **Request explicit value**: if present in the request body
3. **Default entries** (highest level first): chat default > agent default > model default
4. **Unset**: no value sent to upstream

The resolution result includes:
- `value`: the resolved value (or None)
- `source`: "model", "agent", "chat", "request", or None
- `forced`: boolean indicating if a forced entry won
- `dropped_reason`: why a value was not used (if applicable)

## Capability Filtering

Before sending settings to an upstream provider, the gateway filters them based on the target provider/model capabilities:

- Settings not supported by the provider are dropped (e.g., `top_k` for OpenAI)
- Values outside the provider's allowed range are rejected with an error
- Dropped settings are reported in the `x-taos-settings-dropped` response header
- Each fallback attempt re-filters for the new route's capabilities

### Provider Capabilities

| Setting | OpenAI | Anthropic | Ollama |
|---------|--------|-----------|--------|
| temperature | 0.0-2.0 | 0.0-1.0 | 0.0-2.0 |
| top_p | 0.0-1.0 | 0.0-1.0 | 0.0-1.0 |
| top_k | ❌ | 1-100 | 1-100 |
| min_p | ❌ | ❌ | 0.0-1.0 |
| max_output_tokens | 1-32768 | 1-8192 | 1-32768 |
| reasoning_effort | o1 models | Claude 3.5/3.7 | some models |
| repetition_penalty | ❌ | ❌ | 0.0-2.0 |
| presence_penalty | ❌ | ❌ | -2.0 to 2.0 |
| seed | 0-2147483647 | ❌ | 0-2147483647 |
| stop_sequences | ✅ | ✅ | ✅ |
| system_prompt_addition | ✅ | ✅ | ✅ |

## Chat-Level Settings

Chat-level settings are applied when the gateway can identify the chat channel:

1. **Explicit header**: `x-taos-chat-id` header is checked; caller must be a member of that channel
2. **Turn registry**: When the chat hub delivers a message to an agent, it records the turn. If exactly one turn is active for that agent, the channel is used automatically

If multiple concurrent turns exist for an agent, chat-level settings are not applied (falls back to agent level with a note).

## Audit Trail

All setting changes are recorded in `model_settings_events` with:
- Timestamp, actor, level, key, setting
- Old value, new value, mode, via (api/cli)
- Emitted to the system event bus for real-time notifications

## Permissions

- **Model defaults**: admin only
- **Agent settings**: admin or agent owner (via agent registry)
- **Chat settings**: admin or channel member
- **Agents**: never allowed to write settings (consent flow is a separate feature)

## API

Settings are managed via the HTTP API (see API documentation). The gateway applies settings automatically on every chat completion request.

## Configuration

Model defaults can be set by administrators. Capability tables are initialized with sensible defaults and can be extended per-model.