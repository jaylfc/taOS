### Added
- On a taOSmobile handset the built-in taOS Agent now runs on PicoClaw instead of
  opencode, talking to the controller's own LLM gateway with a key scoped to its
  permitted models. Choose the harness with `taos_agent.framework` (auto, opencode
  or picoclaw) and `device.class`, or `PUT /api/taos-agent/framework` without a
  restart; switching back to opencode revokes the key.
