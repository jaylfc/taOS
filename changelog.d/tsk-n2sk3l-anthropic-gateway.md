### Added

- Implemented Anthropic gateway translator in `tinyagentos/llm_gateway/anthropic.py` to support Anthropic provider models (Phase 3 of LiteLLM replacement). The translator converts OpenAI chat requests to Anthropic Messages API and back, including support for:
  - System message placement
  - Tool calls and tool_choice mapping
  - Streaming with text deltas and streamed tool arguments
  - Default max_tokens handling
  - Proper error handling and API key redaction