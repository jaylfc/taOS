### Fixed

- PATCH /api/agents/{slug}/persona and PATCH /api/agents/{slug}/memory no longer return the agent's llm_key (bearer credential) in the response body. The mutation handlers now use the same _public_agent() filter as read endpoints.