### Added
- On a taOSmobile handset the built-in taOS Agent now runs on PicoClaw instead of
  opencode, talking to the controller's own LLM gateway with a key scoped to its
  permitted models. Choose the harness with `taos_agent.framework` (auto, opencode
  or picoclaw) and `device.class`, or `PUT /api/taos-agent/framework` without a
  restart; switching back to opencode revokes the key.
- The taOS Agent settings and the lock screen show the harness that is actually
  running, and why: with the gateway off, PicoClaw is not used and the log says so.
- Under PicoClaw the taOS Agent keeps its reach into taOS (desktop control, notes,
  todos, projects, memory): a `bin/taos` helper in its workspace calls the taOS API
  with the same credential opencode uses, and is removed when you switch back.
