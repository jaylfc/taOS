### Fixed

- Anthropic gateway: whitespace-only image URL is now rejected as a 400 client error instead of being forwarded upstream as an empty image block.