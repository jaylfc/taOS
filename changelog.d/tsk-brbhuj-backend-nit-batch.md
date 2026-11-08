### Fixed

- Anthropic gateway: whitespace-only image URL is now rejected as a 400 client error instead of being forwarded upstream as an empty image block.
- Provider add: models_source correctly reports "discovered" when an explicit empty models list triggers discovery and models are found (previously could report "none").