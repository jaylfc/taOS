### Fixed

- Agent import: write the memory limit key Hermes reads (memory_char_limit) at the top level of the profile config instead of memory.char_limit, create the DM channel before sending the transfer notice so it is delivered as the first chat message, fail loudly if notice delivery fails, share one DM-channel helper between the deploy and import paths, and read the source host from the bundle export manifest before the source_host form field.
