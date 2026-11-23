### Fixed

- Agent import: read and write the memory limit key Hermes reads
  (memory_char_limit) in the nested `memory` section of profile config.yaml
  (NousResearch/hermes-agent @ v2026.8.31,
  tools/memory_tool.py:get_builtin_memory_config reads config["memory"]);
  a top-level memory_char_limit is ignored by Hermes. Create the mapping
  when absent. Also create the DM channel before sending the transfer notice
  so it is delivered as the first chat message, fail loudly if notice
  delivery fails, and share one DM-channel helper between the deploy and
  import paths.
