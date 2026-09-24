### Added

- New `tinyagentos/llm_usage` package: token usage and estimated cost tracking that does not depend
  on LiteLLM, the first piece of replacing it. It normalises usage across the OpenAI, Anthropic and
  Ollama formats (streamed or not, with cached and reasoning tokens counted correctly), and prices
  calls from a vendored, MIT-licensed price table pinned to an upstream commit. Local backends cost
  $0; a cloud model with no known price is reported as unpriced rather than free, so budgets can
  never silently stop counting it. Not yet wired in: the LiteLLM path is unchanged.
