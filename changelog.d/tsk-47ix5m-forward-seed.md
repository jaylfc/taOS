### Fixed
- The `seed` parameter for `generate_image` is now forwarded from the skill-exec runtime to the image generator and is advertised in the agent-facing tool schema, so reusing a returned seed to iterate on a liked image actually holds the seed instead of silently producing a fresh random one (#tsk-47ix5m).
