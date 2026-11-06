### Fixed

- **LLM gateway: images to Anthropic models**: chat messages carrying an image no longer fail with a 400 on the Anthropic route. OpenAI `image_url` parts are converted to Anthropic image blocks (`base64` source for data URLs, `url` source otherwise) (#4198).
