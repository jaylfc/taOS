### Added

- New `lmstudio` backend type: workers can now advertise LM Studio as a
  local OpenAI-compatible backend. LM Studio serves its API on port 1234;
  the type is registered in the catalog with `llm-chat` and `embedding`
  capabilities, maps to the `openai` LiteLLM prefix, and is given an
  `api_base` so litellm_config routes to the LAN URL instead of cloud
  OpenAI. A dedicated adapter health-checks `/v1/models`.
  A bare `http://host:1234` or the `/v1` form LM Studio displays both work:
  the health probe does not double the version prefix, and embedding
  discovery and embedding routes use `/v1`.
