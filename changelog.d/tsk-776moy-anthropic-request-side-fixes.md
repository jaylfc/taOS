### Fixed
- LLM gateway Anthropic backend now sends the configured upstream model id and `stream: true` on streaming requests.
- OpenAI tools and `tool_choice` are converted to Anthropic `input_schema` and `{type:tool,name}` shapes.
- OpenAI `tool` result messages and assistant `tool_calls` are converted to Anthropic `tool_result` and `tool_use` content blocks.
- Every non-2xx upstream status becomes a proper OpenAI-style error (429 keeps `Retry-After`), never `None` and never a silent empty stream.
- Mapped extra documented stop_reasons: `refusal` -> `content_filter`, `model_context_window_exceeded` -> `length`.
