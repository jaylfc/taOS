### Added

- The `/api/llm/v1/chat/completions` gateway now supports `stream: true`, proxying upstream SSE chunks verbatim (including `tool_calls` deltas) and closing the upstream connection when the client disconnects.
