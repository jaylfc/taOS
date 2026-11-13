### Fixed

- The taOS Agent's configured model and system prompt are now passed to Kilo: `ensure_taos_kilo_harness` accepts `model` and `system_prompt`, falls back to `DEFAULT_MODEL` for an empty model, and rebuilds the cached harness when the model or key changes; the Kilo CLI adapter prefixes the system prompt to the first user turn of a session (later turns resume with `-s` and send the user text only).
- A changed system prompt now starts a new Kilo session: the chat route saves the prompt alongside the session id and only resumes the session when the saved prompt matches the current one.
