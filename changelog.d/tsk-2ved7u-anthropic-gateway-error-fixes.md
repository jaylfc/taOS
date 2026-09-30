### Fixed
- Anthropic gateway rate-limit errors now preserve `Retry-After` in response headers and use code `rate_limit_exceeded`.
- Streaming Anthropic errors now return their real HTTP status and headers instead of HTTP 200 with an in-band error.
- `tool_choice: "none"` maps to `{type:"none"}` per the Messages API docs; tools are still sent.
- Tool `input_schema` defaults to `{type:"object", properties:{}}` when no parameters are provided.
